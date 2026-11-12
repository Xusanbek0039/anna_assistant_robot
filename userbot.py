"""Shaxsiy akkaunt (userbot): kiruvchi shaxsiy xabarlarga AI orqali javob beradi."""
import asyncio
import io
import logging
import random
import time
from collections import defaultdict, deque

import openai
from telethon import TelegramClient, events
from telethon.tl.types import User

import ai
import availability
import control_bot
import storage
from config import (FALLBACK_TEXT, MAX_REPLIES_PER_MINUTE, OWNER_NAME, OWNER_TAKEOVER_MINUTES,
                    REPLY_DELAY_SECONDS, VOICE_REPLY_ENABLED)

log = logging.getLogger("userbot")

TELEGRAM_SERVICE_ID = 777000           # login kodlari keladigan rasmiy akkaunt — hech qachon tegmaymiz
MAX_MEDIA_BYTES = 20 * 1024 * 1024

_seq: dict[int, int] = defaultdict(int)             # debounce: har bir chat uchun oxirgi xabar tartibi
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_sent_times: dict[int, deque] = defaultdict(deque)  # rate-limit
_ai_texts: dict[int, set[str]] = defaultdict(set)   # AI yuborgan matnlar (egasi yozganidan farqlash uchun)
_ai_voice_pending: dict[int, int] = defaultdict(int)  # AI yuborayotgan ovozli xabarlar soni


async def _send_voice(user: TelegramClient, chat_id: int, text: str) -> bool:
    """Javobni ovozli xabar qilib yuboradi. Muvaffaqiyatsiz bo'lsa False (matn yuboriladi)."""
    try:
        async with user.action(chat_id, "record-audio"):
            audio = await ai.speak(text)
        f = io.BytesIO(audio)
        f.name = "javob.ogg"
        _ai_voice_pending[chat_id] += 1
        try:
            await user.send_file(chat_id, f, voice_note=True)
        except Exception:
            _ai_voice_pending[chat_id] -= 1
            raise
        log.info("%s ga ovozli javob yuborildi", chat_id)
        return True
    except Exception as e:
        log.warning("Ovozli javob bo'lmadi (%s) — matn yuboriladi", type(e).__name__)
        return False


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


async def _understand_media(msg, label: str, data: bytes) -> str | None:
    """Ovozli xabar/audio/video-xabar -> matn, rasm -> tavsif. Bo'lmasa None (oddiy "[media]" yoziladi)."""
    mime = (getattr(msg.file, "mime_type", None) or "").lower()
    try:
        if msg.voice or msg.video_note or mime.startswith("audio/"):
            ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-wav": "wav",
                   "audio/wav": "wav", "video/mp4": "mp4"}.get(mime, "ogg")
            spoken = await ai.transcribe(data, f"audio.{ext}")
            kind = "video-xabar" if msg.video_note else "ovozli xabar"
            log.info("%s matnga o'girildi (%d belgi)", kind, len(spoken))
            return f"[{kind}] {spoken}" if spoken else f"[{kind}: tushunarsiz yoki bo'sh]"
        if msg.photo or mime.startswith("image/"):
            desc = await ai.describe_image(data, mime if mime.startswith("image/") else "image/jpeg")
            log.info("Rasm tasvirlandi (%d belgi)", len(desc))
            return f"[rasm] {desc}" if desc else None
    except Exception as e:
        await _report_ai_error(e)
    return None


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
        if label and label != "stiker":
            size = getattr(msg.file, "size", 0) or 0
            if size <= MAX_MEDIA_BYTES:
                try:
                    data = await msg.download_media(file=bytes)
                except Exception:
                    log.exception("Media yuklab olinmadi")
        media_text = await _understand_media(msg, label, data) if data else None

        if media_text:
            content = f"{media_text}\n{text}".strip()
        else:
            content = (f"[media: {label}] " if label else "") + text
        if not content.strip():
            return
        await storage.add_history(chat_id, "user", content.strip())

        if data:
            try:
                await control_bot.send_media(chat_id, name, data, getattr(msg.file, "name", None))
            except Exception:
                log.exception("Media botga yuborilmadi")

        # Kontakt turi: oila -> AI javob bermaydi, faqat egasiga xabar
        tag = await storage.get_tag(sender.id)
        if tag == "oila":
            await storage.upsert_inbox(chat_id, name, username, "", False, content)
            await control_bot.notify(chat_id, f"👨‍👩‍👧 {name}", username, content, "", False, None)
            return
        kind = tag or ("tanish" if sender.contact else "notanish")

        if await storage.is_muted(chat_id):
            return  # egasi o'zi shu chatda gaplashyapti
        status = await availability.get_status()
        if not status.reply:
            log.info("%s ga javob berilmadi: %s", chat_id, status.reason)
            await storage.upsert_inbox(chat_id, name, username, "", False, content)
            if status.notify:
                await control_bot.notify(chat_id, name, username, content, "", False, None)
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
            try:
                result = await ai.generate(history, context)
            except Exception as e:
                await _report_ai_error(e)
                already = any(h["role"] == "assistant" for h in history)
                result = {"reply": None if already else FALLBACK_TEXT,
                          "summary": "", "urgent": False, "call_owner": False, "spam": False}

            # Notanishdan kelgan aniq spam: javob ham, bildirishnoma ham yo'q
            if result["spam"] and kind == "notanish":
                log.info("%s (%s) dan spam — javob berilmadi, bildirishnoma yuborilmadi", name, chat_id)
                await storage.upsert_inbox(chat_id, name, username, "🚫 spam", False, content)
                await storage.mark_handled(chat_id)
                return

            reply = result["reply"]
            if reply:
                is_voice_in = bool(msg.voice or msg.video_note)
                if not (is_voice_in and VOICE_REPLY_ENABLED and await _send_voice(user, chat_id, reply)):
                    async with user.action(chat_id, "typing"):
                        await asyncio.sleep(min(1 + len(reply) / 40, 5) + random.uniform(0, 1))
                    _ai_texts[chat_id].add(reply)
                    await user.send_message(chat_id, reply)
                _sent_times[chat_id].append(time.time())
                await storage.add_history(chat_id, "assistant", reply)

            # Suhbatdosh egasini chaqirishni so'radi -> alohida, shoshilinch ogohlantirish
            calling = result["call_owner"] and _call_allowed(chat_id)
            await storage.upsert_inbox(chat_id, name, username, result["summary"],
                                       result["urgent"] or calling, content)
            label_name = f"💼 {name}" if kind == "mijoz" else name
            if calling:
                log.info("%s (%s) egasini chaqiryapti — botga xabar berildi", name, chat_id)
                await control_bot.call_owner(chat_id, label_name, username, content, result["summary"])
            else:
                await control_bot.notify(chat_id, label_name, username, content,
                                         result["summary"], result["urgent"], reply)

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

        # Egasi istalgan joyga (guruh, kanal, shaxsiy) o'zi yozdi -> u faol (/auto rejimi uchun)
        await availability.mark_owner_active()

        if not event.is_private or chat_id == me_id:  # guruhlar va Saved Messages
            return
        # Egasi o'zi shu chatga yozdi -> AI shu chatda vaqtincha jim
        await storage.mute(chat_id, OWNER_TAKEOVER_MINUTES * 60)
        await storage.mark_handled(chat_id)
        if text:
            await storage.add_history(chat_id, "assistant", f"({OWNER_NAME} o'zi yozdi) {text}")
        log.info("Egasi %s chatiga o'zi yozdi — AI %d daqiqa jim", chat_id, OWNER_TAKEOVER_MINUTES)

