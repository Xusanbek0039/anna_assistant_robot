"""Shaxsiy akkaunt (userbot): kiruvchi shaxsiy xabarlarga AI orqali javob beradi."""
import asyncio
import logging
import random
import time
from collections import defaultdict, deque

from telethon import TelegramClient, events
from telethon.tl.types import User

import ai
import control_bot
import storage
from config import (FALLBACK_TEXT, MAX_REPLIES_PER_MINUTE, OWNER_NAME, OWNER_TAKEOVER_MINUTES,
                    REPLY_DELAY_SECONDS)

log = logging.getLogger("userbot")

TELEGRAM_SERVICE_ID = 777000           # login kodlari keladigan rasmiy akkaunt — hech qachon tegmaymiz
MAX_MEDIA_BYTES = 20 * 1024 * 1024

_seq: dict[int, int] = defaultdict(int)             # debounce: har bir chat uchun oxirgi xabar tartibi
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_sent_times: dict[int, deque] = defaultdict(deque)  # rate-limit
_ai_texts: dict[int, set[str]] = defaultdict(set)   # AI yuborgan matnlar (egasi yozganidan farqlash uchun)


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


def _rate_ok(chat_id: int) -> bool:
    q, now = _sent_times[chat_id], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    return len(q) < MAX_REPLIES_PER_MINUTE


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
        content = (f"[media: {label}] " if label else "") + text
        if not content.strip():
            return
        await storage.add_history(chat_id, "user", content.strip())

        name, username = _display_name(sender), sender.username

        # Media'ni egasiga bot orqali yuboramiz
        if label and label != "stiker":
            size = getattr(msg.file, "size", 0) or 0
            if size <= MAX_MEDIA_BYTES:
                try:
                    data = await msg.download_media(file=bytes)
                    await control_bot.send_media(chat_id, name, data, getattr(msg.file, "name", None))
                except Exception:
                    log.exception("Media yuklab olinmadi")

        if not await storage.is_enabled() or await storage.is_muted(chat_id):
            # AI javob bermaydi, lekin egasi baribir xabardor bo'ladi
            await storage.upsert_inbox(chat_id, name, username, "", False, content)
            if not await storage.is_muted(chat_id):
                await control_bot.notify(chat_id, name, username, content, "", False, None)
            return

        # Debounce: odam ketma-ket bir nechta xabar yozsa, oxirgisiga bitta javob beramiz
        _seq[chat_id] += 1
        my_seq = _seq[chat_id]
        await asyncio.sleep(REPLY_DELAY_SECONDS)
        if my_seq != _seq[chat_id]:
            return

        async with _locks[chat_id]:
            if await storage.is_muted(chat_id) or not await storage.is_enabled():
                return
            if not _rate_ok(chat_id):
                log.warning("Rate-limit: %s", chat_id)
                return

            history = await storage.get_history(chat_id)
            try:
                result = await ai.generate(history)
            except Exception:
                log.exception("OpenAI xatosi — shablon javob ishlatiladi")
                already = any(h["role"] == "assistant" for h in history)
                result = {"reply": None if already else FALLBACK_TEXT,
                          "summary": "", "urgent": False}

            reply = result["reply"]
            if reply:
                async with user.action(chat_id, "typing"):
                    await asyncio.sleep(min(1 + len(reply) / 40, 5) + random.uniform(0, 1))
                _ai_texts[chat_id].add(reply)
                await user.send_message(chat_id, reply)
                _sent_times[chat_id].append(time.time())
                await storage.add_history(chat_id, "assistant", reply)

            await storage.upsert_inbox(chat_id, name, username, result["summary"], result["urgent"], content)
            await control_bot.notify(chat_id, name, username, content,
                                     result["summary"], result["urgent"], reply)

    @user.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
    async def on_outgoing(event):
        chat_id = event.chat_id
        text = event.message.message or ""
        if text in _ai_texts[chat_id]:
            _ai_texts[chat_id].discard(text)  # bu AI'ning o'z javobi
            return
        if chat_id == me_id:  # Saved Messages
            return
        # Egasi o'zi yozdi -> AI shu chatda vaqtincha jim
        await storage.mute(chat_id, OWNER_TAKEOVER_MINUTES * 60)
        await storage.mark_handled(chat_id)
        if text:
            await storage.add_history(chat_id, "assistant", f"({OWNER_NAME} o'zi yozdi) {text}")
        log.info("Egasi %s chatiga o'zi yozdi — AI %d daqiqa jim", chat_id, OWNER_TAKEOVER_MINUTES)
