"""Shaxsiy akkaunt (userbot): kiruvchi shaxsiy xabarlarga AI orqali javob beradi."""
import asyncio
import io
import logging
import random
import time
from collections import defaultdict, deque

import openai
from telethon import TelegramClient, errors, events
from telethon.tl.types import User

import ai
import availability
import control_bot
import saver
import storage
import usage
from config import (FALLBACK_TEXT, MAX_MEDIA_MB, MAX_REPLIES_PER_MINUTE, OWNER_TAKEOVER_MINUTES,
                    REPLY_DELAY_SECONDS, REPLY_QUOTE, VOICE_REPLY_ENABLED)

log = logging.getLogger("userbot")

TELEGRAM_SERVICE_ID = 777000           # login kodlari keladigan rasmiy akkaunt — hech qachon tegmaymiz
MAX_MEDIA_BYTES = MAX_MEDIA_MB * 1024 * 1024

# Javob kutayotgan xabarlar: (xabar id, matn, vaqt) — kotiba qaysi xabarga reply qilishini tanlashi uchun
_pending: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
PENDING_MAX_AGE = 15 * 60   # bundan eski javobsiz xabarlarga reply qilinmaydi
QUOTE_SNIPPET = 120

_seq: dict[int, int] = defaultdict(int)             # debounce: har bir chat uchun oxirgi xabar tartibi
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_sent_times: dict[int, deque] = defaultdict(deque)  # rate-limit
_ai_texts: dict[int, set[str]] = defaultdict(set)   # AI yuborgan matnlar (egasi yozganidan farqlash uchun)
_ai_voice_pending: dict[int, int] = defaultdict(int)  # AI yuborayotgan ovozli xabarlar soni


async def _send_quoted(send, reply_to: int | None, *args, **kwargs):
    """Xabarni reply qilib yuboradi; iqtibos qilinayotgan xabar o'chirilgan bo'lsa — oddiy xabar qilib."""
    if reply_to:
        try:
            return await send(*args, reply_to=reply_to, **kwargs)
        except errors.BadRequestError as e:  # masalan, iqtibos qilinayotgan xabar o'chirilgan
            log.info("Reply qilib bo'lmadi (%s) — oddiy xabar yuboriladi", type(e).__name__)
            file = args[1] if len(args) > 1 else None
            if hasattr(file, "seek"):
                file.seek(0)
    return await send(*args, **kwargs)


async def _quoted_prefix(msg) -> str:
    """Suhbatdosh biror xabarga reply qilib yozgan bo'lsa — AI uchun o'sha xabardan iqtibos."""
    if not getattr(msg, "is_reply", False):
        return ""
    try:
        orig = await msg.get_reply_message()
    except Exception:
        return ""
    if orig is None:
        return ""
    quote = " ".join((orig.message or "").split()) or (_media_label(orig) or "xabar")
    if len(quote) > QUOTE_SNIPPET:
        quote = quote[:QUOTE_SNIPPET].rstrip() + "…"
    return f"[«{quote}» xabariga javoban] "


def _take_pending(chat_id: int, fallback_id: int | None) -> list[tuple[int, str]]:
    """Javob kutayotgan (hali eskirmagan) xabarlarni oladi va navbatni tozalaydi."""
    now = time.time()
    items = [(i, text) for i, text, ts in _pending[chat_id] if now - ts <= PENDING_MAX_AGE and i]
    _pending[chat_id].clear()
    if not items and fallback_id:
        items = [(fallback_id, "")]
    return items


def _pick_reply_target(items: list[tuple[int, str]], index: int) -> int | None:
    """AI tanlagan raqam bo'yicha xabar id si; tanlamagan bo'lsa — oxirgi xabar."""
    if not REPLY_QUOTE or not items:
        return None
    if 1 <= index <= len(items):
        return items[index - 1][0]
    return items[-1][0]


async def _send_voice(user: TelegramClient, chat_id: int, text: str, reply_to: int | None = None) -> bool:
    """Javobni ovozli xabar qilib yuboradi. Muvaffaqiyatsiz bo'lsa False (matn yuboriladi)."""
    ok, _ = await usage.check(chat_id)
    if not ok:
        return False
    try:
        async with user.action(chat_id, "record-audio"):
            audio = await ai.speak(text)
        await usage.record(chat_id, "tts")
        f = io.BytesIO(audio)
        f.name = "javob.ogg"
        _ai_voice_pending[chat_id] += 1
        try:
            await _send_quoted(user.send_file, reply_to, chat_id, f, voice_note=True)
        except Exception:
            _ai_voice_pending[chat_id] -= 1
            raise
        log.info("%s ga ovozli javob yuborildi", chat_id)
        return True
    except Exception as e:
        log.warning("Ovozli javob bo'lmadi (%s) — matn yuboriladi", type(e).__name__)
        return False


