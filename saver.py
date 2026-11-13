"""Havola orqali xabarni saqlash: egasi botga t.me havolasini yuboradi — uning akkaunti (o'sha kanal/guruh
a'zosi sifatida) xabardagi mediani oladi va "Saqlangan xabarlar"ga joylaydi.

Avval tez yo'l sinaladi (mediani qayta yuklamasdan nusxalash). Kanalda nusxalash taqiqlangan bo'lsa yoki
bu yo'l ishlamasa — fayl vaqtinchalik papkaga yuklab olinadi va qaytadan yuklanadi.
"""
import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass

from telethon import errors
from telethon.tl.types import MessageMediaWebPage, PeerChannel

from config import SAVE_MAX_MB

log = logging.getLogger("saver")

MAX_LINKS = 10            # bitta xabarda ko'pi bilan shuncha havola
ALBUM_SPAN = 9            # albom bo'laklari qo'shni id larda turadi (albomda ko'pi bilan 10 ta)
PROGRESS_EVERY = 4        # holat xabarini necha soniyada bir yangilash

LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(c/)?([A-Za-z0-9_]+)/(\d+)(?:/(\d+))?(\?[^\s]*)?",
    re.IGNORECASE)
_NOT_USERNAMES = {"joinchat", "addlist", "addstickers", "share", "proxy", "socks", "s", "c"}

_lock = asyncio.Lock()     # saqlashlar navbat bilan — bir vaqtda bir nechta katta fayl yuklanmasin
_active = 0
_last_done = 0.0


@dataclass(frozen=True)
class Link:
    peer: object           # PeerChannel (maxfiy: t.me/c/<id>/...) yoki username (ochiq)
    msg_id: int
    single: bool           # ?single — albomdan faqat shu bitta bo'lak
    url: str


def parse_links(text: str) -> list[Link]:
    out, seen = [], set()
    for m in LINK_RE.finditer(text or ""):
        private, name, a, b, query = m.groups()
        msg_id = int(b or a)                     # t.me/c/<id>/<mavzu>/<xabar> — oxirgi son xabar id si
        if private:
            if not name.isdigit():
                continue
            peer = PeerChannel(int(name))
        else:
            if name.lower() in _NOT_USERNAMES or name.isdigit():
                continue
            peer = name
        key = (str(peer), msg_id)
        if msg_id <= 0 or key in seen:
            continue
        seen.add(key)
        out.append(Link(peer, msg_id, "single" in (query or ""), m.group(0)))
    return out[:MAX_LINKS]


def is_saving() -> bool:
    """Saqlash ketyapti (yoki hozirgina tugadi) — "Saqlangan xabarlar"dagi chiquvchi xabarlar egasi
    o'zi yozgani emas (userbot ularni "egasi faol" deb hisoblamasligi uchun)."""
    return _active > 0 or time.time() - _last_done < 5


def _has_media(msg) -> bool:
    return bool(msg.media) and not isinstance(msg.media, MessageMediaWebPage)


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


async def _entity(user, peer):
    try:
        return await user.get_input_entity(peer)
    except ValueError:
        await user.get_dialogs()   # sessiya keshida yo'q — suhbatlar ro'yxatini bir marta yangilaymiz
        return await user.get_input_entity(peer)


async def _collect(user, entity, link: Link) -> list:
    """Havoladagi xabar; albom bo'lsa — uning barcha bo'laklari (tartib bilan)."""
    msg = await user.get_messages(entity, ids=link.msg_id)
    if msg is None:
        return []
    if not msg.grouped_id or link.single:
        return [msg]
    ids = list(range(max(1, link.msg_id - ALBUM_SPAN), link.msg_id + ALBUM_SPAN + 1))
    around = await user.get_messages(entity, ids=ids)
    return [m for m in around if m is not None and m.grouped_id == msg.grouped_id] or [msg]


async def _send_text(user, msg) -> None:
    await user.send_message("me", msg.message, formatting_entities=msg.entities, link_preview=False)


async def _copy_fast(user, msgs: list) -> None:
    """Mediani qayta yuklamasdan nusxalash. Nusxalash taqiqlangan kanallarda xato beradi."""
    if len(msgs) > 1:
        await user.send_file("me", [m.media for m in msgs], caption=[m.message or "" for m in msgs])
    else:
        m = msgs[0]
        await user.send_file("me", m.media, caption=m.message or "", formatting_entities=m.entities)


