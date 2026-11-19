"""Profil kuzatuvi: yozishgan odamlaringiz ism, username, bio, rasmini o'zgartirsa yoki akkauntini o'chirsa —
egasiga xabar; profil rasmlari tarixi Drive'dagi chat papkasiga (profil_rasmlari/) saqlanadi.

- Real vaqtda: Telegram UpdateUserName / UpdateUser yuborganda.
- Davriy: har PROFILE_CHECK_HOURS soatda barcha kuzatiladiganlar 100 tadan (users.getUsers) tekshiriladi;
  bio esa kuniga bir marta eng faol 50 chat uchun (users.getFullUser).
- Birinchi marta ko'rilgan odam uchun faqat "snapshot" yoziladi — ogohlantirish yuborilmaydi.
"""
import asyncio
import logging
import time
from collections import defaultdict

from telethon import errors, events
from telethon.tl.functions.users import GetFullUserRequest, GetUsersRequest
from telethon.tl.types import UpdateUser, UpdateUserName, User

import archive
import config
import runtime
import schedule
import storage

log = logging.getLogger("profiles")

BATCH = 100                 # users.getUsers bitta so'rovda
BIO_CHECK_CHATS = 50        # bio kuniga bir marta shuncha eng faol chat uchun
BIO_PAUSE = 2.0             # getFullUser so'rovlari orasida (FloodWait bo'lmasligi uchun)
HISTORY_CHATS = 50          # birinchi ko'rishda eski rasmlari ham saqlanadigan eng faol chatlar
HISTORY_PHOTOS = 10
PHOTO_FOLDER = "profil_rasmlari"

_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_alert = None               # async (user_id, info, changes, photo_bytes, drive_link) -> None


# ---------- solishtirish ----------
def snapshot(user: User, about: str | None = None) -> dict:
    photo_id = getattr(user.photo, "photo_id", None)   # UserProfilePhotoEmpty / None -> None
    username = user.username or next((u.username for u in (user.usernames or []) if u.active), "") or ""
    snap = {"first": user.first_name or "", "last": user.last_name or "", "username": username,
            "photo_id": photo_id, "deleted": bool(user.deleted)}
    if about is not None:
        snap["about"] = about
    return snap


def _q(text: str) -> str:
    return f"«{text}»" if text else "(bo'sh)"


def diff(old: dict, new: dict) -> list[str]:
    """O'zgarishlar ro'yxati (odam tilida). Bir xil bo'lsa — bo'sh."""
    if new.get("deleted") and not old.get("deleted"):
        return ["❌ Akkauntini o'chirdi"]
    changes = []
    old_name = " ".join(p for p in (old.get("first"), old.get("last")) if p)
    new_name = " ".join(p for p in (new.get("first"), new.get("last")) if p)
    if old_name != new_name:
        changes.append(f"📝 Ism: {_q(old_name)} → {_q(new_name)}")
    if (old.get("username") or "") != (new.get("username") or ""):
        o, n = old.get("username"), new.get("username")
        changes.append(f"🔗 Username: {'@' + o if o else '(yo‘q)'} → {'@' + n if n else '(olib tashladi)'}")
    if "about" in new and "about" in old and (old["about"] or "") != (new["about"] or ""):
        changes.append(f"📄 Bio: {_q(old['about'] or '')} → {_q(new['about'] or '')}")
    o, n = old.get("photo_id"), new.get("photo_id")
    if o != n:
        if n and o:
            changes.append("🖼 Yangi profil rasmi qo'ydi")
        elif n:
            changes.append("🖼 Profil rasmi qo'ydi")
        else:
            changes.append("🚫 Profil rasmini o'chirdi yoki sizdan yashirdi")
    return changes


# ---------- rasmlar tarixi ----------
async def _save_photo(user_id: int, photo_id: int, data: bytes, when: float | None = None) -> str | None:
    """Rasmni Drive'dagi <chat papkasi>/profil_rasmlari/ ga yuklaydi (bir rasm bir marta). Havola yoki None."""
    import gdrive
    if not (data and gdrive.enabled() and runtime.get("profile_photos")):
        return None
    prof = await storage.profile_get(user_id) or {}
    if photo_id in (prof.get("saved_photos") or []):
        return None
    stamp = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
    name = f"{stamp}_{photo_id}.jpg"

    async def upload(folder):
        sub = await gdrive.ensure_folder(PHOTO_FOLDER, parent=folder)
        return await gdrive.upload(name, data, "image/jpeg", parent=sub)

    res = await archive._in_folder(user_id, None, upload)
    await storage.profile_add_saved_photo(user_id, photo_id)
    return archive._link(res)


async def _save_history(client, user: User) -> int:
    """Birinchi ko'rishda: odamning hozirgi va oldingi (ko'pi bilan 10 ta) profil rasmlari."""
    saved = 0
    async for photo in client.iter_profile_photos(user, limit=HISTORY_PHOTOS):
        try:
            data = await client.download_media(photo, file=bytes)
            when = photo.date.timestamp() if getattr(photo, "date", None) else None
            if await _save_photo(user.id, photo.id, data, when):
                saved += 1
        except Exception as e:
            log.info("Profil rasmi saqlanmadi (%s)", type(e).__name__)
        await asyncio.sleep(0.5)
    return saved