_DEFAULT_NAMES = {"rasm": "photo.jpg", "video": "video.mp4", "ovozli xabar": "voice.ogg",
                  "video-xabar": "video_note.mp4", "audio": "audio.mp3"}


def _is_view_once(msg) -> bool:
    """Bir marta ko'riladigan yoki taymerli (o'zi o'chib ketadigan) media."""
    return bool(getattr(msg.media, "ttl_seconds", None))


def _media_filename(msg, label: str) -> str:
    """Bot orqali yuborishda Telegram media turini to'g'ri aniqlashi uchun kengaytmali nom."""
    name = getattr(msg.file, "name", None)
    if name:
        return name
    if label in _DEFAULT_NAMES:
        return _DEFAULT_NAMES[label]
    return "file" + (getattr(msg.file, "ext", None) or ".bin")


async def _video_thumb(msg) -> bytes | None:
    try:
        return await msg.download_media(file=bytes, thumb=-1)
    except Exception:
        return None


def _media_label(msg) -> str | None:
    if msg.voice:
        return "ovozli xabar"
    if msg.video_note:
        return "video-xabar"
    if msg.photo:
        return "rasm"
    if msg.video:
        return "video"
    if msg.sticker:
        return "stiker"
    if getattr(msg, "audio", None):
        return "audio"
    if msg.document:
        return "fayl"
    if msg.media:
        return "media"
    return None


_ai_alerted: set[str] = set()  # egasiga bir xil AI xatosi haqida faqat bir marta xabar berish


async def _report_ai_error(e: Exception) -> None:
    """OpenAI xatosini tushunarli logga yozadi; jiddiy xatolarda egasini bot orqali ogohlantiradi."""
    hints = {
        openai.AuthenticationError: "OpenAI API kaliti noto'g'ri yoki bekor qilingan (401). "
                                    ".env dagi OPENAI_API_KEY ni yangilang.",
        openai.PermissionDeniedError: "OpenAI kalitida bu modelga ruxsat yo'q (403). OPENAI_MODEL ni tekshiring.",
        openai.NotFoundError: "OPENAI_MODEL topilmadi (404). .env dagi model nomini tekshiring.",
        openai.RateLimitError: "OpenAI limiti tugagan yoki balans yetarli emas (429). "
                               "platform.openai.com -> Billing ni tekshiring.",
    }
    hint = next((h for cls, h in hints.items() if isinstance(e, cls)), None)
    if hint is None:
        log.exception("OpenAI xatosi — shablon javob ishlatiladi")
        return
    log.error("%s Shablon javob ishlatiladi.", hint)
    if type(e).__name__ not in _ai_alerted:
        _ai_alerted.add(type(e).__name__)
        await control_bot.alert(f"⚠️ AI ishlamayapti: {hint}\n"
                                "Hozircha odamlarga tayyor shablon matn yuboriladi. "
                                "Kalitni yangilagach, dasturni qayta ishga tushiring.")


_last_call: dict[int, float] = {}
CALL_COOLDOWN_SECONDS = 10 * 60  # bitta odam 10 daqiqada ko'pi bilan bir marta "chaqirish" xabari yuboradi


def _call_allowed(chat_id: int) -> bool:
    now = time.time()
    if now - _last_call.get(chat_id, 0) < CALL_COOLDOWN_SECONDS:
        return False
    _last_call[chat_id] = now
    return True


def _rate_ok(chat_id: int) -> bool:
    q, now = _sent_times[chat_id], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    return len(q) < MAX_REPLIES_PER_MINUTE


async def _understand_media(msg, label: str, data: bytes, chat_id: int) -> str | None:
    """Ovozli xabar/audio/video-xabar -> matn, rasm -> tavsif. Bo'lmasa None (oddiy "[media]" yoziladi)."""
    mime = (getattr(msg.file, "mime_type", None) or "").lower()
    ok, reason = await usage.check(chat_id)
    if not ok:
        log.info("Media AI'siz qoldirildi: %s", reason)
        return None
    try:
        if msg.voice or msg.video_note or mime.startswith("audio/"):
            ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-wav": "wav",
                   "audio/wav": "wav", "video/mp4": "mp4"}.get(mime, "ogg")
            spoken = await ai.transcribe(data, f"audio.{ext}")
            await usage.record(chat_id, "voice")
            kind = "video-xabar" if msg.video_note else "ovozli xabar"
            log.info("%s matnga o'girildi (%d belgi)", kind, len(spoken))
            return f"[{kind}] {spoken}" if spoken else f"[{kind}: tushunarsiz yoki bo'sh]"
        if msg.photo or mime.startswith("image/"):
            desc = await ai.describe_image(data, mime if mime.startswith("image/") else "image/jpeg")
            await usage.record(chat_id, "image")
            log.info("Rasm tasvirlandi (%d belgi)", len(desc))
            return f"[rasm] {desc}" if desc else None
    except Exception as e:
        await _report_ai_error(e)
    return None


