"""👥 Guruh xabarlarini klonlash: akkauntingizdagi guruhlardan tanlanganlariga kelgan xabarlar botga nusxalanadi.

- Botda «👥 Guruhlar» (/guruhlar): akkauntingiz a'zo bo'lgan guruhlar ro'yxati, har biri yonida yoqish tugmasi.
- Yoqilgan guruhga yangi xabar kelsa — bot uni sizga yuboradi: guruh nomi, kim yozgani, matn, rasm/video/
  ovozli xabar/fayl (asl ko'rinishida), qaysi xabarga javob ekani va guruhdagi xabarga havola.
- Tanlov MongoDB'da saqlanadi (qayta ishga tushganda ham yoqilganlar qoladi).

Xabarlar navbat orqali birma-bir yuboriladi — faol guruhda Telegram cheklovi (FloodWait) bo'lsa, kutib davom etadi.
"""
import asyncio
import html
import logging
import time

from telethon import TelegramClient, errors, events

import config
import storage

log = logging.getLogger("groups")

PER_PAGE = 10
CACHE_SECONDS = 300          # guruhlar ro'yxati shuncha vaqt keshda turadi
QUEUE_MAX = 1000             # navbat to'lsa, yangi xabarlar tashlab yuboriladi (logga yoziladi)
SEND_GAP = 0.7               # bot xabarlari orasidagi pauza (Telegram cheklovidan qochish)
MAX_MEDIA_BYTES = config.MAX_MEDIA_MB * 1024 * 1024

_enabled: dict[int, str] = {}        # chat_id -> guruh nomi
_cache: tuple[float, list[dict]] = (0.0, [])
_queue: asyncio.Queue | None = None
_dropped = 0
_send_text = None
_send_media = None


def _q() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=QUEUE_MAX)
    return _queue


def _esc(s) -> str:
    return html.escape(str(s or ""))


async def load() -> None:
    try:
        rows = await storage.group_clone_list()
    except Exception as e:
        log.warning("Klonlanadigan guruhlar o'qilmadi (%s)", type(e).__name__)
        return
    _enabled.clear()
    _enabled.update({int(r["chat_id"]): r.get("title") or "" for r in rows})


def is_enabled(chat_id: int) -> bool:
    return chat_id in _enabled


def enabled_count() -> int:
    return len(_enabled)


async def toggle(chat_id: int, title: str = "") -> bool:
    """Yoqadi/o'chiradi. Yangi holatni qaytaradi (True — klonlanadi)."""
    if chat_id in _enabled:
        _enabled.pop(chat_id, None)
        await storage.group_clone_remove(chat_id)
        return False
    _enabled[chat_id] = title
    await storage.group_clone_add(chat_id, title)
    return True


async def my_groups(user: TelegramClient, refresh: bool = False) -> list[dict]:
    """Akkauntingiz a'zo bo'lgan guruhlar (kanallarsiz): [{"id", "title", "username"}]."""
    global _cache
    ts, cached = _cache
    if cached and not refresh and time.time() - ts < CACHE_SECONDS:
        return cached
    groups = []
    async for d in user.iter_dialogs():
        if d.is_group:
            groups.append({"id": d.id, "title": d.name or str(d.id),
                           "username": getattr(d.entity, "username", None)})
    _cache = (time.time(), groups)
    return groups