# ---------- tekshirish ----------
async def check_user(client, user: User, about: str | None = None, history: bool = False) -> list[str]:
    """Bitta odamning profilini oldingi holati bilan solishtiradi; o'zgargan bo'lsa egasiga xabar beradi."""
    if not isinstance(user, User) or user.bot or user.is_self or getattr(user, "min", False):
        return []
    async with _locks[user.id]:
        old = await storage.profile_get(user.id)
        new = snapshot(user, about)
        if old is None:   # birinchi ko'rish — jim yozib qo'yamiz
            await storage.profile_set(user.id, **new)
            import gdrive
            if history and new["photo_id"] and runtime.get("profile_photos") and gdrive.enabled():
                await _save_history(client, user)
            return []
        changes = diff(old, new)
        extra = {}
        if new["deleted"] and not old.get("deleted"):
            # akkaunt o'chdi — ismi Telegram'dan yo'qoladi; oxirgi ma'lum ismni saqlab qolamiz
            known = " ".join(p for p in (old.get("first"), old.get("last")) if p)
            if known:
                extra["known_name"] = known
            try:
                await storage.archive_mark_account_deleted(user.id, known)
            except Exception as e:
                log.info("Arxivda o'chirilgan deb belgilanmadi (%s)", type(e).__name__)
        await storage.profile_set(user.id, **new, **extra)
    if not changes:
        return []
    photo, link = None, None
    if new.get("photo_id") and new["photo_id"] != old.get("photo_id"):
        try:
            photo = await client.download_profile_photo(user, file=bytes)
            link = await _save_photo(user.id, new["photo_id"], photo)
        except Exception as e:
            log.info("Yangi profil rasmi olinmadi (%s)", type(e).__name__)
    if runtime.get("profile_watch") and _alert:
        info = await storage.archive_chat(user.id) or {"chat_id": user.id}
        info.setdefault("title", " ".join(p for p in (new["first"], new["last"]) if p) or str(user.id))
        try:
            await _alert(user.id, info, changes, photo, link)
        except Exception:
            log.exception("Profil ogohlantirishi yuborilmadi")
    return changes


async def _tracked(user_id: int) -> bool:
    info = await storage.archive_chat(user_id)
    return bool(info and info.get("kind") == "user")


async def _flood_safe(coro_fn):
    """FloodWait bo'lsa — Telegram aytgan vaqtcha kutib, bir marta qayta urinadi."""
    try:
        return await coro_fn()
    except errors.FloodWaitError as e:
        log.info("Telegram cheklovi: %d soniya kutiladi", e.seconds)
        await asyncio.sleep(e.seconds + 1)
        return await coro_fn()


async def check_full(client, user_id: int, history: bool = False) -> list[str]:
    """getFullUser bilan (bio ham) tekshirish."""
    full = await _flood_safe(lambda: client(GetFullUserRequest(user_id)))
    user = next((u for u in full.users if u.id == user_id), None)
    if user is None:
        return []
    return await check_user(client, user, full.full_user.about or "", history=history)


async def check_all(client, with_bio: bool) -> int:
    """Barcha kuzatiladiganlarni tekshiradi. O'zgargan profillar soni."""
    chats = await storage.archive_user_chats()
    active_ids = {c["chat_id"] for c in chats[:HISTORY_CHATS]}
    inputs = []
    for c in chats:
        try:
            inputs.append(await client.get_input_entity(c["chat_id"]))
        except (ValueError, TypeError):
            continue  # sessiyada yo'q (masalan, eski chat) — keyingi safar
    changed = 0
    for i in range(0, len(inputs), BATCH):
        chunk = inputs[i:i + BATCH]
        try:
            users = await _flood_safe(lambda: client(GetUsersRequest(chunk)))
        except Exception as e:
            log.warning("Profillar olinmadi (%s)", type(e).__name__)
            continue
        for u in users:
            if isinstance(u, User):
                try:
                    changed += bool(await check_user(client, u, history=u.id in active_ids))
                except Exception as e:
                    log.warning("Profil %s tekshirilmadi (%s)", u.id, type(e).__name__)
        await asyncio.sleep(1)
    if with_bio:
        for c in chats[:BIO_CHECK_CHATS]:
            try:
                changed += bool(await check_full(client, c["chat_id"]))
            except Exception as e:
                log.info("Bio tekshirilmadi (%s): %s", c["chat_id"], type(e).__name__)
            await asyncio.sleep(BIO_PAUSE)
    return changed


def _enabled() -> bool:
    return bool(runtime.get("profile_watch") or runtime.get("profile_photos"))


def setup(client, alert) -> None:
    global _alert
    _alert = alert

    @client.on(events.Raw(types=(UpdateUserName, UpdateUser)))
    async def on_profile_update(update):
        if not _enabled():
            return
        try:
            if await _tracked(update.user_id):
                await check_full(client, update.user_id)
        except Exception as e:
            log.info("Profil yangilanishi tekshirilmadi (%s)", type(e).__name__)


async def run(client) -> None:
    """Davriy tekshiruv (fon vazifasi). Bio kuniga bir marta."""
    await asyncio.sleep(90)
    last_bio_day = ""
    while True:
        if _enabled():
            today = schedule.now().date().isoformat()
            try:
                n = await check_all(client, with_bio=today != last_bio_day)
                last_bio_day = today
                if n:
                    log.info("Profil kuzatuvi: %d ta o'zgarish", n)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Profil kuzatuvi bajarilmadi")
        await asyncio.sleep(config.PROFILE_CHECK_HOURS * 3600)
