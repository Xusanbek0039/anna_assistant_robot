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
        except OperationFailure as e:
            if e.code in (18, 8000):  # AuthenticationFailed / Atlas auth xatosi
                sys.exit("[storage] MongoDB login yoki paroli noto'g'ri (MONGODB_URI ni tekshiring)")
            log.warning("MongoDB xatosi: %s (kod %s)", type(e).__name__, e.code)
        except PyMongoError as e:
            log.warning("MongoDB'ga ulanib bo'lmadi: %s. Atlas -> Network Access da shu server IP'si "
                        "(yoki 0.0.0.0/0) ruxsat etilganini tekshiring", type(e).__name__)
        else:
            await _create_indexes(db)
            log.info("MongoDB'ga ulandi (baza: %s)", MONGODB_DB)
            return True
        if not retry_forever:
            return False
        log.info("%d soniyadan keyin qayta urinaman...", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def _create_indexes(db) -> None:
    """Indekslar faqat tezlik uchun — biri yaratilmasa ham dastur ishlashda davom etadi."""
    indexes = [
        ("history", [("chat_id", ASCENDING), ("_id", DESCENDING)], {}),
        ("history", [("role", ASCENDING), ("ts", DESCENDING)], {}),
        ("transactions", [("day", ASCENDING), ("ts", ASCENDING)], {}),
        ("transactions", "batch", {}),
        ("archive", [("chat_id", ASCENDING), ("msg_id", ASCENDING)], {"unique": True}),
        ("archive", [("msg_id", ASCENDING), ("channel", ASCENDING)], {}),
        ("archive_chats", [("last", DESCENDING)], {}),
        ("archive_chats", [("drive_dirty", ASCENDING), ("changed", ASCENDING)], {}),
        ("archive", [("drive_media", ASCENDING), ("date", ASCENDING)], {"sparse": True}),
        ("archive", [("date", DESCENDING)], {}),
        ("archive", [("deleted_at", DESCENDING)], {"sparse": True}),
        ("archive", [("edited_at", DESCENDING)], {"sparse": True}),
        ("watches", [("active", ASCENDING), ("created", ASCENDING)], {}),
        ("ig_archive", [("thread_id", ASCENDING), ("msg_id", ASCENDING)], {"unique": True}),
        ("ig_archive", [("thread_id", ASCENDING), ("date", ASCENDING)], {}),
        ("ig_chats", [("last", DESCENDING)], {}),
        ("ig_archive", [("media_state", ASCENDING), ("date", DESCENDING)], {"sparse": True}),
        ("ig_media", [("thread_id", ASCENDING), ("msg_id", ASCENDING)], {}),
    ]
    failed = []
    for name, keys, opts in indexes:
        try:
            await db[name].create_index(keys, **opts)
        except PyMongoError as e:
            failed.append(f"{name}: {type(e).__name__}")
    if failed:
        log.warning("Ba'zi indekslar yaratilmadi (dastur baribir ishlaydi): %s", ", ".join(failed))


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
def _bio_id(slot: int = 1) -> str:
    """1-akkaunt — «bio», 2-akkaunt — «bio2» (asl bio'lar aralashib ketmasin)."""
    return "bio" if int(slot) == 1 else f"bio{int(slot)}"


async def get_bio_state(slot: int = 1) -> dict:
    return await _get_db().bio_state.find_one({"_id": _bio_id(slot)}) or {}


async def save_bio_state(slot: int = 1, **fields) -> None:
    await _get_db().bio_state.update_one({"_id": _bio_id(slot)}, {"$set": fields}, upsert=True)


async def save_original_bio_once(about: str, saved_at: float, slot: int = 1) -> bool:
    """Asl bio'ni faqat birinchi marta yozadi; mavjud zaxirani hech qachon ustidan yozmaydi."""
    bid = _bio_id(slot)
    res = await _get_db().bio_state.update_one(
        {"_id": bid, "original_bio": {"$exists": False}},
        {"$set": {"original_bio": about, "original_saved_at": saved_at}},
        upsert=False,
    )
    if res.matched_count:
        return True
    if await _get_db().bio_state.find_one({"_id": bid}) is None:
        await _get_db().bio_state.insert_one(
            {"_id": bid, "original_bio": about, "original_saved_at": saved_at}
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


def _real_title(chat_id: int, title: str | None) -> bool:
    """O'chirilgan akkauntning ismi bo'sh keladi (yoki id ga aylanadi) — bunday "nom" eski ismni o'chirmasin."""
    return bool(title) and title != str(chat_id)


async def archive_touch_chat(chat_id: int, title: str, username: str | None, kind: str, ts: float,
                             added: int = 1, account_deleted: bool = False) -> None:
    fields: dict = {"kind": kind}
    update: dict = {"$set": fields, "$max": {"last": ts}, "$inc": {"count": added}}
    if _real_title(chat_id, title):
        fields["title"] = title
    else:  # akkaunt o'chirilgan — oldin saqlangan ism qoladi
        update["$setOnInsert"] = {"title": str(chat_id)}
    if account_deleted:
        fields["account_deleted"] = True
        update["$min"] = {"account_deleted_at": time.time()}
    else:
        fields["username"] = username
    if added:  # yangi xabar bor — Drive'dagi nusxa yangilanishi kerak
        fields.update(changed=time.time(), drive_dirty=True)
    await _get_db().archive_chats.update_one({"_id": chat_id}, update, upsert=True)


async def archive_mark_account_deleted(chat_id: int, title: str = "") -> None:
    """Suhbatdosh akkauntini o'chirgan. title — o'chishidan oldingi ismi (ma'lum bo'lsa)."""
    fields: dict = {"account_deleted": True, "kind": "user"}
    update: dict = {"$set": fields, "$min": {"account_deleted_at": time.time()},
                    "$setOnInsert": {"count": 0, "last": 0}}
    if _real_title(chat_id, title):
        fields["title"] = title
    else:
        update["$setOnInsert"]["title"] = str(chat_id)
    await _get_db().archive_chats.update_one({"_id": chat_id}, update, upsert=True)


async def archive_deleted_accounts(limit: int = 300) -> list[dict]:
    """Akkaunti o'chirilgan suhbatdoshlar (oxirgi yozishganidan boshlab)."""
    rows = await _get_db().archive_chats.find({"account_deleted": True})         .sort("last", DESCENDING).limit(limit).to_list()
    for r in rows:
        r["chat_id"] = r.pop("_id")
    return rows


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
    """Drive'ga yuklanishi kutilayotgan media: avval yangi kelganlari ("pending", kelish tartibida),
    keyin eski chatlardagilari ("history", eng yangisidan boshlab)."""
    db = _get_db()
    rows = await db.archive.find({"drive_media": "pending"}, _ARCHIVE_FIELDS) \
        .sort("date", ASCENDING).limit(limit).to_list()
    if len(rows) < limit:
        rows += await db.archive.find({"drive_media": "history"}, _ARCHIVE_FIELDS) \
            .sort("date", DESCENDING).limit(limit - len(rows)).to_list()
    return rows


async def archive_media_mark_history(kinds: list[str], all_files: bool) -> int:
    """Arxivdagi (mavjud chatlardan nusxalangan) rasm/videolarni Drive navbatiga qo'shadi. Qo'shilganlar soni."""
    files: dict = {"media": "fayl"}
    if not all_files:
        files["mime"] = {"$regex": "^(image|video)/"}
    res = await _get_db().archive.update_many(
        {"drive_media": {"$exists": False}, "deleted": {"$ne": True},
         "$or": [{"media": {"$in": list(kinds)}}, files]},
        {"$set": {"drive_media": "history"}})
    return res.modified_count


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
        query["$or"] = [{"title": rx}, {"username": rx}, {"guess_title": rx}]
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


# ---------- arxivdan qidirish (o'chirilgan / tahrirlangan) ----------
async def archive_search(chat_id: int | None, keywords: list[str], days: int | None = None,
                         only_them: bool = False, limit: int = 30, everything: bool = False) -> list[dict]:
    """Telegram'da endi yo'q (o'chirilgan) yoki tahrirlangan xabarlar ichidan kalit so'zlar bo'yicha.
    everything=True — arxivdagi barcha xabarlardan (akkaunti o'chirilgan odam bilan yozishma)."""
    import re
    words = [w for w in (k.strip() for k in keywords) if len(w) >= 2][:8]
    if not words:
        return []
    rx = [{"$regex": re.escape(w), "$options": "i"} for w in words]
    conds: list[dict] = [{"$or": [{"text": r} for r in rx] + [{"edits.text": r} for r in rx]}]
    if not everything:
        conds.insert(0, {"$or": [{"deleted": True}, {"edits.0": {"$exists": True}}]})
    query: dict = {"$and": conds}
    if chat_id is not None:
        query["chat_id"] = chat_id
    if days:
        query["date"] = {"$gte": time.time() - days * 86400}
    if only_them:
        query["out"] = False
    return await _get_db().archive.find(query, _ARCHIVE_FIELDS).sort("date", DESCENDING).limit(limit).to_list()


async def archive_word_hits(chat_ids: list[int], keywords: list[str], limit: int = 30) -> dict[int, int]:
    """Shu chatlardan qaysilarida bu so'zlar uchraydi: {chat_id: mos xabarlar soni}."""
    import re
    words = [w for w in (k.strip() for k in keywords) if len(w) >= 3][:8]
    if not words or not chat_ids:
        return {}
    rx = [{"$regex": re.escape(w), "$options": "i"} for w in words]
    cursor = await _get_db().archive.aggregate([
        {"$match": {"chat_id": {"$in": list(chat_ids)}, "$or": [{"text": r} for r in rx]}},
        {"$group": {"_id": "$chat_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": limit}])
    return {r["_id"]: r["n"] for r in await cursor.to_list()}


# ---------- haftalik hisobot ----------
async def archive_week_stats(since: float) -> dict:
    """Oxirgi davr statistikasi: faol chatlar, o'chirish/tahrirlar, javobsiz qolganlar."""
    db = _get_db()

    async def agg(pipeline):
        cursor = await db.archive.aggregate(pipeline)
        return await cursor.to_list()

    # 📢 kanal postlari haftalik hisobotga kirmaydi (faol kanal odamlarni siqib chiqarardi)
    skip = [c["_id"] for c in await db.archive_chats.find({"kind": "channel"}, {"_id": 1}).to_list()]
    active = await agg([
        {"$match": {"date": {"$gte": since}, "chat_id": {"$nin": skip}}},
        {"$group": {"_id": "$chat_id", "inc": {"$sum": {"$cond": ["$out", 0, 1]}},
                    "out": {"$sum": {"$cond": ["$out", 1, 0]}}}},
        {"$sort": {"inc": -1}}, {"$limit": 50}])
    deleted = await agg([
        {"$match": {"deleted_at": {"$gte": since}, "out": False, "chat_id": {"$nin": skip}}},
        {"$group": {"_id": "$chat_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 10}])
    edited = await agg([
        {"$match": {"edited_at": {"$gte": since}, "out": False, "chat_id": {"$nin": skip}}},
        {"$group": {"_id": "$chat_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 10}])
    last = await agg([
        {"$match": {"date": {"$gte": since - 30 * 86400}, "deleted": {"$ne": True}}},
        {"$sort": {"date": -1}},
        {"$group": {"_id": "$chat_id", "out": {"$first": "$out"}, "date": {"$first": "$date"},
                    "text": {"$first": "$text"}, "media": {"$first": "$media"}}}])
    # faqat shaxsiy chatlar (guruh id lari manfiy)
    unanswered = sorted((r for r in last if r["_id"] > 0 and not r["out"] and r["date"] < time.time() - 86400),
                        key=lambda r: r["date"])[:10]
    ids = {r["_id"] for r in active + deleted + edited + unanswered}
    chats = {c["_id"]: c for c in await db.archive_chats.find({"_id": {"$in": list(ids)}}).to_list()}
    return {"active": active, "deleted": deleted, "edited": edited, "unanswered": unanswered, "chats": chats}


async def archive_active_chats(since: float, limit: int = 15, kind: str = "user") -> list[dict]:
    """Shu vaqtdan beri yozishilgan chatlar (eng faolidan)."""
    rows = await _get_db().archive_chats.find({"last": {"$gte": since}, "kind": kind}) \
        .sort("last", DESCENDING).limit(limit).to_list()
    for r in rows:
        r["chat_id"] = r.pop("_id")
    return rows


async def archive_user_chats(limit: int = 1000) -> list[dict]:
    """Profili kuzatiladigan odamlar (yozishgan shaxsiy chatlar), eng faolidan."""
    rows = await _get_db().archive_chats.find({"kind": "user", "_id": {"$gt": 0}}) \
        .sort("last", DESCENDING).limit(limit).to_list()
    for r in rows:
        r["chat_id"] = r.pop("_id")
    return rows


# ---------- profil snapshotlari ----------
async def profile_get(user_id: int) -> dict | None:
    return await _get_db().profiles.find_one({"_id": user_id})


async def profile_set(user_id: int, **fields) -> None:
    await _get_db().profiles.update_one({"_id": user_id}, {"$set": {**fields, "updated": time.time()}}, upsert=True)


async def profile_add_saved_photo(user_id: int, photo_id: int) -> None:
    await _get_db().profiles.update_one({"_id": user_id}, {"$addToSet": {"saved_photos": photo_id}}, upsert=True)



# ---------- eslatmalar (watchers.py): raqam Telegram'da paydo bo'lsa / odam online bo'lsa ----------
async def watch_add(doc: dict) -> str:
    res = await _get_db().watches.insert_one({**doc, "active": True, "created": time.time()})
    return str(res.inserted_id)


async def watch_list(active_only: bool = True) -> list[dict]:
    rows = await _get_db().watches.find({"active": True} if active_only else {}) \
        .sort("created", ASCENDING).to_list()
    return [_prm_out(r) for r in rows]


async def watch_update(watch_id: str, **fields) -> bool:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        res = await _get_db().watches.update_one({"_id": ObjectId(watch_id)}, {"$set": fields})
    except InvalidId:
        return False
    return res.matched_count > 0


async def watch_delete(watch_id: str) -> bool:
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        res = await _get_db().watches.delete_one({"_id": ObjectId(watch_id)})
    except InvalidId:
        return False
    return res.deleted_count > 0


# ---------- 🔑 Telegram akkaunt sessiyasi (bot orqali login, tg_login.py) ----------
def _tg_session_id(slot: int = 1) -> str:
    """1-akkaunt — «tg_session», 2-akkaunt — «tg_session2»."""
    return "tg_session" if int(slot) == 1 else f"tg_session{int(slot)}"


async def tg_get_session(slot: int = 1) -> dict | None:
    """Bot orqali kirilgan akkaunt sessiyasi: {"string": ..., "user_id": ...}. MAXFIY — to'liq kirish beradi."""
    return await _get_db().settings.find_one({"_id": _tg_session_id(slot)})


async def tg_set_session(string: str, user_id: int, slot: int = 1) -> None:
    await _get_db().settings.update_one({"_id": _tg_session_id(slot)},
                                        {"$set": {"string": string, "user_id": user_id, "ts": time.time()}},
                                        upsert=True)


async def tg_delete_session(slot: int = 1) -> None:
    await _get_db().settings.delete_one({"_id": _tg_session_id(slot)})


async def tg_get_owner() -> int | None:
    """Oxirgi marta ulangan akkaunt egasi (nazorat botidan faqat u login qila oladi)."""
    doc = await _get_db().settings.find_one({"_id": "tg_owner"})
    return int(doc["user_id"]) if doc and doc.get("user_id") else None


async def tg_set_owner(user_id: int) -> None:
    await _get_db().settings.update_one({"_id": "tg_owner"}, {"$set": {"user_id": int(user_id)}}, upsert=True)


# ---------- ☁️ Google Drive tokeni (botdan /drive bilan qayta ulanganda) ----------
async def drive_get_token() -> str | None:
    doc = await _get_db().settings.find_one({"_id": "gdrive_token"})
    return (doc or {}).get("refresh_token") or None


async def drive_set_token(token: str) -> None:
    await _get_db().settings.update_one({"_id": "gdrive_token"},
                                        {"$set": {"refresh_token": token, "ts": time.time()}}, upsert=True)


# ---------- 🤖 nazorat boti sessiyasi (qayta ishga tushganda qayta login — FloodWait bo'lmasin) ----------
async def bot_get_session() -> dict | None:
    return await _get_db().settings.find_one({"_id": "bot_session"})


async def bot_set_session(string: str, bot_id: str) -> None:
    await _get_db().settings.update_one({"_id": "bot_session"},
                                        {"$set": {"string": string, "bot_id": bot_id, "ts": time.time()}},
                                        upsert=True)


async def bot_delete_session() -> None:
    await _get_db().settings.delete_one({"_id": "bot_session"})


# ---------- 📸 Instagram (instagram.py) ----------
def _ig_session_id(slot: str = "1") -> str:
    """1-akkaunt — «session» (eski yozuv o'zgarmaydi), 2-akkaunt — «session2»."""
    return "session" if str(slot) == "1" else f"session{slot}"


async def ig_get_session(slot: str = "1") -> dict | None:
    return await _get_db().instagram.find_one({"_id": _ig_session_id(slot)})


async def ig_set_session(slot: str = "1", **fields) -> None:
    await _get_db().instagram.update_one({"_id": _ig_session_id(slot)}, {"$set": {**fields, "ts": time.time()}},
                                         upsert=True)


async def ig_delete_session(slot: str = "1") -> None:
    await _get_db().instagram.delete_one({"_id": _ig_session_id(slot)})


async def ig_saved_has(item_id: str) -> bool:
    """Bir martalik media allaqachon saqlanganmi (yoki «ochilgan ekan» deb aytilganmi)."""
    return await _get_db().ig_saved.find_one({"_id": item_id}, {"_id": 1}) is not None


async def ig_saved_add(item_id: str, doc: dict) -> bool:
    try:
        await _get_db().ig_saved.insert_one({"_id": item_id, **doc, "ts": time.time()})
        return True
    except DuplicateKeyError:
        return False


async def ig_saved_count() -> int:
    return await _get_db().ig_saved.count_documents({"status": "saved"})


async def ig_archive_add_many(docs: list[dict]) -> int:
    """Instagram xabarlarini arxivga qo'shadi (bor bo'lganlari o'zgarmaydi). Yangi qo'shilganlar soni."""
    from pymongo import UpdateOne
    if not docs:
        return 0
    ops = [UpdateOne({"thread_id": d["thread_id"], "msg_id": d["msg_id"]}, {"$setOnInsert": d}, upsert=True)
           for d in docs]
    res = await _get_db().ig_archive.bulk_write(ops, ordered=False)
    return res.upserted_count


async def ig_archive_window(thread_id: str, since: float) -> list[dict]:
    """Shu vaqtdan keyingi, hali o'chirilmagan xabarlar (qaytarib olinganlarni aniqlash uchun)."""
    return await _get_db().ig_archive.find(
        {"thread_id": thread_id, "date": {"$gte": since}, "deleted": {"$ne": True}}, {"_id": 0}).to_list()


async def ig_archive_mark_deleted(thread_id: str, msg_ids: list[str], ts: float) -> None:
    await _get_db().ig_archive.update_many({"thread_id": thread_id, "msg_id": {"$in": msg_ids}},
                                           {"$set": {"deleted": True, "deleted_at": ts}})


async def ig_ai_ids(thread_id: str) -> set[str]:
    """Kotiba (AI) yozgan xabarlar — tarixda egasi yozganidan farqlash uchun."""
    rows = await _get_db().ig_archive.find({"thread_id": thread_id, "ai": True}, {"msg_id": 1}) \
        .sort("date", DESCENDING).limit(100).to_list()
    return {r["msg_id"] for r in rows}


async def ig_archive_export(thread_id: str, limit: int) -> list[dict]:
    rows = await _get_db().ig_archive.find({"thread_id": thread_id}, {"_id": 0}) \
        .sort("date", DESCENDING).limit(limit).to_list()
    return rows[::-1]


async def ig_chat_touch(thread_id: str, title: str, usernames: list[str], group: bool, last: float,
                        added: int) -> None:
    """Chat ma'lumotini yangilaydi. Yangi xabar qo'shilgan bo'lsa — Drive'ga qayta yuklashga belgilanadi."""
    update: dict = {"$set": {"title": title, "usernames": usernames, "group": group},
                    "$max": {"last": last}, "$inc": {"count": added}}
    if added:
        update["$set"].update(changed=time.time(), drive_dirty=True)
    await _get_db().ig_chats.update_one({"_id": thread_id}, update, upsert=True)


async def ig_chat_set(thread_id: str, **fields) -> None:
    await _get_db().ig_chats.update_one({"_id": thread_id}, {"$set": fields}, upsert=True)


async def ig_chat_count(thread_id: str) -> int:
    return await _get_db().ig_archive.count_documents({"thread_id": thread_id})


async def ig_archive_page(thread_id: str, skip: int, limit: int) -> list[dict]:
    """Botda ko'rish uchun: eng yangi xabarlardan boshlab bir sahifa (bittasi ortiqcha — davomi bormi)."""
    rows = await _get_db().ig_archive.find({"thread_id": thread_id}, {"_id": 0}) \
        .sort("date", DESCENDING).skip(skip).limit(limit + 1).to_list()
    return rows


async def ig_drive_pending(limit: int = 50) -> list[dict]:
    """Drive'ga yuklanishi kerak (o'zgargan) Instagram chatlari."""
    rows = await _get_db().ig_chats.find({"drive_dirty": True}).sort("changed", ASCENDING) \
        .limit(limit).to_list()
    for r in rows:
        r["thread_id"] = r.pop("_id")
    return rows


async def ig_drive_done(thread_id: str, changed, file_id: str, link: str) -> None:
    """Yuklandi. Yuklash paytida chat yana o'zgargan bo'lsa ("changed" boshqa) — belgi qoladi."""
    db = _get_db()
    await db.ig_chats.update_one({"_id": thread_id}, {"$set": {"drive_file_id": file_id, "drive_link": link,
                                                               "drive_synced": time.time()}})
    await db.ig_chats.update_one({"_id": thread_id, "changed": changed}, {"$set": {"drive_dirty": False}})


async def ig_drive_mark_all(only_missing: bool = False) -> int:
    query: dict = {"count": {"$gt": 0}}
    if only_missing:
        query["drive_file_id"] = {"$exists": False}
    res = await _get_db().ig_chats.update_many(query, {"$set": {"drive_dirty": True}})
    return res.modified_count


async def ig_backfill_pending(limit: int = 50) -> list[dict]:
    """Eski yozishmalari hali yuklanmagan chatlar (bir martalik tarix nusxasi uchun)."""
    rows = await _get_db().ig_chats.find({"backfilled": {"$exists": False}}) \
        .sort("last", DESCENDING).limit(limit).to_list()
    for r in rows:
        r["thread_id"] = r.pop("_id")
    return rows


IG_MEDIA_PENDING = {"pending", "retry"}


async def ig_media_pending(limit: int = 10) -> list[dict]:
    """Yuklab olinishi kerak bo'lgan media: Instagram havolalari tez eskiradi — eng yangisidan boshlanadi."""
    rows = await _get_db().ig_archive.find(
        {"media_state": {"$in": list(IG_MEDIA_PENDING)}}, {"_id": 0}).sort("date", DESCENDING) \
        .limit(limit).to_list()
    return rows


async def ig_media_save(thread_id: str, msg_id: str, data: bytes, mime: str) -> None:
    """Media faylni bazaga saqlaydi (HTML ichida ochilishi uchun)."""
    from bson import Binary
    await _get_db().ig_media.update_one(
        {"_id": f"{thread_id}:{msg_id}"},
        {"$set": {"thread_id": thread_id, "msg_id": msg_id, "data": Binary(data),
                  "mime": mime, "size": len(data), "ts": time.time()}},
        upsert=True)


async def ig_media_get(thread_id: str, msg_ids: list[str]) -> dict[str, tuple[bytes, str]]:
    """{msg_id: (fayl, mime)} — HTML yasashda ishlatiladi."""
    rows = await _get_db().ig_media.find({"thread_id": thread_id, "msg_id": {"$in": msg_ids}}).to_list()
    return {r["msg_id"]: (bytes(r["data"]), r.get("mime") or "application/octet-stream") for r in rows}


async def ig_media_set(thread_id: str, msg_id: str, **fields) -> None:
    await _get_db().ig_archive.update_one({"thread_id": thread_id, "msg_id": msg_id}, {"$set": fields})


async def ig_media_counts() -> dict:
    cursor = await _get_db().ig_archive.aggregate([
        {"$match": {"media_state": {"$exists": True}}},
        {"$group": {"_id": "$media_state", "n": {"$sum": 1}}}])
    rows = await cursor.to_list()
    return {r["_id"]: r["n"] for r in rows}


async def ig_media_bytes() -> int:
    cursor = await _get_db().ig_media.aggregate([{"$group": {"_id": None, "n": {"$sum": "$size"}}}])
    rows = await cursor.to_list()
    return rows[0]["n"] if rows else 0


async def ig_stats() -> dict:
    db = _get_db()
    return {"chats": await db.ig_chats.count_documents({}),
            "messages": await db.ig_archive.count_documents({}),
            "unsent": await db.ig_archive.count_documents({"deleted": True}),
            "pending": await db.ig_chats.count_documents({"drive_dirty": True}),
            "left": await db.ig_chats.count_documents({"backfilled": {"$exists": False}}),
            "media": await db.ig_media.count_documents({}),
            "media_left": await db.ig_archive.count_documents({"media_state": {"$in": list(IG_MEDIA_PENDING)}})}


async def ig_chats(page: int, per_page: int) -> list[dict]:
    """Oxirgi yozishma bo'yicha; bitta ortiqcha qaytaradi (keyingi sahifa bormi — bilish uchun)."""
    rows = await _get_db().ig_chats.find({}).sort("last", DESCENDING).skip(page * per_page) \
        .limit(per_page + 1).to_list()
    for r in rows:
        r["thread_id"] = r.pop("_id")
    return rows


async def ig_chat_get(thread_id: str) -> dict | None:
    doc = await _get_db().ig_chats.find_one({"_id": thread_id})
    if doc:
        doc["thread_id"] = doc.pop("_id")
    return doc


# ---------- 📢 Kanallar arxivi (channels.py) ----------
async def channel_archive_list() -> list[dict]:
    rows = await _get_db().channel_archive.find({}).to_list()
    return [{"chat_id": r["_id"], "title": r.get("title") or ""} for r in rows]


async def channel_archive_add(chat_id: int, title: str) -> None:
    await _get_db().channel_archive.update_one({"_id": chat_id}, {"$set": {"title": title, "ts": time.time()}},
                                               upsert=True)


async def channel_archive_remove(chat_id: int) -> None:
    await _get_db().channel_archive.delete_one({"_id": chat_id})


# ---------- 👥 Guruh xabarlarini klonlash (groups.py) ----------
async def group_clone_list() -> list[dict]:
    rows = await _get_db().group_clone.find({}).to_list()
    return [{"chat_id": r["_id"], "title": r.get("title") or ""} for r in rows]


async def group_clone_add(chat_id: int, title: str) -> None:
    await _get_db().group_clone.update_one({"_id": chat_id}, {"$set": {"title": title, "ts": time.time()}},
                                           upsert=True)


async def group_clone_remove(chat_id: int) -> None:
    await _get_db().group_clone.delete_one({"_id": chat_id})


# ---------- zaxira ----------
async def collection_names() -> list[str]:
    return sorted(await _get_db().list_collection_names())


def raw_collection(name: str):
    """Zaxira/tiklash uchun to'g'ridan-to'g'ri kolleksiya."""
    return _get_db()[name]


async def ping() -> None:
    await _get_db().command("ping")