def panel(groups: list[dict], page: int = 0) -> tuple[str, list]:
    """Matn va tugmalar ([[(matn, data)]]) — control_bot._inline formatida."""
    # Yoqilganlar tepada, qolganlari Telegram'dagi tartibda
    ordered = [g for g in groups if g["id"] in _enabled] + [g for g in groups if g["id"] not in _enabled]
    pages = max(1, -(-len(ordered) // PER_PAGE))
    page = min(max(0, page), pages - 1)
    chunk = ordered[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [
        "👥 <b>Guruhlar — xabarlarni klonlash</b>", "",
        f"Akkauntingizda {len(groups)} ta guruh, klonlanyapti: <b>{len(_enabled)}</b> ta.",
        "Guruhni bosib yoqing — o'sha guruhga yangi xabar kelsa, bot uni sizga nusxalab yuboradi "
        "(matn, rasm, video, ovozli xabar, fayl).",
    ]
    if not groups:
        lines += ["", "<i>Akkauntingizda guruh topilmadi.</i>"]
    if pages > 1:
        lines += ["", f"Sahifa {page + 1}/{pages}"]
    rows = []
    for g in chunk:
        on = g["id"] in _enabled
        title = g["title"] if len(g["title"]) <= 40 else g["title"][:39] + "…"
        rows.append([(("✅ " if on else "▫️ ") + title, f"grp:t:{g['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"grp:p:{page - 1}"))
    if page < pages - 1:
        nav.append(("➡️", f"grp:p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🔄 Yangilash", f"grp:r:{page}")])
    return "\n".join(lines), rows


def message_link(chat, msg_id: int) -> str | None:
    """Guruhdagi xabarga havola: ochiq guruh — t.me/<username>/<id>, yopiq superguruh — t.me/c/<id>/<id>."""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if getattr(chat, "megagroup", False):
        return f"https://t.me/c/{chat.id}/{msg_id}"
    return None   # oddiy (kichik) guruh xabariga havola bo'lmaydi


def _sender_name(sender) -> tuple[str, str | None]:
    if sender is None:
        return "Nomaʼlum", None
    parts = (getattr(sender, "first_name", None), getattr(sender, "last_name", None))
    name = " ".join(p for p in parts if p) or getattr(sender, "title", None) or "Nomaʼlum"
    return name, getattr(sender, "username", None)


def setup(user: TelegramClient, send_text, send_media) -> None:
    """send_text(text_html, link) va send_media(...) — control_bot'dagi yuboruvchilar."""
    global _send_text, _send_media
    _send_text, _send_media = send_text, send_media

    @user.on(events.NewMessage(incoming=True, func=lambda e: e.is_group and e.chat_id in _enabled))
    async def _on_group_message(event):
        global _dropped
        try:
            _q().put_nowait(event)
        except asyncio.QueueFull:
            _dropped += 1
            if _dropped % 100 == 1:
                log.warning("Guruh klon navbati to'ldi — %d ta xabar tashlab yuborildi", _dropped)


async def clone(event) -> None:
    """Bitta guruh xabarini egasiga nusxalaydi."""
    import userbot   # media turi va fayl nomi uchun umumiy yordamchilar

    msg = event.message
    chat = await event.get_chat()
    group = getattr(chat, "title", None) or _enabled.get(event.chat_id) or str(event.chat_id)
    name, username = _sender_name(await event.get_sender())
    text = msg.message or ""
    link = message_link(chat, msg.id)

    quote = ""
    if msg.is_reply:
        try:
            r = await msg.get_reply_message()
            if r is not None:
                rname, _ = _sender_name(await r.get_sender())
                rtext = (r.message or "").strip() or (f"[{userbot._media_label(r)}]" if r.media else "")
                quote = f"↩️ <i>{_esc(rname)}: {_esc(rtext[:150])}</i>\n"
        except Exception:
            pass

    label = userbot._media_label(msg)
    if label and label != "stiker" and getattr(msg, "file", None):
        size = getattr(msg.file, "size", 0) or 0
        if size <= MAX_MEDIA_BYTES:
            try:
                data = await msg.download_media(file=bytes)
            except Exception as e:
                log.warning("Guruh mediasi yuklab olinmadi (%s)", type(e).__name__)
                data = None
            if data:
                await _send_media(event.chat_id, f"👥 {group} · {name}", data,
                                  userbot._media_filename(msg, label), kind=label, username=username,
                                  caption=text, mime=(getattr(msg.file, "mime_type", None) or "").lower(),
                                  attributes=getattr(getattr(msg.media, "document", None), "attributes", None),
                                  link_reply=False)
                return
        text = (f"[{label}: {size / 1024 / 1024:.0f} MB — yuklanmadi, limit {config.MAX_MEDIA_MB} MB] "
                + text).strip()
    elif label:
        text = (f"[{label}] " + text).strip()
    if not text:
        return   # xizmat xabarlari (kimdir qo'shildi va h.k.)

    who = f"<b>{_esc(name)}</b>" + (f" (@{_esc(username)})" if username else "")
    body = f"👥 <b>{_esc(group)}</b>\n👤 {who}\n{quote}\n{_esc(text[:3500])}"
    await _send_text(body, link)


async def run() -> None:
    """Navbatdagi guruh xabarlarini birma-bir botga yuboradi."""
    await load()
    q = _q()
    while True:
        event = await q.get()
        try:
            await clone(event)
        except errors.FloodWaitError as e:
            log.warning("Guruh klon: Telegram cheklovi %d s — kutaman", e.seconds)
            await asyncio.sleep(e.seconds + 1)
            try:
                await clone(event)
            except Exception:
                log.exception("Guruh xabari klonlanmadi")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Guruh xabari klonlanmadi")
        await asyncio.sleep(SEND_GAP)
