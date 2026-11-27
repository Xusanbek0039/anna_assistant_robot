"""📢 Kanallar arxivi: faqat siz belgilagan kanallarning postlari chat arxiviga yoziladi.

- Botda «🗂 Chat arxivi» → «📢 Kanallar» (yoki /kanallar): akkauntingiz obuna bo'lgan kanallar ro'yxati,
  har biri yonida yoqish tugmasi. Guruhlar bu ro'yxatda yo'q (ular — 👥 Guruhlar va ARCHIVE_GROUPS).
- Yoqilgan kanalning yangi postlari (matn, rasm, video, fayl) arxivga tushadi; yoqilgan zahoti oxirgi
  postlari ham olinadi. Post o'chirilsa yoki tahrirlansa — arxivda eski holati qoladi, HTML/Drive'ga ham
  boshqa chatlar kabi yuklanadi.
- Tanlov MongoDB'da saqlanadi. Faqat asosiy (1-) akkaunt kanallari.
"""
import logging
import time

from telethon import TelegramClient

import storage

log = logging.getLogger("channels")

PER_PAGE = 10
CACHE_SECONDS = 300

_enabled: dict[int, str] = {}        # kanal id -> nomi
_cache: tuple[float, list[dict]] = (0.0, [])


async def load() -> None:
    try:
        rows = await storage.channel_archive_list()
    except Exception as e:
        log.warning("Arxivlanadigan kanallar o'qilmadi (%s)", type(e).__name__)
        return
    _enabled.clear()
    _enabled.update({int(r["chat_id"]): r.get("title") or "" for r in rows})


def is_enabled(chat_id: int | None) -> bool:
    return chat_id in _enabled


def enabled_count() -> int:
    return len(_enabled)


async def toggle(chat_id: int, title: str = "") -> bool:
    """Yoqadi/o'chiradi. Yangi holat (True — arxivlanadi). O'chirilsa ham avval saqlangan postlar arxivda qoladi."""
    if chat_id in _enabled:
        _enabled.pop(chat_id, None)
        await storage.channel_archive_remove(chat_id)
        return False
    _enabled[chat_id] = title
    await storage.channel_archive_add(chat_id, title)
    return True


def is_broadcast(dialog_or_event) -> bool:
    """Kanal (guruh emas): Telethon'da superguruh ham «channel», shuning uchun is_group tekshiriladi."""
    return bool(getattr(dialog_or_event, "is_channel", False)) and not getattr(dialog_or_event, "is_group", False)


async def my_channels(user: TelegramClient, refresh: bool = False) -> list[dict]:
    global _cache
    ts, cached = _cache
    if cached and not refresh and time.time() - ts < CACHE_SECONDS:
        return cached
    result = []
    async for d in user.iter_dialogs():
        if is_broadcast(d):
            result.append({"id": d.id, "title": d.name or str(d.id),
                           "username": getattr(d.entity, "username", None)})
    _cache = (time.time(), result)
    return result


def panel(channels: list[dict], page: int = 0, archive_on: bool = True) -> tuple[str, list]:
    """Matn va tugmalar ([[(matn, data)]]) — control_bot._inline formatida."""
    ordered = [c for c in channels if c["id"] in _enabled] + [c for c in channels if c["id"] not in _enabled]
    pages = max(1, -(-len(ordered) // PER_PAGE))
    page = min(max(0, page), pages - 1)
    chunk = ordered[page * PER_PAGE:(page + 1) * PER_PAGE]
    lines = [
        "📢 <b>Kanallar arxivi</b>", "",
        f"Obuna bo'lgan kanallar: {len(channels)} ta, arxivlanyapti: <b>{len(_enabled)}</b> ta.",
        "Kanalni bosib yoqing — uning postlari (matn, rasm, video, fayl) chat arxiviga yoziladi: "
        "o'chirilsa ham sizda qoladi, HTML qilib olasiz va Drive'ga yuklanadi.",
    ]
    if not archive_on:
        lines += ["", "⚠️ Chat arxivi to'xtatilgan — «🗂 Chat arxivi» → «▶️ Arxivni yoqish»."]
    if not channels:
        lines += ["", "<i>Akkauntingizda kanal topilmadi.</i>"]
    if pages > 1:
        lines += ["", f"Sahifa {page + 1}/{pages}"]
    rows = []
    for c in chunk:
        on = c["id"] in _enabled
        title = c["title"] if len(c["title"]) <= 40 else c["title"][:39] + "…"
        rows.append([(("✅ " if on else "▫️ ") + title, f"chn:t:{c['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"chn:p:{page - 1}"))
    if page < pages - 1:
        nav.append(("➡️", f"chn:p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🔄 Yangilash", f"chn:r:{page}"), ("⬅️ Arxiv", "arc:panel")])
    return "\n".join(lines), rows
