"""Chat arxivi: yozishmalar MongoDB'ga yozib boriladi — suhbatdosh xabarni yoki butun chatni o'chirsa ham,
bot orqali uni HTML fayl qilib qayta yuklab olish mumkin.

- Barcha shaxsiy chatlar arxivlanadi (/ignore qilingan istisnolar ham). Guruhlar — ixtiyoriy.
- Login kodlari keladigan Telegram rasmiy akkaunti, botlar va "Saqlangan xabarlar" arxivlanmaydi.
- Xabar tahrirlansa — eski matni ham saqlanadi. O'chirilsa — "o'chirilgan" deb belgilanadi va egasiga
  bot orqali ogohlantirish keladi (bir vaqtda o'chirilganlari bitta xabarda).
- Kichik rasm va ovozli xabarlar (ARCHIVE_MEDIA_MAX_KB gacha) ham saqlanadi va HTML ichiga joylanadi.
"""
import asyncio
import base64
import gc
import html
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import events
from telethon.tl.types import Message, MessageMediaWebPage, PeerChannel, User

import channels
import config
import groups
import runtime
import storage

log = logging.getLogger("archive")

TELEGRAM_SERVICE_ID = 777000      # login kodlari — hech qachon saqlanmaydi
DELETE_GATHER_SECONDS = 3         # shu vaqt ichida o'chirilganlari bitta ogohlantirishga yig'iladi
# HTML ichiga joylanadigan media jami. base64 hajmni ~4/3 ga oshiradi: 30 MB -> ~40 MB fayl (bot limiti 50 MB)
HTML_MEDIA_BUDGET = 30 * 1024 * 1024
# Chat tarixini olganda HTML ichida ko'rinadigan media turlari (video — faqat Drive havolasi)
EMBED_KINDS = {"rasm", "ovozli xabar", "video-xabar", "stiker", "GIF", "audio"}
# Yengil media (rasm, ovoz, stiker, GIF) og'ir video-xabar/audiodan OLDIN joylanadi — barcha rasmlar sig'sin
LIGHT_EMBED_KINDS = {"rasm", "ovozli xabar", "stiker", "GIF"}

_on_deleted = None                # async (chat_id, docs) -> None — control_bot ogohlantirishi
_on_edited = None                 # async (chat_id, eski hujjat, yangi matn) -> None
_me_id = 0
_del_buf: dict[int, list[dict]] = {}
_del_last: dict[int, float] = {}
_del_tasks: dict[int, asyncio.Task] = {}
# 📢 2-akkaunt kanallari: qaysi akkaunt orqali olingan (media/HTML shu akkauntdan yuklanadi)
_clients: dict[int, object] = {}
_chat_via: dict[int, int] = {}
_arc_slots: set[int] = set()      # shaxsiy chatlari ham arxivlanadigan qo'shimcha akkauntlar (2)
_me_ids: dict[int, int] = {}      # akkaunt -> o'z id si («Saqlangan xabarlar» arxivlanmaydi)


# ---------- xabar -> arxiv yozuvi ----------
def display_name(entity) -> str:
    if entity is None:
        return ""
    if isinstance(entity, User):
        return " ".join(p for p in (entity.first_name, entity.last_name) if p) or (entity.username or "")
    return getattr(entity, "title", "") or ""


def is_deleted_account(entity) -> bool:
    """Telegram'da «Deleted Account» — ismi va username'i yo'q, lekin yozishma chatda qolgan."""
    return isinstance(entity, User) and bool(getattr(entity, "deleted", False))


def has_real_title(info: dict) -> bool:
    title = (info or {}).get("title") or ""
    return bool(title) and title != str(info.get("chat_id"))


def chat_label(info: dict) -> str:
    """Chat nomi odam uchun: akkaunt o'chirilgan bo'lsa — eski ismi yoki taxminiy ismi."""
    info = info or {}
    if has_real_title(info):
        return info["title"]
    if info.get("guess_title"):
        return f"≈ {info['guess_title']}"
    if info.get("account_deleted"):
        return f"O'chirilgan akkaunt #{info.get('chat_id')}"
    return info.get("title") or str(info.get("chat_id") or "")


def media_label(msg) -> str | None:
    if not msg.media or isinstance(msg.media, MessageMediaWebPage):
        return None
    if msg.voice:
        return "ovozli xabar"
    if msg.video_note:
        return "video-xabar"
    if msg.photo:
        return "rasm"
    if msg.gif:
        return "GIF"
    if msg.video:
        return "video"
    if msg.sticker:
        return "stiker"
    if msg.audio:
        return "audio"
    if msg.document:
        return "fayl"
    if msg.geo or msg.venue:
        return "lokatsiya"
    if msg.contact:
        return "kontakt"
    if msg.poll:
        return "so'rovnoma"
    return "media"


def message_doc(msg, chat_id: int, sender_name: str = "") -> dict:
    file = msg.file if msg.media and not isinstance(msg.media, MessageMediaWebPage) else None
    return {
        "chat_id": chat_id,
        "msg_id": msg.id,
        "channel": isinstance(msg.peer_id, PeerChannel),
        "out": bool(msg.out),
        "sender_id": msg.sender_id,
        "sender": sender_name,
        "text": msg.message or "",
        "media": media_label(msg),
        "file_name": getattr(file, "name", None) if file else None,
        "view_once": bool(getattr(msg.media, "ttl_seconds", None)),
        "mime": (getattr(file, "mime_type", None) or "") if file else "",
        "fwd": bool(msg.fwd_from),
        "reply_to": msg.reply_to_msg_id,
        "date": msg.date.timestamp() if msg.date else time.time(),
    }


def _blob_mime(msg) -> str | None:
    """Arxivga saqlanadigan (HTML'da ko'rinadigan) media turi; boshqalar faqat nomi bilan qoladi."""
    if msg.photo:
        return "image/jpeg"
    if msg.voice:
        return "audio/ogg"
    mime = (getattr(msg.file, "mime_type", None) or "").lower() if msg.file else ""
    if msg.sticker:
        return mime if mime in ("image/webp", "video/webm") else None
    if msg.video_note or mime.startswith(("image/", "audio/", "video/")):
        return mime or "video/mp4"
    return None


MONGO_BLOB_MAX = 12 * 1024 * 1024  # MongoDB hujjati ko'pi bilan 16 MB — kattasi saqlanmaydi (asli Drive'da)


async def _save_blob(msg, limit_kb: int | None = None) -> str | None:
    """Kichik media'ni MongoDB'ga (HTML ichida ko'rsatish uchun). Xato bo'lsa None — xabar baribir arxivlanadi."""
    try:
        return await _save_blob_inner(msg, limit_kb)
    except Exception as e:
        log.warning("Media arxivga saqlanmadi (%s) — xabar media'siz arxivlanadi", type(e).__name__)
        return None


