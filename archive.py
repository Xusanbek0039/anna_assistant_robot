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
import html
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import events
from telethon.tl.types import Message, MessageMediaWebPage, PeerChannel, User

import config
import runtime
import storage

log = logging.getLogger("archive")

TELEGRAM_SERVICE_ID = 777000      # login kodlari — hech qachon saqlanmaydi
DELETE_GATHER_SECONDS = 3         # shu vaqt ichida o'chirilganlari bitta ogohlantirishga yig'iladi
HTML_MEDIA_BUDGET = 40 * 1024 * 1024  # HTML ichiga joylanadigan media jami (bot fayl limiti 50 MB)

_on_deleted = None                # async (chat_id, docs) -> None — control_bot ogohlantirishi
_me_id = 0
_del_buf: dict[int, list[dict]] = {}
_del_last: dict[int, float] = {}
_del_tasks: dict[int, asyncio.Task] = {}


# ---------- xabar -> arxiv yozuvi ----------
def display_name(entity) -> str:
    if entity is None:
        return ""
    if isinstance(entity, User):
        return " ".join(p for p in (entity.first_name, entity.last_name) if p) or (entity.username or "")
    return getattr(entity, "title", "") or ""


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


async def _save_blob(msg) -> str | None:
    limit = config.ARCHIVE_MEDIA_MAX_KB * 1024
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
    return await storage.archive_media_put(data, mime)


async def _eligible(event) -> tuple[object, str] | None:
    """(chat, tur) yoki None — bu chat arxivlanmaydi."""
    if not runtime.get("archive_enabled"):
        return None
    if event.is_private:
        if event.chat_id in (_me_id, TELEGRAM_SERVICE_ID):
            return None
        chat = await event.get_chat()
        if isinstance(chat, User) and (chat.bot or chat.is_self):
            return None
        return chat, "user"
    if event.is_group and runtime.get("archive_groups"):
        return await event.get_chat(), "group"
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
    added = await storage.archive_add(doc)
    await storage.archive_touch_chat(event.chat_id, display_name(chat) or str(event.chat_id),
                                     getattr(chat, "username", None), kind, doc["date"], int(added))


async def _edited(event) -> None:
    if not await _eligible(event):
        return
    msg = event.message
    old = await storage.archive_edit(event.chat_id, msg.id, msg.message or "", time.time())
    if old is None:   # arxivga tushmagan (masalan, dastur o'chiq paytda kelgan) — hozirgi holati yoziladi
        await _store(event)


# ---------- o'chirilganlar ----------
async def _deleted(chat_id: int | None, ids: list[int]) -> None:
    if not ids:
        return
    docs = await storage.archive_mark_deleted(chat_id, ids, time.time())
    now = time.time()
    for d in docs:
        cid = d["chat_id"]
        _del_buf.setdefault(cid, []).append(d)
        _del_last[cid] = now
        if cid not in _del_tasks:
            _del_tasks[cid] = asyncio.create_task(_flush(cid))


async def _flush(cid: int) -> None:
    """Chat butunlay o'chirilsa Telegram id larni 100 tadan bo'lib yuboradi — tinchigach bitta xabar."""
    try:
        while time.time() - _del_last.get(cid, 0) < DELETE_GATHER_SECONDS:
            await asyncio.sleep(DELETE_GATHER_SECONDS / 3)
    finally:
        _del_tasks.pop(cid, None)
        docs = _del_buf.pop(cid, [])
    if docs and runtime.get("archive_alerts") and _on_deleted:
        try:
            await _on_deleted(cid, docs)
        except Exception:
            log.exception("O'chirilgan xabarlar haqida ogohlantirish yuborilmadi")


def setup(user, me_id: int, on_deleted) -> None:
    global _on_deleted, _me_id
    _on_deleted, _me_id = on_deleted, me_id

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