async def _stat(chat_id: int | None = None, **counters: int) -> None:
    try:
        await storage.stat_inc(chat_id=chat_id, **counters)
    except Exception as e:
        log.warning("Statistika yozilmadi (%s)", type(e).__name__)


def _display_name(u: User) -> str:
    return " ".join(p for p in (u.first_name, u.last_name) if p) or "Nomaʼlum"


def setup(user: TelegramClient, me_id: int) -> None:

    @user.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_incoming(event):
        sender = await event.get_sender()
        chat_id = event.chat_id
        if not isinstance(sender, User) or sender.bot or sender.id == TELEGRAM_SERVICE_ID:
            return
        if await storage.is_ignored(sender.id):
            return

        msg = event.message
        label = _media_label(msg)
        text = msg.message or ""
        name, username = _display_name(sender), sender.username

        # Media: yuklab olamiz, ovozni matnga o'giramiz / rasmni tasvirlaymiz, egasiga bot orqali yuboramiz
        data = None
        view_once = _is_view_once(msg)
        if label and label != "stiker":
            size = getattr(msg.file, "size", 0) or 0
            problem = None
            if size > MAX_MEDIA_BYTES:
                problem = f"hajmi juda katta ({size / 1024 / 1024:.0f} MB, limit {MAX_MEDIA_MB} MB — .env: MAX_MEDIA_MB)"
            else:
                try:
                    data = await msg.download_media(file=bytes)
                except Exception as e:
                    log.exception("Media yuklab olinmadi")
                    problem = f"yuklab olinmadi ({type(e).__name__})"
                if not data and not problem:
                    problem = "yuklab olinmadi (bo'sh)"
            if problem and (view_once or size > MAX_MEDIA_BYTES):
                await control_bot.alert(
                    f"⚠️ {name} ({chat_id}) yuborgan {label}"
                    f"{' (bir marta ko`riladigan)' if view_once else ''} saqlanmadi: {problem}.")
        media_text = await _understand_media(msg, label, data, chat_id) if data else None

        if media_text:
            content = f"{media_text}\n{text}".strip()
        else:
            content = (f"[media: {label}] " if label else "") + text
        if not content.strip():
            return
        content = (await _quoted_prefix(msg) + content.strip()).strip()
        await storage.add_history(chat_id, "user", content)
        _pending[chat_id].append((getattr(msg, "id", None), content, time.time()))
        await _stat(chat_id=chat_id, messages=1)

        media_msg_id = None  # egasiga yuborilgan media — bildirishnoma shunga reply bo'ladi
        if data:
            try:
                has_video = bool(msg.video or msg.video_note)
                media_msg_id = await control_bot.send_media(
                    chat_id, name, data, _media_filename(msg, label),
                    kind=label, username=username, view_once=view_once,
                    mime=(getattr(msg.file, "mime_type", None) or "").lower(),
                    attributes=getattr(getattr(msg.media, "document", None), "attributes", None),
                    thumb=await _video_thumb(msg) if has_video else None,
                )
            except Exception:
                log.exception("Media botga yuborilmadi")

        # Kontakt turi: oila -> AI javob bermaydi, faqat egasiga xabar
        tag = await storage.get_tag(sender.id)
        if tag == "oila":
            await storage.upsert_inbox(chat_id, name, username, "", False, content)
            await control_bot.notify(chat_id, f"👨‍👩‍👧 {name}", username, content, "", False, None, reply_to=media_msg_id)
            return
        kind = tag or ("tanish" if sender.contact else "notanish")

        if await storage.is_muted(chat_id):
            return  # egasi o'zi shu chatda gaplashyapti
        status = await availability.get_status()
        if not status.reply:
            log.info("%s ga javob berilmadi: %s", chat_id, status.reason)
            await storage.upsert_inbox(chat_id, name, username, "", False, content)
            if status.notify:
                await control_bot.notify(chat_id, name, username, content, "", False, None, reply_to=media_msg_id)
            return

        # Debounce: odam ketma-ket bir nechta xabar yozsa, oxirgisiga bitta javob beramiz
        _seq[chat_id] += 1
        my_seq = _seq[chat_id]
        await asyncio.sleep(REPLY_DELAY_SECONDS)
        if my_seq != _seq[chat_id]:
            return

        async with _locks[chat_id]:
            if await storage.is_muted(chat_id):
                return
            if not _rate_ok(chat_id):
                log.warning("Rate-limit: %s", chat_id)
                return

            history = await storage.get_history(chat_id)
            context = f"{status.ai_context}\nSuhbatdosh turi: {kind}."
            waiting = _take_pending(chat_id, getattr(msg, "id", None))
            if REPLY_QUOTE and len(waiting) > 1:
                context += "\nJavob kutayotgan xabarlar (reply_to uchun raqamlar):\n" + "\n".join(
                    f"{n}) {text[:200]}" for n, (_, text) in enumerate(waiting, 1))
            already = any(h["role"] == "assistant" for h in history)
            fallback = {"reply": None if already else FALLBACK_TEXT,
                        "summary": "", "urgent": False, "call_owner": False, "spam": False, "tokens": 0}
            ok, reason = await usage.check(chat_id)
            if not ok:
                log.warning("AI ishlatilmadi (%s) — shablon javob", reason)
                if usage.should_alert(reason, chat_id):
                    await control_bot.alert(f"💸 Xarajat limiti: {reason}.\n"
                                            f"Suhbatdosh: {name} ({chat_id}). Kotiba hozircha shablon matn yuboradi. "
                                            "Limitlar: .env dagi AI_DAILY_LIMIT, AI_CHAT_DAILY_LIMIT.")
                result = fallback
            else:
                try:
                    result = await ai.generate(history, context)
                    await usage.record(chat_id, "chat", result["tokens"])
                except Exception as e:
                    await _report_ai_error(e)
                    result = fallback

            # Notanishdan kelgan aniq spam: javob ham, bildirishnoma ham yo'q
            if result["spam"] and kind == "notanish":
                await _stat(spam=1)
                log.info("%s (%s) dan spam — javob berilmadi, bildirishnoma yuborilmadi", name, chat_id)
                await storage.upsert_inbox(chat_id, name, username, "🚫 spam", False, content)
                await storage.mark_handled(chat_id)
                return

            reply = result["reply"]
            if reply:
                is_voice_in = bool(msg.voice or msg.video_note)
                target = _pick_reply_target(waiting, result.get("reply_to", 0))
                if not (is_voice_in and VOICE_REPLY_ENABLED and await _send_voice(user, chat_id, reply, target)):
                    async with user.action(chat_id, "typing"):
                        await asyncio.sleep(min(1 + len(reply) / 40, 5) + random.uniform(0, 1))
                    _ai_texts[chat_id].add(reply)
                    await _send_quoted(user.send_message, target, chat_id, reply)
                _sent_times[chat_id].append(time.time())
                await storage.add_history(chat_id, "assistant", reply)

            # Suhbatdosh egasini chaqirishni so'radi -> alohida, shoshilinch ogohlantirish
            calling = result["call_owner"] and _call_allowed(chat_id)
            if calling:
                await _stat(calls=1)
            if result["urgent"]:
                await _stat(urgent=1)
            await storage.upsert_inbox(chat_id, name, username, result["summary"],
                                       result["urgent"] or calling, content)
            label_name = f"💼 {name}" if kind == "mijoz" else name
            if calling:
                log.info("%s (%s) egasini chaqiryapti — botga xabar berildi", name, chat_id)
                await control_bot.call_owner(chat_id, label_name, username, content, result["summary"])
            else:
                await control_bot.notify(chat_id, label_name, username, content,
                                         result["summary"], result["urgent"], reply, reply_to=media_msg_id)

    @user.on(events.NewMessage(outgoing=True))
    async def on_outgoing(event):
        chat_id = event.chat_id
        msg = event.message
        text = msg.message or ""
        # Kotibaning o'z xabarlari — egasi yozgani emas
        if text and text in _ai_texts[chat_id]:
            _ai_texts[chat_id].discard(text)
            return
        if (msg.voice or msg.video_note) and _ai_voice_pending[chat_id] > 0:
            _ai_voice_pending[chat_id] -= 1
            return

        if chat_id == me_id and saver.is_saving():
            return  # /save: media Saqlangan xabarlarga dastur tomonidan joylanyapti — egasi yozgani emas

        # Egasi istalgan joyga (guruh, kanal, shaxsiy) o'zi yozdi -> u faol (/auto rejimi uchun)
        await availability.mark_owner_active()

        if not event.is_private or chat_id == me_id:  # guruhlar va Saved Messages
            return
        # Egasi o'zi shu chatga yozdi -> AI shu chatda vaqtincha jim
        await storage.mute(chat_id, OWNER_TAKEOVER_MINUTES * 60)
        await storage.mark_handled(chat_id)
        if text:
            await storage.add_history(chat_id, "assistant", f"{ai.OWNER_WROTE_PREFIX} {text}")
        log.info("Egasi %s chatiga o'zi yozdi — AI %d daqiqa jim", chat_id, OWNER_TAKEOVER_MINUTES)