async def _save_blob_inner(msg, limit_kb: int | None) -> str | None:
    limit = min((config.ARCHIVE_MEDIA_MAX_KB if limit_kb is None else limit_kb) * 1024, MONGO_BLOB_MAX)
    mime = _blob_mime(msg) if limit > 0 else None
    if not mime:
        return None
    size = getattr(msg.file, "size", 0) or 0
    try:
        if size <= limit:
            data = await msg.download_media(file=bytes)
        elif msg.photo:
            data = await msg.download_media(file=bytes, thumb=-2)  # katta rasm — kichikroq nusxasi
        else:
            return None
    except Exception as e:
        log.info("Arxiv: media yuklab olinmadi (%s)", type(e).__name__)
        return None
    if not data or len(data) > limit:
        return None
    if mime == "audio/ogg":  # ovozli xabar: Safari (iPhone) ogg'ni ijro etmaydi — m4a ga o'giramiz
        try:
            import tts
            data, mime = await asyncio.to_thread(tts.to_m4a, data), "audio/mp4"
        except Exception as e:
            log.info("Ovozli xabar m4a ga o'girilmadi (%s) — ogg holida qoladi", type(e).__name__)
    return await storage.archive_media_put(data, mime)


async def _eligible(event) -> tuple[object, str] | None:
    """(chat, tur) yoki None — bu chat arxivlanmaydi."""
    if not runtime.get("archive_enabled"):
        return None
    if event.is_private:
        if event.chat_id in (_me_ids.get(storage.arc_slot(), _me_id), TELEGRAM_SERVICE_ID):
            return None
        chat = await event.get_chat()
        if isinstance(chat, User) and (chat.bot or chat.is_self):
            return None
        return chat, "user"
    # guruhlar: hammasi (ARCHIVE_GROUPS) yoki 👥 Guruhlar'da yoqilganlari — ular arxivda ham turadi
    if event.is_group and (runtime.get("archive_groups") or groups.is_enabled(event.chat_id)):
        return await event.get_chat(), "group"
    if channels.is_broadcast(event) and channels.is_enabled(event.chat_id):   # 📢 faqat belgilangan kanallar
        return await event.get_chat(), "channel"
    return None


async def _store(event) -> None:
    found = await _eligible(event)
    if not found:
        return
    chat, kind = found
    msg = event.message
    sender_name = ""
    if kind == "group" and not msg.out:
        try:
            sender_name = display_name(await event.get_sender())
        except Exception:
            pass
    doc = message_doc(msg, event.chat_id, sender_name)
    if doc["media"]:
        doc["media_id"] = await _save_blob(msg)
        if wants_drive_media(doc, view_once_any=True):
            doc["drive_media"] = "pending"   # fon jarayoni asl faylni Drive'ga yuklaydi
            if doc["view_once"]:
                doc["drive_local"] = await _spool_now(msg, doc)
    added = await storage.archive_add(doc)
    await storage.archive_touch_chat(event.chat_id, display_name(chat) or str(event.chat_id),
                                     getattr(chat, "username", None), kind, doc["date"], int(added),
                                     account_deleted=is_deleted_account(chat))
    if added and doc.get("drive_media"):
        _media_wake.set()


async def _edited(event) -> None:
    found = await _eligible(event)
    if not found:
        return
    msg = event.message
    new_text = msg.message or ""
    old = await storage.archive_edit(event.chat_id, msg.id, new_text, time.time())
    if old is None:   # arxivga tushmagan (masalan, dastur o'chiq paytda kelgan) — hozirgi holati yoziladi
        await _store(event)
        return
    # Reaksiya, pin, tugma o'zgarishi ham "tahrir" bo'lib keladi — faqat matn o'zgarganda xabar beramiz.
    # O'zimizning tahrirlar va guruhlar — ogohlantirishsiz (faqat arxivda).
    changed = (old.get("text") or "") != new_text
    if changed and not old.get("out") and found[1] == "user" and runtime.get("edit_alerts") and _on_edited:
        try:
            await _on_edited(event.chat_id, old, new_text)
        except Exception:
            log.exception("Tahrir haqida ogohlantirish yuborilmadi")


def chat_lines_from(docs: list[dict], title: str, max_chars: int = 12000) -> str:
    """AI uchun yozishma matni: "[19.11 14:32] Siz: ..." — eng yangilaridan boshlab max_chars gacha."""
    tz = ZoneInfo(config.TIMEZONE)
    them = title or "Suhbatdosh"
    lines, size = [], 0
    for d in reversed(docs):
        body = " ".join((d.get("text") or "").split())
        if d.get("media"):
            body = f"[{d['media']}] {body}".strip()
        if not body:
            continue
        who = "Siz" if d.get("out") else (d.get("sender") or them)
        marks = (" (o'chirilgan)" if d.get("deleted") else "") + (" (tahrirlangan)" if d.get("edits") else "")
        line = f"[{_fmt(d.get('date'), '%d.%m %H:%M', tz)}] {who}{marks}: {body[:600]}"
        if size + len(line) > max_chars:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(reversed(lines))


async def chat_lines(chat_id: int, days: int | None = None, max_chars: int = 12000) -> tuple[str, dict, int]:
    """(yozishma matni, chat ma'lumoti, xabarlar soni) — oxirgi `days` kun (None — hammasi)."""
    docs = await storage.archive_messages(chat_id, limit=config.ARCHIVE_EXPORT_LIMIT)
    if days:
        since = time.time() - days * 86400
        docs = [d for d in docs if (d.get("date") or 0) >= since]
    info = await storage.archive_chat(chat_id) or {"chat_id": chat_id, "title": str(chat_id)}
    return chat_lines_from(docs, info.get("title") or ""), info, len(docs)


# ---------- o'chirilganlar ----------
async def _deleted(chat_id: int | None, ids: list[int]) -> None:
    if not ids:
        return
    docs = await storage.archive_mark_deleted(chat_id, ids, time.time())
    now = time.time()
    for d in docs:
        key = (storage.arc_slot(), d["chat_id"])   # ikki akkauntda bir xil chat — alohida ogohlantirish
        _del_buf.setdefault(key, []).append(d)
        _del_last[key] = now
        if key not in _del_tasks:
            _del_tasks[key] = asyncio.create_task(_flush(key))


async def _flush(key) -> None:
    """Chat butunlay o'chirilsa Telegram id larni 100 tadan bo'lib yuboradi — tinchigach bitta xabar."""
    cid = key[1] if isinstance(key, tuple) else key
    try:
        while time.time() - _del_last.get(key, 0) < DELETE_GATHER_SECONDS:
            await asyncio.sleep(DELETE_GATHER_SECONDS / 3)
    finally:
        _del_tasks.pop(key, None)
        docs = _del_buf.pop(key, [])
    if docs and runtime.get("archive_alerts") and _on_deleted:
        try:
            await _on_deleted(cid, docs)
        except Exception:
            log.exception("O'chirilgan xabarlar haqida ogohlantirish yuborilmadi")