async def _copy_slow(user, msg, progress) -> None:
    """Yuklab olib, qaytadan yuklash (nusxalash taqiqlangan kontent uchun ham ishlaydi)."""
    size = getattr(msg.file, "size", 0) or 0
    if size > SAVE_MAX_MB * 1024 * 1024:
        raise ValueError(f"fayl juda katta ({_mb(size)}, limit {SAVE_MAX_MB} MB — .env: SAVE_MAX_MB)")
    tmp = tempfile.mkdtemp(prefix="anna_save_")
    try:
        path = await msg.download_media(file=tmp + os.sep, progress_callback=progress("⬇️ Yuklab olinmoqda"))
        if not path:
            raise ValueError("bu turdagi mediani yuklab bo'lmadi")
        thumb = None
        if msg.video or msg.video_note or msg.gif:
            try:
                thumb = await msg.download_media(file=os.path.join(tmp, "thumb.jpg"), thumb=-1)
            except Exception:
                thumb = None
        mime = (getattr(msg.file, "mime_type", None) or "").lower()
        kwargs = dict(
            attributes=getattr(getattr(msg.media, "document", None), "attributes", None),
            thumb=thumb,
            voice_note=bool(msg.voice),
            video_note=bool(msg.video_note),
            supports_streaming=bool(msg.video),
            # fayl qilib yuborilgan rasm siqilmasin
            force_document=bool(msg.document) and mime.startswith("image/") and not msg.sticker,
            progress_callback=progress("⬆️ Saqlanmoqda"),
        )
        try:
            await user.send_file("me", path, caption=msg.message or "", formatting_entities=msg.entities, **kwargs)
        except errors.MediaCaptionTooLongError:
            await user.send_file("me", path, **kwargs)
            await _send_text(user, msg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _explain(e: Exception) -> str:
    if isinstance(e, (errors.ChannelPrivateError, errors.ChannelInvalidError, errors.UserNotParticipantError)):
        return "bu kanal/guruhga kirish yo'q — akkauntingiz uning a'zosi emas"
    if isinstance(e, (errors.UsernameInvalidError, errors.UsernameNotOccupiedError)):
        return "bunday kanal topilmadi"
    if isinstance(e, errors.FloodWaitError):
        return f"Telegram cheklovi — {e.seconds} soniyadan keyin urinib ko'ring"
    if isinstance(e, ValueError):
        return str(e) if "entity" not in str(e).lower() else "kanal topilmadi — akkauntingiz uning a'zosi emas"
    return type(e).__name__


async def save_link(user, link: Link, status=None) -> str:
    """Bitta havolani saqlaydi. status(text) — egasiga jarayonni ko'rsatish uchun (ixtiyoriy).
    Natija matnini qaytaradi ("✅ ..." yoki "❌ ...")."""
    global _active, _last_done

    def progress(title: str):
        last = [0.0]

        async def cb(done: int, total: int) -> None:
            now = time.time()
            if status and total and now - last[0] >= PROGRESS_EVERY:
                last[0] = now
                await status(f"{title}: {done * 100 // total}% ({_mb(done)} / {_mb(total)})")
        return cb

    async with _lock:
        _active += 1
        try:
            entity = await _entity(user, link.peer)
            msgs = await _collect(user, entity, link)
            if not msgs:
                return "❌ Xabar topilmadi (o'chirilgan yoki havola noto'g'ri)"
            media = [m for m in msgs if _has_media(m)]
            if not media:
                if msgs[0].message:
                    await _send_text(user, msgs[0])
                    return "✅ Bu xabarda media yo'q — matni saqlandi"
                return "❌ Bu xabarda saqlanadigan narsa yo'q"
            try:
                await _copy_fast(user, media)
            except Exception as e:
                log.info("Tez nusxalash bo'lmadi (%s) — yuklab olinadi", type(e).__name__)
                for m in media:
                    await _copy_slow(user, m, progress)
            n = len(media)
            return "✅ Saqlangan xabarlarga joylandi" + (f" ({n} ta media)" if n > 1 else "")
        except Exception as e:
            log.exception("Havola saqlanmadi")
            return f"❌ Saqlanmadi: {_explain(e)}"
        finally:
            _active -= 1
            _last_done = time.time()
