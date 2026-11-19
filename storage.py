"""MongoDB Atlas saqlash: suhbat tarixi, inbox, sozlamalar, istisnolar va bio holati."""
import asyncio
import logging
import sys
import time

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import ConfigurationError, DuplicateKeyError, OperationFailure, PyMongoError

from config import HISTORY_LIMIT, MONGODB_DB, MONGODB_URI

log = logging.getLogger("storage")

_client: AsyncMongoClient | None = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        if not MONGODB_URI:
            raise ConfigurationError("MONGODB_URI to'ldirilmagan")
        # Noto'g'ri formatdagi URI ham ConfigurationError beradi (xabarida URI chiqarilmaydi)
        _client = AsyncMongoClient(MONGODB_URI, serverSelectionTimeoutMS=10_000,
                                   appname="anna_assistant")
        _db = _client[MONGODB_DB]
    return _db


async def init(retry_forever: bool = True) -> bool:
    """Ulanishni tekshiradi va indekslarni yaratadi.

    Login/parol xato bo'lsa dastur to'xtaydi; tarmoq xatosida qayta uriniladi
    (retry_forever=False bo'lsa False qaytaradi). URI hech qachon logga chiqmaydi.
    """
    try:
        db = _get_db()
    except ConfigurationError:
        if not retry_forever:
            return False
        sys.exit("[storage] MONGODB_URI bo'sh yoki noto'g'ri formatda (.env ni tekshiring)")
    delay = 5
    while True:
        try:
            await db.command("ping")
            await db.history.create_index([("chat_id", ASCENDING), ("_id", DESCENDING)])
            await db.history.create_index([("role", ASCENDING), ("ts", DESCENDING)])
            await db.transactions.create_index([("day", ASCENDING), ("ts", ASCENDING)])
            await db.transactions.create_index("batch")
            await db.archive.create_index([("chat_id", ASCENDING), ("msg_id", ASCENDING)], unique=True)
            await db.archive.create_index([("msg_id", ASCENDING), ("channel", ASCENDING)])
            await db.archive_chats.create_index([("last", DESCENDING)])
            await db.archive_chats.create_index([("drive_dirty", ASCENDING), ("changed", ASCENDING)])
            await db.archive.create_index([("drive_media", ASCENDING), ("date", ASCENDING)], sparse=True)
            log.info("MongoDB'ga ulandi (baza: %s)", MONGODB_DB)
            return True
        except OperationFailure as e:
            if e.code in (18, 8000):  # AuthenticationFailed / Atlas auth xatosi
                sys.exit("[storage] MongoDB login yoki paroli noto'g'ri (MONGODB_URI ni tekshiring)")
            log.warning("MongoDB xatosi: %s (kod %s)", type(e).__name__, e.code)
        except PyMongoError as e:
            log.warning("MongoDB'ga ulanib bo'lmadi: %s", type(e).__name__)
        if not retry_forever:
            return False
        log.info("%d soniyadan keyin qayta urinaman...", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ---------- bitta nusxa qulfi ----------
async def acquire_lock(owner: str, ttl: float) -> bool:
    """Qulfni oladi yoki (o'ziniki bo'lsa) yangilaydi. Boshqa tirik nusxa ushlab turgan bo'lsa — False."""
    now = time.time()
    try:
        await _get_db().locks.update_one(
            {"_id": "main", "$or": [{"owner": owner}, {"ts": {"$lt": now - ttl}}]},
            {"$set": {"owner": owner, "ts": now}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False  # hujjat bor, lekin shartga tushmadi: qulf boshqa nusxada va hali eskirmagan


async def release_lock(owner: str) -> None:
    await _get_db().locks.delete_one({"_id": "main", "owner": owner})


# ---------- tarix ----------
async def add_history(chat_id: int, role: str, content: str) -> None:
    await _get_db().history.insert_one(
        {"chat_id": chat_id, "role": role, "content": content, "ts": time.time()}
    )


async def get_history(chat_id: int, limit: int = HISTORY_LIMIT) -> list[dict]:
    cursor = _get_db().history.find({"chat_id": chat_id}).sort("_id", DESCENDING).limit(limit)
    rows = await cursor.to_list()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------- inbox ----------
async def upsert_inbox(chat_id, name, username, summary, urgent, last_text) -> None:
    # Pipeline update: ko'rilmagan xabar shoshilinch bo'lsa, belgisi saqlanib qoladi
    await _get_db().inbox.update_one(
        {"_id": chat_id},
        [{"$set": {
            "chat_id": chat_id,
            "name": name,
            "username": username,
            "summary": summary,
            "last_text": last_text,
            "updated": time.time(),
            "urgent": {"$cond": [
                {"$eq": [{"$ifNull": ["$handled", True]}, True]},
                bool(urgent),
                {"$or": [{"$ifNull": ["$urgent", False]}, bool(urgent)]},
            ]},
            "handled": False,
        }}],
        upsert=True,
    )


async def mark_handled(chat_id: int) -> None:
    await _get_db().inbox.update_one({"_id": chat_id}, {"$set": {"handled": True, "urgent": False}})


async def pending_inbox() -> list[dict]:
    cursor = _get_db().inbox.find({"handled": False}).sort([("urgent", DESCENDING),
                                                             ("updated", DESCENDING)])
    return await cursor.to_list()


async def today_count() -> int:
    return await _get_db().history.count_documents(
        {"role": "user", "ts": {"$gte": time.time() - 24 * 3600}}
    )


# ---------- sozlamalar ----------
MODES = ("on", "off", "auto")


async def get_mode() -> str:
    """on — doim javob beradi, off — hech qachon, auto — faqat band bo'lganda."""
    doc = await _get_db().settings.find_one({"_id": "mode"})
    if doc and doc.get("value") in MODES:
        return doc["value"]
    legacy = await _get_db().settings.find_one({"_id": "enabled"})  # eski /on /off sozlamasi
    if legacy is not None:
        return "on" if legacy.get("value") else "off"
    return "on"


async def set_mode(mode: str) -> None:
    await _get_db().settings.update_one({"_id": "mode"}, {"$set": {"value": mode}}, upsert=True)


async def is_enabled() -> bool:
    return await get_mode() != "off"


# ---------- egasining faolligi ----------
async def set_owner_active(ts: float) -> None:
    await _get_db().settings.update_one({"_id": "owner_active"}, {"$set": {"value": ts}}, upsert=True)


async def get_owner_active() -> float:
    doc = await _get_db().settings.find_one({"_id": "owner_active"})
    return float(doc["value"]) if doc else 0.0


# ---------- kontakt turlari (oila / mijoz / do'st) ----------
async def get_tag(user_id: int) -> str | None:
    doc = await _get_db().contacts.find_one({"_id": user_id})
    return doc.get("tag") if doc else None


async def set_tag(user_id: int, tag: str, label: str) -> None:
    await _get_db().contacts.update_one(
        {"_id": user_id}, {"$set": {"user_id": user_id, "tag": tag, "label": label}}, upsert=True
    )


async def remove_tag(user_id: int) -> bool:
    res = await _get_db().contacts.delete_one({"_id": user_id})
    return res.deleted_count > 0


async def list_tags() -> list[dict]:
    return await _get_db().contacts.find().sort("tag", ASCENDING).to_list()


# ---------- istisnolar ----------
async def is_ignored(user_id: int) -> bool:
    return await _get_db().ignored.find_one({"_id": user_id}) is not None


async def add_ignored(user_id: int, label: str) -> None:
    await _get_db().ignored.update_one(
        {"_id": user_id}, {"$set": {"user_id": user_id, "label": label}}, upsert=True
    )


async def remove_ignored(user_id: int) -> bool:
    res = await _get_db().ignored.delete_one({"_id": user_id})
    return res.deleted_count > 0


async def list_ignored() -> list[dict]:
    return await _get_db().ignored.find().to_list()


# ---------- vaqtincha jim (egasi o'zi gaplashayotganda) ----------
async def mute(chat_id: int, seconds: float) -> None:
    await _get_db().muted.update_one(
        {"_id": chat_id}, {"$set": {"until": time.time() + seconds}}, upsert=True
    )


async def unmute(chat_id: int) -> None:
    await _get_db().muted.delete_one({"_id": chat_id})


async def is_muted(chat_id: int) -> bool:
    doc = await _get_db().muted.find_one({"_id": chat_id})
    return doc is not None and doc.get("until", 0) > time.time()


# ---------- avtomatik bio holati ----------
async def get_bio_state() -> dict:
    return await _get_db().bio_state.find_one({"_id": "bio"}) or {}


async def save_bio_state(**fields) -> None:
    await _get_db().bio_state.update_one({"_id": "bio"}, {"$set": fields}, upsert=True)


async def save_original_bio_once(about: str, saved_at: float) -> bool:
    """Asl bio'ni faqat birinchi marta yozadi; mavjud zaxirani hech qachon ustidan yozmaydi."""
    res = await _get_db().bio_state.update_one(
        {"_id": "bio", "original_bio": {"$exists": False}},
        {"$set": {"original_bio": about, "original_saved_at": saved_at}},
        upsert=False,
    )
    if res.matched_count:
        return True
    if await _get_db().bio_state.find_one({"_id": "bio"}) is None:
        await _get_db().bio_state.insert_one(
            {"_id": "bio", "original_bio": about, "original_saved_at": saved_at}
        )
        return True
    return False


# ---------- kunlik statistika (hisobot va xarajat nazorati uchun) ----------
def today_key() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from config import TIMEZONE
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


async def stat_inc(chat_id: int | None = None, day: str | None = None, **counters: int) -> None:
    """Bugungi hisoblagichlarni oshiradi. chat_id berilsa — "yozgan odamlar" ro'yxatiga qo'shiladi."""
    update: dict = {}
    if counters:
        update["$inc"] = counters
    if chat_id is not None:
        update["$addToSet"] = {"chats": chat_id}
    if update:
        await _get_db().stats.update_one({"_id": day or today_key()}, update, upsert=True)


async def get_stats(day: str | None = None) -> dict:
    return await _get_db().stats.find_one({"_id": day or today_key()}) or {}


# ---------- dasturdan o'zgartiriladigan sozlamalar (/bio, /quote, /online) ----------
async def get_runtime() -> dict:
    doc = await _get_db().settings.find_one({"_id": "runtime"}) or {}
    doc.pop("_id", None)
    return doc


async def set_runtime(**values) -> None:
    await _get_db().settings.update_one({"_id": "runtime"}, {"$set": values}, upsert=True)


# ---------- shaxsiy buxgalter: kirim-chiqim yozuvlari ----------
def _tx_out(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


async def add_transactions(docs: list[dict]) -> tuple[str, list[str]]:
    """Yozuvlarni bitta "paket" qilib qo'shadi. (paket id, yozuv id lari) — bekor qilish uchun."""
    from bson import ObjectId
    batch = str(ObjectId())
    docs = [{**d, "batch": batch} for d in docs]
    res = await _get_db().transactions.insert_many(docs)
    return batch, [str(i) for i in res.inserted_ids]


async def list_transactions(start_day: str | None = None, end_day: str | None = None) -> list[dict]:
    """Kunlar oralig'idagi yozuvlar (YYYY-MM-DD, ikkala chegara ham kiradi). Chegarasiz — hammasi."""
    query: dict = {}
    if start_day or end_day:
        query["day"] = {}
        if start_day:
            query["day"]["$gte"] = start_day
        if end_day:
            query["day"]["$lte"] = end_day
    rows = await _get_db().transactions.find(query).sort([("day", ASCENDING), ("ts", ASCENDING)]).to_list()
    return [_tx_out(r) for r in rows]


async def last_transactions(n: int = 15) -> list[dict]:
    rows = await _get_db().transactions.find().sort("_id", DESCENDING).limit(n).to_list()
    return [_tx_out(r) for r in rows]


async def get_transaction(tx_id: str) -> dict | None:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        doc = await _get_db().transactions.find_one({"_id": ObjectId(tx_id)})
    except InvalidId:
        return None
    return _tx_out(doc) if doc else None


async def delete_transaction(tx_id: str) -> bool:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        res = await _get_db().transactions.delete_one({"_id": ObjectId(tx_id)})
    except InvalidId:
        return False
    return res.deleted_count > 0


async def delete_batch(batch: str) -> int:
    res = await _get_db().transactions.delete_many({"batch": batch})
    return res.deleted_count


async def last_batch() -> str | None:
    doc = await _get_db().transactions.find_one(sort=[("_id", DESCENDING)])
    return doc.get("batch") if doc else None


async def update_transaction(tx_id: str, **fields) -> bool:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        res = await _get_db().transactions.update_one({"_id": ObjectId(tx_id)}, {"$set": fields})
    except InvalidId:
        return False
    return res.matched_count > 0


async def get_budget() -> dict:
    """Oylik byudjet: {kategoriya yoki "jami": limit}"""
    doc = await _get_db().settings.find_one({"_id": "budget"}) or {}
    return dict(doc.get("limits") or {})


async def set_budget(category: str, limit: int | None) -> None:
    if limit:
        await _get_db().settings.update_one({"_id": "budget"}, {"$set": {f"limits.{category}": limit}},
                                            upsert=True)
    else:
        await _get_db().settings.update_one({"_id": "budget"}, {"$unset": {f"limits.{category}": ""}})


# ---------- chat arxivi (o'chirilgan yozishmalarni tiklash uchun) ----------
_ARCHIVE_FIELDS = {"_id": 0}


async def archive_add(doc: dict) -> bool:
    """Xabarni arxivga qo'shadi (chat_id + msg_id bo'yicha bir marta). Yangi bo'lsa True."""
    res = await _get_db().archive.update_one(
        {"chat_id": doc["chat_id"], "msg_id": doc["msg_id"]}, {"$setOnInsert": doc}, upsert=True)
    return res.upserted_id is not None


async def archive_add_many(docs: list[dict]) -> int:
    """Ko'p xabarni bir yo'la qo'shadi (bor bo'lganlari o'zgarmaydi). Yangi qo'shilganlar soni."""
    from pymongo import UpdateOne
    if not docs:
        return 0
    ops = [UpdateOne({"chat_id": d["chat_id"], "msg_id": d["msg_id"]}, {"$setOnInsert": d}, upsert=True)
           for d in docs]
    res = await _get_db().archive.bulk_write(ops, ordered=False)
    return res.upserted_count


async def archive_edit(chat_id: int, msg_id: int, text: str, ts: float) -> dict | None:
    """Tahrirlangan xabar: eski matn "edits" ga o'tadi. Arxivda bo'lmasa None."""
    db = _get_db()
    old = await db.archive.find_one({"chat_id": chat_id, "msg_id": msg_id}, _ARCHIVE_FIELDS)
    if old is None or (old.get("text") or "") == (text or ""):
        return old
    await db.archive.update_one(
        {"chat_id": chat_id, "msg_id": msg_id},
        {"$set": {"text": text, "edited_at": ts},
         "$push": {"edits": {"text": old.get("text") or "", "ts": old.get("edited_at") or old.get("date")}}})
    await db.archive_chats.update_one({"_id": chat_id}, {"$set": {"changed": ts, "drive_dirty": True}})
    return old


async def archive_mark_deleted(chat_id: int | None, msg_ids: list[int], ts: float) -> list[dict]:
    """O'chirilgan deb belgilaydi va shu xabarlarni qaytaradi. chat_id=None — shaxsiy chat yoki oddiy
    guruh (Telegram qaysi chatligini aytmaydi, lekin u yerda xabar id lari akkaunt bo'yicha yagona)."""
    db = _get_db()
    query: dict = {"msg_id": {"$in": list(msg_ids)}, "deleted": {"$ne": True}}
    if chat_id is None:
        query["channel"] = False
    else:
        query["chat_id"] = chat_id
    docs = await db.archive.find(query, _ARCHIVE_FIELDS).sort("date", ASCENDING).to_list()
    if docs:
        await db.archive.update_many(query, {"$set": {"deleted": True, "deleted_at": ts}})
        per_chat: dict[int, int] = {}
        for d in docs:
            per_chat[d["chat_id"]] = per_chat.get(d["chat_id"], 0) + 1
        for cid, n in per_chat.items():
            await db.archive_chats.update_one({"_id": cid}, {"$inc": {"deleted": n},
                                                             "$set": {"last_deleted": ts, "changed": ts,
                                                                      "drive_dirty": True}})
    for d in docs:
        d["deleted"], d["deleted_at"] = True, ts
    return docs


async def archive_touch_chat(chat_id: int, title: str, username: str | None, kind: str, ts: float,
                             added: int = 1) -> None:
    fields = {"title": title, "username": username, "kind": kind}
    if added:  # yangi xabar bor — Drive'dagi nusxa yangilanishi kerak
        fields.update(changed=time.time(), drive_dirty=True)
    await _get_db().archive_chats.update_one(
        {"_id": chat_id}, {"$set": fields, "$max": {"last": ts}, "$inc": {"count": added}}, upsert=True)


async def archive_drive_pending(limit: int = 50) -> list[dict]:
    """Drive'ga yuklanishi kerak bo'lgan (o'zgargan) chatlar."""
    rows = await _get_db().archive_chats.find({"drive_dirty": True}).sort("changed", ASCENDING).limit(limit).to_list()
    for r in rows:
        r["chat_id"] = r.pop("_id")
    return rows


async def archive_drive_done(chat_id: int, changed, file_id: str, link: str) -> None:
    """Yuklandi. Yuklash paytida chat yana o'zgargan bo'lsa ("changed" boshqa) — belgi qoladi."""
    db = _get_db()
    await db.archive_chats.update_one({"_id": chat_id}, {"$set": {"drive_file_id": file_id, "drive_link": link,
                                                                  "drive_synced": time.time()}})
    await db.archive_chats.update_one({"_id": chat_id, "changed": changed}, {"$set": {"drive_dirty": False}})


async def archive_media_pending(limit: int = 20) -> list[dict]:
    """Drive'ga yuklanishi kutilayotgan media (eskisidan boshlab)."""
    return await _get_db().archive.find({"drive_media": "pending"}, _ARCHIVE_FIELDS) \
        .sort("date", ASCENDING).limit(limit).to_list()


async def archive_msg_set(chat_id: int, msg_id: int, **fields) -> None:
    await _get_db().archive.update_one({"chat_id": chat_id, "msg_id": msg_id}, {"$set": fields})


async def archive_media_done(chat_id: int, msg_id: int, file_id: str, link: str) -> None:
    """Media Drive'ga yuklandi — chat HTML'i ham (havola bilan) yangilansin."""
    db = _get_db()
    await db.archive.update_one({"chat_id": chat_id, "msg_id": msg_id},
                                {"$set": {"drive_media": "done", "drive_media_id": file_id,
                                          "drive_media_link": link}})
    await db.archive_chats.update_one({"_id": chat_id}, {"$set": {"changed": time.time(), "drive_dirty": True}})


async def archive_chat_set(chat_id: int, **fields) -> None:
    await _get_db().archive_chats.update_one({"_id": chat_id}, {"$set": fields})


async def archive_media_counts() -> dict:
    cursor = await _get_db().archive.aggregate([   # AsyncCollection.aggregate — korutina
        {"$match": {"drive_media": {"$exists": True}}},
        {"$group": {"_id": "$drive_media", "n": {"$sum": 1}}}])
    rows = await cursor.to_list()
    return {r["_id"]: r["n"] for r in rows}


async def archive_drive_mark_all(only_missing: bool = False) -> int:
    """Barcha chatlarni (only_missing — Drive'da hali fayli yo'qlarini) yuklashga belgilaydi."""
    query: dict = {"count": {"$gt": 0}}
    if only_missing:
        query["drive_file_id"] = {"$exists": False}
    res = await _get_db().archive_chats.update_many(query, {"$set": {"drive_dirty": True}})
    return res.modified_count


async def archive_chat(chat_id: int) -> dict | None:
    doc = await _get_db().archive_chats.find_one({"_id": chat_id})
    if doc:
        doc["chat_id"] = doc.pop("_id")
    return doc


async def archive_chats(skip: int = 0, limit: int = 10, only_deleted: bool = False,
                        name: str = "") -> list[dict]:
    import re
    query: dict = {}
    if only_deleted:
        query["deleted"] = {"$gt": 0}
    if name:
        rx = {"$regex": re.escape(name.lstrip("@")), "$options": "i"}
        query["$or"] = [{"title": rx}, {"username": rx}]
    sort = "last_deleted" if only_deleted else "last"
    rows = await _get_db().archive_chats.find(query).sort(sort, DESCENDING).skip(skip).limit(limit).to_list()
    for r in rows:
        r["chat_id"] = r.pop("_id")
    return rows


async def archive_messages(chat_id: int, only_deleted: bool = False, limit: int = 0) -> list[dict]:
    query: dict = {"chat_id": chat_id}
    if only_deleted:
        query["deleted"] = True
    cursor = _get_db().archive.find(query, _ARCHIVE_FIELDS).sort("date", DESCENDING)
    if limit:
        cursor = cursor.limit(limit)
    return list(reversed(await cursor.to_list()))


async def archive_last_msg_id(chat_id: int) -> int:
    """Shu chatdan arxivdagi eng oxirgi xabar id si (yo'q bo'lsa 0) — keyingi nusxalash shundan davom etadi."""
    doc = await _get_db().archive.find_one({"chat_id": chat_id}, {"msg_id": 1}, sort=[("msg_id", DESCENDING)])
    return int(doc["msg_id"]) if doc else 0


async def archive_media_put(data: bytes, mime: str) -> str:
    from bson import Binary
    res = await _get_db().archive_media.insert_one({"data": Binary(data), "mime": mime, "ts": time.time()})
    return str(res.inserted_id)


async def archive_media_get(ids: list[str]) -> dict[str, tuple[bytes, str]]:
    from bson import ObjectId
    from bson.errors import InvalidId
    oids = []
    for i in ids:
        try:
            oids.append(ObjectId(i))
        except (InvalidId, TypeError):
            pass
    if not oids:
        return {}
    rows = await _get_db().archive_media.find({"_id": {"$in": oids}}).to_list()
    return {str(r["_id"]): (bytes(r["data"]), r.get("mime") or "") for r in rows}


async def archive_stats() -> dict:
    db = _get_db()
    return {
        "messages": await db.archive.estimated_document_count(),
        "deleted": await db.archive.count_documents({"deleted": True}),
        "chats": await db.archive_chats.estimated_document_count(),
    }


async def ping() -> None:
    await _get_db().command("ping")
