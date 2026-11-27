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
import runtime
import storage

log = logging.getLogger("groups")

PER_PAGE = 10
CACHE_SECONDS = 300          # guruhlar ro'yxati shuncha vaqt keshda turadi
QUEUE_MAX = 1000             # navbat to'lsa, yangi xabarlar tashlab yuboriladi (logga yoziladi)
SEND_GAP = 0.7               # bot xabarlari orasidagi pauza (Telegram cheklovidan qochish)
MAX_MEDIA_BYTES = config.MAX_MEDIA_MB * 1024 * 1024

_enabled: dict[int, str] = {}        # chat_id -> guruh nomi
_cache: dict = {}   # akkaunt (None — hammasi) -> (vaqt, guruhlar)
_queue: asyncio.Queue | None = None
_dropped = 0
_send_text = None
_send_media = None
_clients: dict[int, TelegramClient] = {}   # akkaunt raqami -> client (guruhlar ikkala akkauntdan)
_seen: dict[tuple[int, int], None] = {}    # (guruh, xabar) — ikkala akkaunt bir guruhda bo'lsa, bir marta
SEEN_MAX = 2000


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


async def my_groups(refresh: bool = False, only: int | None = None) -> list[dict]:
    """Akkaunt(lar)ingiz a'zo bo'lgan guruhlar (kanallarsiz): [{"id", "title", "username", "slots"}].
    only — faqat shu akkaunt guruhlari. Hammasi bo'lsa, faqat 2-akkauntdagi guruh nomi yonida «· 2-akk»."""
    ts, cached = _cache.get(only, (0.0, []))
    if cached and not refresh and time.time() - ts < CACHE_SECONDS:
        return cached
    groups: dict[int, dict] = {}
    for slot, client in sorted(_clients.items()):
        if (only is not None and slot != only) or (only is None and not slot_enabled(slot)):
            continue
        try:
            async for d in client.iter_dialogs():
                if not d.is_group:
                    continue
                if d.id in groups:
                    groups[d.id]["slots"].append(slot)
                else:
                    groups[d.id] = {"id": d.id, "title": d.name or str(d.id), "slots": [slot],
                                    "username": getattr(d.entity, "username", None)}
        except Exception as e:
            log.warning("%d-akkaunt guruhlari olinmadi (%s)", slot, type(e).__name__)
    if only is None:
        for g in groups.values():
            if 1 not in g["slots"] and len(_clients) > 1:
                g["title"] += f" · {g['slots'][0]}-akk"
    result = list(groups.values())
    _cache[only] = (time.time(), result)
    return result