def setup(user, me_id: int, on_deleted, on_edited=None) -> None:
    global _on_deleted, _on_edited, _me_id
    _on_deleted, _on_edited, _me_id = on_deleted, on_edited, me_id
    _clients[1] = user
    _me_ids[1] = me_id

    @user.on(events.NewMessage())
    async def archive_new(event):
        try:
            await _store(event)
        except Exception:
            log.exception("Xabar arxivga yozilmadi")

    @user.on(events.MessageEdited())
    async def archive_edited(event):
        try:
            await _edited(event)
        except Exception:
            log.exception("Tahrir arxivga yozilmadi")

    @user.on(events.MessageDeleted())
    async def archive_deleted(event):
        if not runtime.get("archive_enabled"):
            return
        try:
            await _deleted(event.chat_id, list(event.deleted_ids or []))
        except Exception:
            log.exception("O'chirilgan xabarlar belgilanmadi")


def setup_channels(client, slot: int) -> None:
    """2-akkaunt: faqat belgilangan 📢 kanallar postlari arxivga (shaxsiy chatlar arxivi — 1-akkauntda)."""
    _clients[slot] = client

    def selected(e) -> bool:
        return channels.is_broadcast(e) and channels.is_enabled(e.chat_id)

    @client.on(events.NewMessage(func=selected))
    async def _ch_new(event):
        try:
            await _store(event)
            await mark_via(event.chat_id, slot)   # chat yozuvi endi bor — «via» saqlanadi
        except Exception:
            log.exception("Kanal posti arxivga yozilmadi (%s-akkaunt)", slot)

    @client.on(events.MessageEdited(func=selected))
    async def _ch_edited(event):
        try:
            await _edited(event)
        except Exception:
            log.exception("Kanal tahriri arxivga yozilmadi")

    @client.on(events.MessageDeleted())
    async def _ch_deleted(event):
        if not runtime.get("archive_enabled") or not channels.is_enabled(event.chat_id):
            return
        try:
            await _deleted(event.chat_id, list(event.deleted_ids or []))
        except Exception:
            log.exception("O'chirilgan kanal postlari belgilanmadi")


def setup_account(client, me_id: int, slot: int) -> None:
    """2-akkaunt shaxsiy chatlari (va yoqilgan guruhlari) — o'z arxiviga («archive2»), 1-akkauntnikidek:
    o'chirilsa/tahrirlansa xabar beriladi, HTML va Drive'ga ham yuklanadi. Kanallar — setup_channels."""
    _clients[slot] = client
    _me_ids[slot] = me_id
    _arc_slots.add(slot)

    @client.on(events.NewMessage(func=lambda e: not channels.is_broadcast(e)))
    async def _acc_new(event):
        with storage.use_slot(slot):
            try:
                await _store(event)
            except Exception:
                log.exception("Xabar arxivga yozilmadi (%s-akkaunt)", slot)

    @client.on(events.MessageEdited(func=lambda e: not channels.is_broadcast(e)))
    async def _acc_edited(event):
        with storage.use_slot(slot):
            try:
                await _edited(event)
            except Exception:
                log.exception("Tahrir arxivga yozilmadi (%s-akkaunt)", slot)

    @client.on(events.MessageDeleted())
    async def _acc_deleted(event):
        if not runtime.get("archive_enabled") or channels.is_enabled(event.chat_id):
            return   # kanal o'chirishlari — setup_channels'da
        with storage.use_slot(slot):
            try:
                await _deleted(event.chat_id, list(event.deleted_ids or []))
            except Exception:
                log.exception("O'chirilgan xabarlar belgilanmadi (%s-akkaunt)", slot)


def arc_slots() -> list[int]:
    """Shaxsiy chatlar arxivi bor akkauntlar: [1, 2]."""
    return [1] + sorted(_arc_slots)


def forget_client(slot: int) -> None:
    _clients.pop(slot, None)
    _arc_slots.discard(slot)


async def mark_via(chat_id: int, slot: int) -> None:
    """Bu kanal qaysi akkaunt orqali kuzatiladi (1-akkaunt — standart, yozilmaydi)."""
    if slot == 1 or _chat_via.get(chat_id) == slot:
        return
    _chat_via[chat_id] = slot
    try:
        await storage.archive_chat_set(chat_id, via=slot)
    except Exception:
        pass


async def client_for(chat_id: int, default):
    """Chat (kanal) qaysi akkaunt orqali arxivlangan bo'lsa — o'sha akkaunt client'i."""
    if storage.arc_slot() != 1:   # 2-akkaunt arxivi — doim o'sha akkauntdan
        return _clients.get(storage.arc_slot(), default)
    slot = _chat_via.get(chat_id)
    if slot is None:
        try:
            slot = int((await storage.archive_chat(chat_id) or {}).get("via") or 1)
        except Exception:
            slot = 1
        _chat_via[chat_id] = slot
    return _clients.get(slot, default) if slot != 1 else default


# ---------- eksport ----------
BACKFILL_BATCH = 500   # katta chatlar tarixini bo'laklab bazaga yozish (xotirani tejash uchun)


async def _mark_full_synced(chat_id: int) -> None:
    try:
        await storage.archive_chat_set(chat_id, full_synced=True)
    except Exception:
        pass


async def backfill(user, chat_id: int, limit: int | None = None) -> int:
    """Telegram'da hali turgan xabarlarni ham arxivga qo'shadi (arxiv yoqilishidan oldingilari uchun).
    limit=None — shu vaqtgacha bo'lgan BARCHA xabarlar (500 talik bo'laklarda, xotirani to'ldirmasdan)."""
    user = await client_for(chat_id, user)
    try:
        entity = await user.get_entity(chat_id)
    except Exception as e:
        log.info("Chat %s Telegram'dan olinmadi (%s) — faqat arxivdagilar", chat_id, type(e).__name__)
        return 0
    group = not isinstance(entity, User)
    kind = "user" if not group else ("channel" if getattr(entity, "broadcast", False) else "group")
    docs: list[dict] = []
    added = total = 0
    max_date = 0.0
    completed = True
    try:
        async for m in user.iter_messages(entity, limit=limit):
            if isinstance(m, Message):
                doc = _as_history(message_doc(m, chat_id, display_name(m.sender) if group and not m.out else ""))
                if doc["date"] > max_date:
                    max_date = doc["date"]
                docs.append(doc)
                total += 1
                if len(docs) >= BACKFILL_BATCH:
                    added += await storage.archive_add_many(docs)
                    docs.clear()
                    await asyncio.sleep(0.15)
    except Exception as e:
        completed = False
        log.info("Chat %s tarixi to'liq o'qilmadi (%s)", chat_id, type(e).__name__)
    if docs:
        added += await storage.archive_add_many(docs)
        docs.clear()
    if total or await storage.archive_chat(chat_id) is None:
        await storage.archive_touch_chat(chat_id, display_name(entity) or str(chat_id),
                                         getattr(entity, "username", None), kind,
                                         max_date, added,
                                         account_deleted=is_deleted_account(entity))
    if completed and limit is None:
        await _mark_full_synced(chat_id)
    return added


