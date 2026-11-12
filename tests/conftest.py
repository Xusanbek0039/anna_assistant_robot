"""Testlar uchun umumiy sozlamalar.

- Haqiqiy .env o'qilmaydi (maxfiy qiymatlar va haqiqiy MongoDB ishlatilmaydi).
- Tarmoqqa (Telegram, OpenAI, Open-Meteo, MongoDB) hech qanday so'rov yuborilmaydi.
"""
import asyncio
import os
import sys
from pathlib import Path

import dotenv
import pytest

dotenv.load_dotenv = lambda *a, **k: False  # config.py import qilinishidan OLDIN

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.update({
    "API_ID": "1", "API_HASH": "testhash12345", "PHONE": "", "BOT_TOKEN": "1:testtoken",
    "OPENAI_API_KEY": "sk-test-key", "MONGODB_URI": "", "CALENDAR_ICS_URL": "",
    "DRY_RUN": "true", "BIO_ENABLED": "true", "BIO_PREFIX": "Full Stack Developer",
    "BIO_TEMPLATE": "{prefix} | 🕒 {time} | {icon} {temp}°C", "BIO_MAX_LENGTH": "0",
    "BIO_QUOTE": "", "BIO_QUOTE_ENABLED": "true", "NAME_CLOCK_ENABLED": "true",
    "NAME_TEMPLATE": "{last_name} 🕒 {time}", "TIMEZONE": "Asia/Tashkent",
    "BUSY_HOURS": "", "OWNER_ACTIVE_MINUTES": "10", "KEEP_ONLINE": "true",
    "AI_DAILY_LIMIT": "5", "AI_CHAT_DAILY_LIMIT": "2", "VOICE_REPLY_ENABLED": "true",
    "REPLY_DELAY_SECONDS": "0", "MAX_REPLIES_PER_MINUTE": "100", "REPORT_TIME": "21:00",
})


class FakeStorage:
    """MongoDB o'rniga xotiradagi baza (storage.py bilan bir xil interfeys)."""

    def __init__(self):
        self.d_history: dict[int, list] = {}
        self.d_tags: dict[int, str] = {}
        self.d_muted: set[int] = set()
        self.d_mode = "on"
        self.d_owner_active = 0.0
        self.d_inbox: dict[int, dict] = {}
        self.d_stats: dict = {}
        self.d_runtime: dict = {}
        self.d_bio: dict = {}
        self.d_ignored: set[int] = set()
        self.d_ping_error: Exception | None = None

    async def ping(self):
        if self.d_ping_error:
            raise self.d_ping_error

    async def get_stats(self, day=None):
        return self.d_stats

    async def stat_inc(self, chat_id=None, day=None, **counters):
        for key, val in counters.items():
            if "." in key:
                outer, inner = key.split(".", 1)
                bucket = self.d_stats.setdefault(outer, {})
                bucket[inner] = bucket.get(inner, 0) + val
            else:
                self.d_stats[key] = self.d_stats.get(key, 0) + val
        if chat_id is not None and chat_id not in self.d_stats.setdefault("chats", []):
            self.d_stats["chats"].append(chat_id)

    async def get_mode(self):
        return self.d_mode

    async def set_mode(self, mode):
        self.d_mode = mode

    async def set_owner_active(self, ts):
        self.d_owner_active = ts

    async def get_owner_active(self):
        return self.d_owner_active

    async def is_ignored(self, uid):
        return uid in self.d_ignored

    async def add_history(self, chat_id, role, content):
        self.d_history.setdefault(chat_id, []).append({"role": role, "content": content})

    async def get_history(self, chat_id, limit=20):
        return list(self.d_history.get(chat_id, []))[-limit:]

    async def get_tag(self, uid):
        return self.d_tags.get(uid)

    async def is_muted(self, chat_id):
        return chat_id in self.d_muted

    async def mute(self, chat_id, seconds):
        self.d_muted.add(chat_id)

    async def upsert_inbox(self, chat_id, name, username, summary, urgent, last_text):
        self.d_inbox[chat_id] = {"chat_id": chat_id, "name": name, "summary": summary,
                                 "urgent": urgent, "last_text": last_text, "handled": False}

    async def mark_handled(self, chat_id):
        if chat_id in self.d_inbox:
            self.d_inbox[chat_id]["handled"] = True

    async def pending_inbox(self):
        return [r for r in self.d_inbox.values() if not r["handled"]]

    async def get_runtime(self):
        return dict(self.d_runtime)

    async def set_runtime(self, **values):
        self.d_runtime.update(values)

    async def get_bio_state(self):
        return dict(self.d_bio)

    async def save_bio_state(self, **fields):
        self.d_bio.update(fields)

    async def save_original_bio_once(self, about, saved_at):
        if "original_bio" in self.d_bio:
            return False
        self.d_bio.update(original_bio=about, original_saved_at=saved_at)
        return True


@pytest.fixture
def fake_storage(monkeypatch):
    import storage
    fs = FakeStorage()
    for name in dir(fs):
        if not name.startswith(("_", "d_")):
            monkeypatch.setattr(storage, name, getattr(fs, name))
    return fs


@pytest.fixture
def fast_sleep(monkeypatch):
    """asyncio.sleep ni darhol qaytadigan qiladi (testlar sekin bo'lmasligi uchun)."""
    real = asyncio.sleep

    async def _sleep(delay, result=None):
        return await real(0, result)

    monkeypatch.setattr(asyncio, "sleep", _sleep)


def run(coro):
    return asyncio.run(coro)