# ---------- eksport ----------
async def backfill(user, chat_id: int, limit: int) -> int:
    """Telegram'da hali turgan xabarlarni ham arxivga qo'shadi (arxiv yoqilishidan oldingilari uchun)."""
    try:
        entity = await user.get_entity(chat_id)
    except Exception as e:
        log.info("Chat %s Telegram'dan olinmadi (%s) — faqat arxivdagilar", chat_id, type(e).__name__)
        return 0
    group = not isinstance(entity, User)
    docs = []
    try:
        async for m in user.iter_messages(entity, limit=limit):
            if isinstance(m, Message):
                docs.append(message_doc(m, chat_id, display_name(m.sender) if group and not m.out else ""))
    except Exception as e:
        log.info("Chat %s tarixi o'qilmadi (%s)", chat_id, type(e).__name__)
    added = await storage.archive_add_many(docs)
    if docs or await storage.archive_chat(chat_id) is None:
        await storage.archive_touch_chat(chat_id, display_name(entity) or str(chat_id),
                                         getattr(entity, "username", None), "group" if group else "user",
                                         max((d["date"] for d in docs), default=0), added)
    return added


async def export_html(user, chat_id: int, only_deleted: bool = False) -> tuple[bytes, str, int]:
    """(HTML fayl, fayl nomi, xabarlar soni)."""
    if not only_deleted:
        await backfill(user, chat_id, config.ARCHIVE_EXPORT_LIMIT)
    docs = await storage.archive_messages(chat_id, only_deleted, limit=config.ARCHIVE_EXPORT_LIMIT)
    info = await storage.archive_chat(chat_id) or {"title": str(chat_id)}
    media = await storage.archive_media_get([d["media_id"] for d in docs if d.get("media_id")])
    page = build_html(info, docs, media, only_deleted)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", info.get("title") or str(chat_id)).strip("_")[:40] or "chat"
    stamp = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d_%H-%M")
    name = f"{'ochirilgan_' if only_deleted else 'chat_'}{safe}_{stamp}.html"
    return page.encode("utf-8"), name, len(docs)


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


def build_html(info: dict, docs: list[dict], media: dict[str, tuple[bytes, str]], only_deleted: bool) -> str:
    tz = ZoneInfo(config.TIMEZONE)
    title = info.get("title") or str(info.get("chat_id", ""))
    group = info.get("kind") == "group"
    by_id = {d["msg_id"]: d for d in docs}
    n_del = sum(1 for d in docs if d.get("deleted"))
    n_edit = sum(1 for d in docs if d.get("edits"))
    budget = HTML_MEDIA_BUDGET

    sub = [f"@{_text(info['username'])}" if info.get("username") else "",
           f"id {_text(str(info.get('chat_id', '')))}",
           f"{len(docs)} ta xabar", f"🗑 {n_del} ta o'chirilgan", f"✏️ {n_edit} ta tahrirlangan"]
    out = [
        "<!DOCTYPE html><html lang='uz'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_text(title)} — chat arxivi</title><style>{_CSS}</style></head><body>",
        f"<header><h1>{'🗑 O‘chirilgan xabarlar — ' if only_deleted else '💬 '}{_text(title)}</h1>",
        f"<div class='sub'>{' · '.join(s for s in sub if s)}</div>",
        "<div class='sub legend'><span><span class='tag tdel'>o‘chirilgan</span></span>"
        "<span><span class='tag tedit'>tahrirlangan</span> — eski variantlari ostida</span></div></header><main>",
    ]
    if not docs:
        out.append("<p class='day'><span>Arxivda xabar yo‘q</span></p>")
    day = None
    for d in docs:
        dkey = _fmt(d.get("date"), "%d.%m.%Y", tz)
        if dkey != day:
            day = dkey
            out.append(f"<div class='day'><span>{dkey}</span></div>")
        cls = "msg del" if d.get("deleted") else "msg"
        parts = []
        if group and not d.get("out") and d.get("sender"):
            parts.append(f"<div class='who'>{_text(d['sender'])}</div>")
        if d.get("reply_to"):
            parts.append(f"<div class='reply'>↩️ {_text(_snippet(by_id.get(d['reply_to'])))}</div>")
        if d.get("media"):
            blob = media.get(d.get("media_id") or "")
            if blob and len(blob[0]) <= budget:
                budget -= len(blob[0])
                data, mime = blob
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
        out.append(f"<div class='row {side}'><div class='{cls}'>{''.join(parts)}</div></div>")
    made = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    out.append(f"</main><footer>Anna chat arxivi · {made} da tayyorlandi</footer></body></html>")
    return "".join(out)