def _as_history(doc: dict) -> dict:
    """Tarixdan olingan xabar: rasm/video bo'lsa — Drive navbatiga (yangi kelganlardan keyin) qo'yiladi."""
    if doc.get("media") and config.DRIVE_MEDIA_HISTORY and wants_drive_media(doc):
        doc["drive_media"] = "history"
    return doc


def _dialog_kind(dialog) -> str | None:
    """Nusxalanadigan chat turi yoki None."""
    ent = dialog.entity
    me_id = _me_ids.get(storage.arc_slot(), _me_id)
    if dialog.is_user:
        if not isinstance(ent, User) or ent.bot or ent.is_self or ent.id in (TELEGRAM_SERVICE_ID, me_id):
            return None
        return "user"
    if dialog.is_group and (runtime.get("archive_groups") or groups.is_enabled(dialog.id)):
        return "group"
    # 2-akkaunt kanallari 1-akkaunt arxivida (via=2) saqlanadi — sync_selected_channels ularni alohida oladi
    if storage.arc_slot() == 1 and channels.is_broadcast(dialog) and channels.is_enabled(dialog.id):
        return "channel"
    return None


async def sync_existing(user, per_chat: int, pause: float = 0.5, full: bool = False) -> tuple[int, int]:
    """Mavjud chatlardagi yozishmalarni arxivga nusxalaydi (faqat matn va media nomi, fayllar yuklanmaydi).
    - full=True (masalan 2-akkaunt) yoki belgilangan kanal/guruhlarda: birinchi marta shu vaqtgacha bo'lgan
      BARCHA xabarlar (500 talik bo'laklarda) olinadi va full_synced=True belgilanadi.
    - Keyingi ishga tushishlarda (yoki full=False oddiy chatlarda): faqat oxirgi xabardan keyin kelganlari."""
    chats = added_total = 0
    async for dialog in user.iter_dialogs():
        if not runtime.get("archive_enabled"):
            break
        kind = _dialog_kind(dialog)
        if kind is None or dialog.message is None:
            continue
        chat_id = dialog.id
        try:
            need_full = (full
                         or (kind == "group" and groups.is_enabled(chat_id))
                         or (kind == "channel" and channels.is_enabled(chat_id)))
            info = await storage.archive_chat(chat_id) if need_full else None
            do_full = need_full and not (info and info.get("full_synced"))
            last = await storage.archive_last_msg_id(chat_id)
            if not do_full and last and dialog.message.id <= last:
                continue  # yangi xabar yo'q
            req_limit = None if do_full else per_chat
            req_min = 0 if do_full else last
            docs: list[dict] = []
            added = total = 0
            max_date = 0.0
            async for m in user.iter_messages(dialog.entity, limit=req_limit, min_id=req_min):
                if isinstance(m, Message):
                    doc = _as_history(message_doc(
                        m, chat_id, display_name(m.sender) if kind == "group" and not m.out else ""))
                    if doc["date"] > max_date:
                        max_date = doc["date"]
                    docs.append(doc)
                    total += 1
                    if len(docs) >= BACKFILL_BATCH:
                        added += await storage.archive_add_many(docs)
                        docs.clear()
                        await asyncio.sleep(0.15)
            if docs:
                added += await storage.archive_add_many(docs)
                docs.clear()
            if total:
                await storage.archive_touch_chat(chat_id, display_name(dialog.entity) or str(chat_id),
                                                 getattr(dialog.entity, "username", None), kind,
                                                 max_date, added,
                                                 account_deleted=is_deleted_account(dialog.entity))
            if do_full:
                await _mark_full_synced(chat_id)
            if added:
                chats += 1
                added_total += added
        except Exception as e:
            log.warning("Chat %s nusxalanmadi (%s)", chat_id, type(e).__name__)
        await asyncio.sleep(pause)  # Telegram cheklovi (FloodWait) ga tushmaslik uchun
    log.info("Arxiv: mavjud chatlardan %d ta xabar nusxalandi (%d ta chat)", added_total, chats)
    return chats, added_total


async def sync_selected_channels(user, slot: int, pause: float = 0.5) -> tuple[int, int]:
    """2-akkauntdagi belgilangan 📢 kanallarning shu vaqtgacha bo'lgan barcha postlarini arxivga (slot 1, via=slot)."""
    if not runtime.get("archive_enabled") or not channels.enabled_count():
        return 0, 0
    chats = added_total = 0
    with storage.use_slot(1):
        async for dialog in user.iter_dialogs():
            if not runtime.get("archive_enabled"):
                break
            if not (channels.is_broadcast(dialog) and channels.is_enabled(dialog.id)) or dialog.message is None:
                continue
            chat_id = dialog.id
            try:
                info = await storage.archive_chat(chat_id)
                do_full = not (info and info.get("full_synced"))
                last = await storage.archive_last_msg_id(chat_id)
                if not do_full and last and dialog.message.id <= last:
                    continue
                docs: list[dict] = []
                added = total = 0
                max_date = 0.0
                async for m in user.iter_messages(dialog.entity, limit=None if do_full else config.ARCHIVE_SYNC_LIMIT,
                                                  min_id=0 if do_full else last):
                    if isinstance(m, Message):
                        doc = _as_history(message_doc(m, chat_id, ""))
                        if doc["date"] > max_date:
                            max_date = doc["date"]
                        docs.append(doc)
                        total += 1
                        if len(docs) >= BACKFILL_BATCH:
                            added += await storage.archive_add_many(docs)
                            docs.clear()
                            await asyncio.sleep(0.15)
                if docs:
                    added += await storage.archive_add_many(docs)
                    docs.clear()
                if total:
                    await storage.archive_touch_chat(chat_id, display_name(dialog.entity) or str(chat_id),
                                                     getattr(dialog.entity, "username", None), "channel",
                                                     max_date, added,
                                                     account_deleted=is_deleted_account(dialog.entity))
                    await mark_via(chat_id, slot)
                if do_full:
                    await _mark_full_synced(chat_id)
                if added:
                    chats += 1
                    added_total += added
            except Exception as e:
                log.warning("Kanal %s nusxalanmadi (%s-akkaunt, %s)", chat_id, slot, type(e).__name__)
            await asyncio.sleep(pause)
    return chats, added_total