def panel(groups: list[dict], page: int = 0, slot: int | None = None, acc_name: str = "") -> tuple[str, list]:
    """Matn va tugmalar ([[(matn, data)]]) — control_bot._inline formatida. slot — qaysi akkaunt guruhlari."""
    sfx = f":{slot}" if slot else ""
    # Yoqilganlar tepada, qolganlari Telegram'dagi tartibda
    ordered = [g for g in groups if g["id"] in _enabled] + [g for g in groups if g["id"] not in _enabled]
    pages = max(1, -(-len(ordered) // PER_PAGE))
    page = min(max(0, page), pages - 1)
    chunk = ordered[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [
        "👥 <b>Guruhlar — xabarlarni klonlash</b>" + (f" — {slot}-akkaunt {acc_name}".rstrip() if slot else ""),
        "",
        f"Akkauntingizda {len(groups)} ta guruh, klonlanyapti: "
        f"<b>{sum(1 for g in groups if g['id'] in _enabled)}</b> ta.",
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
        rows.append([(("✅ " if on else "▫️ ") + title, f"grp:t:{g['id']}:{page}{sfx}")])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"grp:p:{page - 1}{sfx}"))
    if page < pages - 1:
        nav.append(("➡️", f"grp:p:{page + 1}{sfx}"))
    if nav:
        rows.append(nav)
    rows.append([("🗂 Yoqilganlar — o'qish/yuklash", f"grp:on:0{sfx}"), ("🔄 Yangilash", f"grp:r:{page}{sfx}")])
    return "\n".join(lines), rows


def slot_enabled(slot: int) -> bool:
    """2-akkaunt guruhlari faqat «acc2_groups» yoqilganda."""
    return slot == 1 or bool(runtime.get(f"acc{slot}_groups"))


def reset_cache() -> None:
    _cache.clear()


def forget_client(slot: int) -> None:
    """Akkauntdan chiqilganda — uning guruhlari ro'yxatdan olib tashlanadi."""
    _clients.pop(slot, None)
    _cache.clear()


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


def setup(user: TelegramClient, send_text, send_media, slot: int = 1) -> None:
    """send_text(text_html, link) va send_media(...) — control_bot'dagi yuboruvchilar.
    Har bir akkaunt uchun alohida chaqiriladi (slot 1, 2)."""
    global _send_text, _send_media
    _send_text, _send_media = send_text, send_media
    _clients[slot] = user
    _cache.clear()

    @user.on(events.NewMessage(incoming=True, func=lambda e: e.is_group and e.chat_id in _enabled))
    async def _on_group_message(event):
        global _dropped
        if not slot_enabled(slot):
            return
        key = (event.chat_id, event.message.id)
        if key in _seen:
            return   # ikkinchi akkaunt ham shu guruhda — xabar allaqachon navbatda
        _seen[key] = None
        while len(_seen) > SEEN_MAX:
            _seen.pop(next(iter(_seen)))
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
    plain_quote = ""
    reply_msg_id = None
    if msg.is_reply:
        try:
            r = await msg.get_reply_message()
            if r is not None:
                rname, rusername = _sender_name(await r.get_sender())
                rlabel = userbot._media_label(r) if r.media else None
                rtext = (r.message or "").strip() or (f"[{rlabel}]" if rlabel else "")
                quote = f"↩️ <i>{_esc(rname)}: {_esc(rtext[:150])}</i>\n"
                plain_quote = f"↩️ {rname}: {rtext[:150]}"
                if rlabel and rlabel != "stiker" and (getattr(r, "file", None) or getattr(r, "photo", None)):
                    rsize = getattr(getattr(r, "file", None), "size", 0) or 0
                    if rsize <= MAX_MEDIA_BYTES:
                        try:
                            rdata = await r.download_media(file=bytes)
                        except Exception as e:
                            log.warning("Javob berilgan guruh mediasi yuklab olinmadi (%s)", type(e).__name__)
                            rdata = None
                        if rdata:
                            reply_msg_id = await _send_media(
                                event.chat_id, f"👥 {group} · ↩️ {rname}", rdata,
                                userbot._media_filename(r, rlabel), kind=rlabel, username=rusername,
                                caption=(r.message or "").strip(), view_once=userbot._is_view_once(r),
                                mime=(getattr(getattr(r, "file", None), "mime_type", None) or "").lower(),
                                attributes=getattr(getattr(r.media, "document", None), "attributes", None),
                                link_reply=False,
                            )
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
                cap = f"{plain_quote}\n{text}".strip() if plain_quote else text
                media_kw = dict(
                    kind=label, username=username, caption=cap, view_once=userbot._is_view_once(msg),
                    mime=(getattr(msg.file, "mime_type", None) or "").lower(),
                    attributes=getattr(getattr(msg.media, "document", None), "attributes", None),
                    link_reply=False,
                )
                if reply_msg_id is not None:
                    media_kw["reply_to"] = reply_msg_id
                await _send_media(event.chat_id, f"👥 {group} · {name}", data,
                                  userbot._media_filename(msg, label), **media_kw)
                return
        text = (f"[{label}: {size / 1024 / 1024:.0f} MB — yuklanmadi, limit {config.MAX_MEDIA_MB} MB] "
                + text).strip()
    elif label:
        text = (f"[{label}] " + text).strip()
    if not text:
        return   # xizmat xabarlari (kimdir qo'shildi va h.k.)

    who = f"<b>{_esc(name)}</b>" + (f" (@{_esc(username)})" if username else "")
    body = f"👥 <b>{_esc(group)}</b>\n👤 {who}\n{quote}\n{_esc(text[:3500])}"
    if reply_msg_id is not None:
        await _send_text(body, link, reply_to=reply_msg_id)
    else:
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
