"""MongoDB Atlas saqlash: suhbat tarixi, inbox, sozlamalar, istisnolar va bio holati."""
import asyncio
import logging
import sys
import time

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError

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