async def _fetch_media_budget(docs: list[dict], budget: int) -> tuple[dict[str, tuple[bytes, str]], set[int]]:
    """HTML sig'imiga (budget) kiradigan media'ni kichik bo'laklarda bazadan oladi (xotirani tejash uchun).
    Tartib: avval o'chirilganlar, keyin yengil media (rasm/ovoz/stiker/GIF), so'ngra video-xabar/audio."""
    ordered = sorted(
        (d for d in docs if d.get("media") and d.get("media_id")),
        key=lambda x: (not x.get("deleted"), x.get("media") not in LIGHT_EMBED_KINDS, -(x.get("date") or 0)),
    )
    media: dict[str, tuple[bytes, str]] = {}
    embed: set[int] = set()
    left = budget
    for i in range(0, len(ordered), 20):
        if left <= 0:
            break
        batch = ordered[i:i + 20]
        need_ids = list(dict.fromkeys(d["media_id"] for d in batch if d["media_id"] not in media))
        fetched = await storage.archive_media_get(need_ids) if need_ids else {}
        for d in batch:
            mid = d["media_id"]
            blob = media.get(mid) or fetched.get(mid)
            if blob and len(blob[0]) <= left:
                left -= len(blob[0])
                embed.add(d["msg_id"])
                media[mid] = blob
    return media, embed


async def _render(chat_id: int, only_deleted: bool) -> tuple[bytes, dict, int]:
    docs = await storage.archive_messages(chat_id, only_deleted, limit=config.ARCHIVE_EXPORT_LIMIT)
    info = await storage.archive_chat(chat_id) or {"chat_id": chat_id, "title": str(chat_id)}
    media, embed = await _fetch_media_budget(docs, HTML_MEDIA_BUDGET)
    data = await asyncio.to_thread(lambda: build_html(info, docs, media, only_deleted, embed=embed).encode("utf-8"))
    return data, info, len(docs)


async def _render_to_file(chat_id: int, only_deleted: bool, path: str) -> tuple[dict, int]:
    """Chat HTML'ini to'g'ridan-to'g'ri diskdagi faylga yozadi (katta chatlarda xotirani tejash uchun)."""
    docs = await storage.archive_messages(chat_id, only_deleted, limit=config.ARCHIVE_EXPORT_LIMIT)
    info = await storage.archive_chat(chat_id) or {"chat_id": chat_id, "title": str(chat_id)}
    media, embed = await _fetch_media_budget(docs, HTML_MEDIA_BUDGET)
    n = len(docs)

    def _write() -> None:
        with open(path, "w", encoding="utf-8", buffering=65536) as f:
            for chunk in _iter_html(info, docs, media, only_deleted, embed=embed):
                f.write(chunk)

    await asyncio.to_thread(_write)
    del docs, media, embed
    gc.collect()
    return info, n


def _safe(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title or "").strip("_")[:40] or "chat"


async def fill_media(user, chat_id: int) -> int:
    """Arxivda faqat nomi bor rasm/ovozli/video-xabarlarni Telegram'dan yuklab, arxivga saqlaydi — HTML'da
    ko'rinadi va keyin xabar o'chirilsa ham qoladi. Avval barcha rasm va ovozlar (yengil), keyin og'ir
    video-xabar/audio — HTML sig'imigacha. Saqlanganlar soni."""
    user = await client_for(chat_id, user)
    docs = await storage.archive_messages(chat_id, limit=config.ARCHIVE_EXPORT_LIMIT)
    need = [d for d in docs
            if d.get("media") in EMBED_KINDS and not d.get("media_id") and not d.get("deleted") and not d.get("media_tried")]
    need.sort(key=lambda d: (d.get("media") not in LIGHT_EMBED_KINDS, -(d.get("date") or 0)))
    if not need or config.EXPORT_MEDIA_MAX_KB <= 0:
        return 0
    try:
        entity = await user.get_input_entity(chat_id)
    except Exception:
        entity = await user.get_entity(chat_id)
    total, saved = 0, 0
    for i in range(0, len(need), 100):
        chunk = need[i:i + 100]
        msgs = await user.get_messages(entity, ids=[d["msg_id"] for d in chunk])
        for d, m in zip(chunk, msgs):
            if total >= HTML_MEDIA_BUDGET:
                break
            media_id = await _save_blob(m, config.EXPORT_MEDIA_MAX_KB) if m is not None and m.media else None
            if media_id:
                await storage.archive_msg_set(chat_id, d["msg_id"], media_id=media_id)
                total += min(getattr(m.file, "size", 0) or 0, config.EXPORT_MEDIA_MAX_KB * 1024)
                saved += 1
            else:  # o'chirilgan yoki juda katta — keyingi safar qayta urinmaymiz
                await storage.archive_msg_set(chat_id, d["msg_id"], media_tried=True)
        if total >= HTML_MEDIA_BUDGET:
            break
    if saved:
        log.info("Chat %s: %d ta rasm/ovozli xabar arxivga saqlandi", chat_id, saved)
        await storage.archive_chat_set(chat_id, changed=time.time(), drive_dirty=True)  # Drive'dagi HTML ham
    return saved


async def export_html(user, chat_id: int, only_deleted: bool = False) -> tuple[bytes, str, int]:
    """(HTML fayl, fayl nomi, xabarlar soni)."""
    if not only_deleted:
        await backfill(user, chat_id, config.ARCHIVE_EXPORT_LIMIT)
        try:
            await fill_media(user, chat_id)
        except Exception as e:
            log.warning("Chat %s media'si yuklab olinmadi (%s) — HTML media'siz chiqadi", chat_id, type(e).__name__)
    data, info, n = await _render(chat_id, only_deleted)
    stamp = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d_%H-%M")
    name = f"{'ochirilgan_' if only_deleted else 'chat_'}{_safe(info.get('title'))}_{stamp}.html"
    return data, name, n


# ---------- Google Drive ----------
DRIVE_BATCH = 50
_drive_lock = asyncio.Lock()
_drive_wake = asyncio.Event()
drive_state = {"ok": None, "problem": "", "last": 0.0, "files": 0}


def _drive_base(info: dict) -> str:
    title = info.get("title") or str(info.get("chat_id"))
    user = f" (@{info['username']})" if info.get("username") else ""
    acc = f" [{storage.arc_slot()}-akk]" if storage.arc_slot() != 1 else ""   # 2-akkaunt papkasi alohida
    return f"{_safe(title)}{user} [{info.get('chat_id')}]{acc}"


def _drive_name(info: dict) -> str:
    return _drive_base(info) + ".html"


def _link(res: dict) -> str:
    return res.get("webViewLink") or f"https://drive.google.com/file/d/{res['id']}/view"


def _fatal(e) -> bool:
    """Sozlamadagi xato (token, ruxsat, joy tugagan) — boshqa fayllar ham yuklanmaydi."""
    return e.status in (401, 403) or "invalid_grant" in e.reason or "invalid_client" in e.reason


async def _chat_folder(chat_id: int, info: dict | None = None, fresh: bool = False) -> str:
    """Har bir chatning Drive'dagi papkasi: <asosiy papka>/<Ism (@user) [id]>/ — HTML va media shu yerda."""
    import gdrive
    info = info or await storage.archive_chat(chat_id) or {"chat_id": chat_id}
    if info.get("drive_folder_id") and not fresh:
        return info["drive_folder_id"]
    folder = await gdrive.ensure_folder(_drive_base({**info, "chat_id": chat_id}))
    await storage.archive_chat_set(chat_id, drive_folder_id=folder)
    return folder


async def _in_folder(chat_id: int, info: dict | None, upload):
    """upload(folder) ni bajaradi; chat papkasi Drive'da o'chirib yuborilgan bo'lsa (404) — qayta yaratadi."""
    import gdrive
    folder = await _chat_folder(chat_id, info)
    try:
        return await upload(folder)
    except gdrive.DriveError as e:
        if e.status != 404:
            raise
        log.info("Chat %s papkasi Drive'da topilmadi — qayta yaratiladi", chat_id)
        return await upload(await _chat_folder(chat_id, info, fresh=True))


async def drive_sync_chat(chat_id: int, info: dict | None = None) -> str:
    """Bitta chatni Drive'ga yuklaydi (bor bo'lsa — o'sha faylni yangilaydi). Fayl havolasini qaytaradi."""
    import gdrive
    info = info or await storage.archive_chat(chat_id) or {"chat_id": chat_id}
    changed = info.get("changed")
    tmp = tempfile.mkdtemp(prefix="anna_html_")
    path = os.path.join(tmp, "chat.html")
    try:
        async with _drive_lock:
            cli = _clients.get(storage.arc_slot()) or _clients.get(1)
            if cli is not None:
                try:
                    await fill_media(cli, chat_id)
                except Exception as e:
                    log.debug("Drive HTML media to'ldirilmadi (%s)", type(e).__name__)
            info2, _ = await _render_to_file(chat_id, False, path)
            res = await _in_folder(chat_id, info, lambda folder: gdrive.upload(
                _drive_name(info2), mime="text/html", file_id=info.get("drive_file_id"),
                parent=folder, path=path))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        gc.collect()
    link = _link(res)
    await storage.archive_drive_done(chat_id, changed, res["id"], link)
    return link


# ---------- media (rasm, video, ovozli xabar) -> Drive ----------
DRIVE_MEDIA_KINDS = {"rasm", "video", "video-xabar", "ovozli xabar", "GIF", "audio"}
MEDIA_BATCH = 20
MEDIA_ATTEMPTS = 3
MEDIA_ATTEMPTS_SPOOLED = 10
_media_wake = asyncio.Event()


def _spool_dir() -> str:
    import os
    import tempfile
    return os.path.join(tempfile.gettempdir(), "anna_drive_spool")


async def _spool_now(msg, doc: dict) -> str | None:
    """O'chib ketadigan (bir marta ko'riladigan / taymerli) media: navbatni kutmasdan DARHOL diskka olinadi —
    kimdir uni ochishi bilan Telegram faylni o'chiradi. Fayl yo'li yoki None."""
    import os
    import shutil
    size = getattr(msg.file, "size", 0) or 0
    if not size or size > config.DRIVE_MEDIA_MAX_MB * 1024 * 1024:
        return None
    folder = os.path.join(_spool_dir(), f"{doc['chat_id']}_{doc['msg_id']}")
    os.makedirs(folder, exist_ok=True)
    try:
        path = await msg.download_media(file=folder + os.sep)
    except Exception as e:
        log.warning("O'chib ketadigan media yuklab olinmadi (%s)", type(e).__name__)
        path = None
    if not path:
        shutil.rmtree(folder, ignore_errors=True)
        return None
    log.info("O'chib ketadigan media saqlab olindi: chat %s, xabar %s", doc["chat_id"], doc["msg_id"])
    return path
media_state = {"ok": None, "problem": "", "done": 0}


def wants_drive_media(doc: dict, view_once_any: bool = False) -> bool:
    """Bu xabardagi media Drive'ga yuklanadimi. O'chib ketadigan media — turidan qat'i nazar, doim."""
    import gdrive
    if not (gdrive.enabled() and runtime.get("drive_media")):
        return False
    if view_once_any and doc.get("view_once") and doc.get("media"):
        return True
    kind, mime = doc.get("media"), doc.get("mime") or ""
    if kind in DRIVE_MEDIA_KINDS:
        return True
    return kind == "fayl" and (config.DRIVE_MEDIA_FILES or mime.startswith(("image/", "video/")))


def _media_name(doc: dict, msg, path: str) -> str:
    import os
    stamp = datetime.fromtimestamp(doc["date"], ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d_%H-%M-%S")
    who = "men" if doc.get("out") else "u"
    once = "_bir-martalik" if doc.get("view_once") else ""
    original = doc.get("file_name") or os.path.basename(path)
    return f"{stamp}_{who}_{doc['msg_id']}{once}_{original}"


async def _upload_media(user, doc: dict) -> str:
    """Bitta mediani Telegram'dan yuklab olib, Drive'dagi chat papkasiga joylaydi. Natija holati."""
    import os
    import shutil
    import tempfile
    import gdrive
    import mimetypes
    cid, mid = doc["chat_id"], doc["msg_id"]
    user = await client_for(cid, user)   # 2-akkaunt kanali — media o'sha akkauntdan olinadi
    local = doc.get("drive_local")
    if local and os.path.isfile(local):
        # o'chib ketadigan media — kelgan zahoti diskka olingan, Telegram'dan qayta so'ralmaydi
        tmp, path, mime = os.path.dirname(local), local, doc.get("mime") or ""
    else:
        msg = await user.get_messages(cid, ids=mid)
        if msg is None or not msg.media or getattr(msg.file, "size", None) is None:
            await storage.archive_msg_set(cid, mid, drive_media="lost")  # yuklashga ulgurmay o'chirilgan
            return "lost"
        if (msg.file.size or 0) > config.DRIVE_MEDIA_MAX_MB * 1024 * 1024:
            await storage.archive_msg_set(cid, mid, drive_media="too_big")
            return "too_big"
        tmp = tempfile.mkdtemp(prefix="anna_drive_")
        try:
            path = await msg.download_media(file=tmp + os.sep)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        mime = getattr(msg.file, "mime_type", None) or doc.get("mime") or ""
        if not path:
            shutil.rmtree(tmp, ignore_errors=True)
            await storage.archive_msg_set(cid, mid, drive_media="lost")
            return "lost"
    mime = mime or mimetypes.guess_type(path)[0] or "application/octet-stream"
    name = _media_name(doc, None, path)
    spooled = path == local
    try:
        res = await _in_folder(cid, None, lambda folder: gdrive.upload(name, mime=mime, parent=folder, path=path))
    except BaseException:
        if not spooled:  # oddiy media keyin Telegram'dan qayta olinadi; o'chib ketadiganini esa saqlab turamiz
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(tmp, ignore_errors=True)
    await storage.archive_media_done(cid, mid, res["id"], _link(res))
    return "done"


async def drive_media_pending(user) -> int:
    """Navbatdagi mediani Drive'ga yuklaydi. Yuklanganlar soni."""
    import gdrive
    done = 0
    while runtime.get("drive_media"):
        rows = await storage.archive_media_pending(MEDIA_BATCH)
        if not rows:
            break
        retry_later = False
        for d in rows:
            try:
                if await _upload_media(user, d) == "done":
                    done += 1
                    media_state["done"] += 1
            except gdrive.DriveError as e:
                if _fatal(e):
                    raise
                retry_later |= await _media_failed(d, gdrive.explain(e))
            except Exception as e:
                retry_later |= await _media_failed(d, type(e).__name__)
        if retry_later or len(rows) < MEDIA_BATCH:
            break
    return done


async def _media_failed(doc: dict, reason: str) -> bool:
    """Xato: MEDIA_ATTEMPTS martagacha keyinroq qayta urinadi. Keyin urinish kerakmi — True."""
    import os
    import shutil
    attempts = doc.get("drive_attempts", 0) + 1
    log.warning("Media %s/%s Drive'ga yuklanmadi (%s), urinish %d", doc["chat_id"], doc["msg_id"], reason, attempts)
    local = doc.get("drive_local")
    # o'chib ketadigan media'ni Telegram'dan qayta olib bo'lmaydi — unga ko'proq imkoniyat
    final = attempts >= (MEDIA_ATTEMPTS_SPOOLED if local else MEDIA_ATTEMPTS)
    await storage.archive_msg_set(doc["chat_id"], doc["msg_id"], drive_attempts=attempts,
                                  drive_error=reason, **({"drive_media": "failed"} if final else {}))
    if final and local:
        shutil.rmtree(os.path.dirname(local), ignore_errors=True)  # disk to'lib qolmasin
    return not final


async def drive_media_loop(user, alert) -> None:
    """Fon vazifasi: yangi rasm/video/ovozli xabarlar kelishi bilan Drive'ga yuklanadi."""
    import gdrive
    await asyncio.sleep(40)
    if config.DRIVE_MEDIA_HISTORY and runtime.get("drive_media"):
        try:  # oldin arxivlangan (mavjud chatlardan nusxalangan) rasm/videolar ham navbatga
            n = await storage.archive_media_mark_history(sorted(DRIVE_MEDIA_KINDS), config.DRIVE_MEDIA_FILES)
            if n:
                log.info("Google Drive: arxivdagi %d ta eski rasm/video navbatga qo'yildi", n)
        except Exception as e:
            log.warning("Eski media navbatga qo'yilmadi (%s)", type(e).__name__)
    alerted = ""
    while True:
        wait = 120
        try:
            n = 0
            for slot in arc_slots():   # har bir akkaunt arxivi
                with storage.use_slot(slot):
                    n += await drive_media_pending(user)
            media_state.update(ok=True, problem="")
            if n:
                log.info("Google Drive: %d ta media yuklandi", n)
            if alerted:
                alerted = ""
                await alert("☁️ Media yana Google Drive'ga yuklanmoqda.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reason = gdrive.explain(e)
            media_state.update(ok=False, problem=reason)
            log.warning("Google Drive (media): %s", reason)
            wait = 600
            if reason != alerted:
                alerted = reason
                if not gdrive.is_auth_error(e):   # token o'lgan bo'lsa — gdrive bitta umumiy xabar beradi
                    await alert(f"☁️❌ Rasm/videolar Google Drive'ga yuklanmadi: {reason}")
        try:
            await asyncio.wait_for(_media_wake.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        _media_wake.clear()


async def drive_sync_pending() -> int:
    """O'zgargan barcha chatlarni yuklaydi. Yuklanganlar soni."""
    import gdrive
    done = 0
    while True:
        rows = await storage.archive_drive_pending(DRIVE_BATCH)
        if not rows:
            break
        progressed = 0
        for info in rows:
            try:
                await drive_sync_chat(info["chat_id"], info)
                done += 1
                progressed += 1
            except gdrive.DriveError as e:
                if _fatal(e) or e.status == 404:
                    raise  # sozlamada xato (asosiy papka topilmadi va h.k.) — qolgan chatlar ham yuklanmaydi
                log.warning("Chat %s Drive'ga yuklanmadi: %s", info["chat_id"], gdrive.explain(e))
            except Exception as e:
                log.warning("Chat %s Drive'ga yuklanmadi (%s)", info["chat_id"], type(e).__name__)
        if progressed == 0 or len(rows) < DRIVE_BATCH:
            break
    return done


def media_wake() -> None:
    """Drive media siklini darhol uyg'otadi (masalan, Drive qayta ulanganda)."""
    _media_wake.set()


def drive_wake() -> None:
    """Drive siklini darhol ishga tushirish (masalan, botdagi «Hozir yuklash» tugmasi)."""
    _drive_wake.set()


async def drive_loop(alert) -> None:
    """Fon vazifasi: har DRIVE_SYNC_MINUTES da o'zgargan chatlar Drive'ga yuklanadi."""
    import gdrive
    await asyncio.sleep(30)
    await storage.archive_drive_mark_all(only_missing=True)  # Drive'da hali nusxasi yo'q chatlar
    alerted = ""
    while True:
        try:
            n = 0
            for slot in arc_slots():
                with storage.use_slot(slot):
                    n += await drive_sync_pending()
            drive_state.update(ok=True, problem="", last=time.time())
            drive_state["files"] += n
            if n:
                log.info("Google Drive: %d ta chat yuklandi", n)
            if alerted:
                alerted = ""
                await alert("☁️ Google Drive bilan aloqa tiklandi — arxiv yana yuklanmoqda.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reason = gdrive.explain(e)
            drive_state.update(ok=False, problem=reason)
            log.warning("Google Drive: %s", reason)
            if reason != alerted:   # bir xil xato haqida bir marta
                alerted = reason
                if not gdrive.is_auth_error(e):
                    await alert(f"☁️❌ Chat arxivi Google Drive'ga yuklanmadi: {reason}")
        try:
            await asyncio.wait_for(_drive_wake.wait(), timeout=config.DRIVE_SYNC_MINUTES * 60)
        except asyncio.TimeoutError:
            pass
        _drive_wake.clear()


_ICONS = {"rasm": "📸", "video": "🎬", "ovozli xabar": "🎤", "video-xabar": "📹", "audio": "🎵", "fayl": "📎",
          "stiker": "🧩", "GIF": "🎞", "lokatsiya": "📍", "kontakt": "👤", "so'rovnoma": "📊"}

_CSS = """
*{box-sizing:border-box}body{margin:0;background:#0e1621;color:#e9eef3;font:15px/1.45 -apple-system,Segoe UI,
Roboto,Arial,sans-serif}header{position:sticky;top:0;background:#17212b;padding:14px 18px;border-bottom:1px solid
#0b1218;z-index:2}h1{margin:0;font-size:18px}.sub{color:#8b9ba8;font-size:13px;margin-top:3px}
.legend span{display:inline-block;margin-right:12px}main{max-width:820px;margin:0 auto;padding:12px}
.day{text-align:center;margin:16px 0 8px}.day span{background:#1e2c3a;color:#aab8c4;border-radius:12px;
padding:3px 12px;font-size:13px}.row{display:flex;margin:4px 0}.row.out{justify-content:flex-end}
.msg{max-width:78%;background:#182533;border-radius:12px;padding:7px 11px;word-wrap:break-word;
white-space:pre-wrap;position:relative}.out .msg{background:#2b5278}.msg.del{outline:2px solid #e5484d;
background:#3a1d22}.out .msg.del{background:#4a2330}.who{color:#6ab3f3;font-weight:600;font-size:13px}
.meta{color:#8b9ba8;font-size:12px;text-align:right;margin-top:3px;white-space:normal}
.tag{display:inline-block;border-radius:6px;padding:0 6px;font-size:12px;margin-left:6px}
.tdel{background:#e5484d;color:#fff}.tedit{background:#3d4f63;color:#dfe8f0}.tfwd{background:#3d4f63}
.reply{border-left:3px solid #6ab3f3;padding-left:7px;color:#aab8c4;font-size:13px;margin-bottom:4px;
white-space:normal}.media{color:#aab8c4;font-style:italic}img,video{max-width:100%;border-radius:8px;
display:block;margin:3px 0}audio{width:260px;max-width:100%}details{font-size:13px;color:#aab8c4;
white-space:normal}details li{margin:3px 0}footer{color:#6c7883;text-align:center;font-size:12px;padding:20px}
"""


def _fmt(ts: float | None, pattern: str, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime(pattern) if ts else ""


def _text(s: str) -> str:
    return html.escape(s or "")


def _snippet(d: dict | None) -> str:
    if not d:
        return "xabar"
    s = " ".join((d.get("text") or "").split()) or f"[{d.get('media') or 'xabar'}]"
    return s[:90] + ("…" if len(s) > 90 else "")


def _iter_html(info: dict, docs: list[dict], media: dict[str, tuple[bytes, str]], only_deleted: bool,
               embed: set[int] | None = None):
    tz = ZoneInfo(config.TIMEZONE)
    title = info.get("title") or str(info.get("chat_id", ""))
    group = info.get("kind") == "group"
    by_id = {d["msg_id"]: d for d in docs}
    n_del = sum(1 for d in docs if d.get("deleted"))
    n_edit = sum(1 for d in docs if d.get("edits"))
    # Hammasi sig'masa: avval o'chirilganlar, keyin yengil media (rasm/ovoz/stiker/GIF), so'ngra og'irroqlari
    if embed is None:
        embed, budget = set(), HTML_MEDIA_BUDGET
        for d in sorted(docs, key=lambda x: (not x.get("deleted"), x.get("media") not in LIGHT_EMBED_KINDS,
                                             -(x.get("date") or 0))):
            blob = media.get(d.get("media_id") or "") if d.get("media") else None
            if blob and len(blob[0]) <= budget:
                budget -= len(blob[0])
                embed.add(d["msg_id"])

    sub = [f"@{_text(info['username'])}" if info.get("username") else "",
           f"id {_text(str(info.get('chat_id', '')))}",
           f"{len(docs)} ta xabar", f"🗑 {n_del} ta o'chirilgan", f"✏️ {n_edit} ta tahrirlangan"]
    yield (
        "<!DOCTYPE html><html lang='uz'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_text(title)} — chat arxivi</title><style>{_CSS}</style></head><body>"
        f"<header><h1>{'🗑 O‘chirilgan xabarlar — ' if only_deleted else '💬 '}{_text(title)}</h1>"
        f"<div class='sub'>{' · '.join(s for s in sub if s)}</div>"
        "<div class='sub legend'><span><span class='tag tdel'>o‘chirilgan</span></span>"
        "<span><span class='tag tedit'>tahrirlangan</span> — eski variantlari ostida</span></div></header><main>"
    )
    if not docs:
        yield "<p class='day'><span>Arxivda xabar yo‘q</span></p>"
    day = None
    for d in docs:
        dkey = _fmt(d.get("date"), "%d.%m.%Y", tz)
        if dkey != day:
            day = dkey
            yield f"<div class='day'><span>{dkey}</span></div>"
        cls = "msg del" if d.get("deleted") else "msg"
        parts = []
        if group and not d.get("out") and d.get("sender"):
            parts.append(f"<div class='who'>{_text(d['sender'])}</div>")
        if d.get("reply_to"):
            parts.append(f"<div class='reply'>↩️ {_text(_snippet(by_id.get(d['reply_to'])))}</div>")
        if d.get("media"):
            if d["msg_id"] in embed and d.get("media_id") in media:
                data, mime = media[d["media_id"]]
                src = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                if mime.startswith("image/"):
                    parts.append(f"<img src='{src}' alt='rasm' loading='lazy'>")
                elif mime.startswith("audio/"):
                    parts.append(f"<audio controls src='{src}'></audio>")
                else:
                    parts.append(f"<video controls src='{src}'></video>")
            else:
                label = f"{_ICONS.get(d['media'], '📎')} {d['media']}"
                if d.get("file_name"):
                    label += f": {d['file_name']}"
                parts.append(f"<div class='media'>[{_text(label)}]</div>")
            if d.get("drive_media_link"):
                parts.append(f"<div class='media'><a href='{_text(d['drive_media_link'])}' target='_blank' "
                             "rel='noopener' style='color:#6ab3f3'>☁️ Asl fayl Google Drive'da</a></div>")
            if d.get("view_once"):
                parts.append("<div class='media'>🔥 bir marta ko‘riladigan media</div>")
        if d.get("text"):
            parts.append(f"<div>{_text(d['text'])}</div>")
        tags = []
        if d.get("fwd"):
            tags.append("<span class='tag tfwd'>↪️ uzatilgan</span>")
        if d.get("edits"):
            tags.append("<span class='tag tedit'>✏️ tahrirlangan</span>")
        if d.get("deleted"):
            tags.append(f"<span class='tag tdel'>🗑 o‘chirilgan {_fmt(d.get('deleted_at'), '%d.%m %H:%M', tz)}</span>")
        who = "Siz" if d.get("out") else ""
        parts.append(f"<div class='meta'>{who + ' · ' if who else ''}{_fmt(d.get('date'), '%H:%M', tz)}"
                     f"{''.join(tags)}</div>")
        if d.get("edits"):
            items = "".join(f"<li><i>{_fmt(e.get('ts'), '%d.%m %H:%M', tz)}</i> — {_text(e.get('text')) or '(bo‘sh)'}</li>"
                            for e in d["edits"])
            parts.append(f"<details><summary>Oldingi variantlar ({len(d['edits'])})</summary><ul>{items}</ul></details>")
        side = "out" if d.get("out") else "in"
        yield f"<div class='row {side}'><div class='{cls}'>{''.join(parts)}</div></div>"
    made = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    yield f"</main><footer>Anna chat arxivi · {made} da tayyorlandi</footer></body></html>"


def build_html(info: dict, docs: list[dict], media: dict[str, tuple[bytes, str]], only_deleted: bool,
               embed: set[int] | None = None) -> str:
    return "".join(_iter_html(info, docs, media, only_deleted, embed=embed))
