"""Kotibaning to'liq oqimi: soxta Telegram + soxta AI + xotiradagi baza."""
import contextlib
from collections import defaultdict, deque

import pytest
from telethon.tl.types import User

import ai
import availability
import control_bot
import userbot
import usage
from conftest import run


class File:
    size = 100
    name = None
    mime_type = "audio/ogg"


class Msg:
    def __init__(self, text, voice=False):
        self.message = text
        self.voice = True if voice else None
        self.video_note = self.photo = self.video = self.sticker = self.document = None
        self.media = True if voice else None
        self.file = File() if voice else None

    async def download_media(self, file=None):
        return b"fake-ogg"


class Ev:
    def __init__(self, chat, text="", voice=False, contact=False, private=True):
        self.chat_id = chat
        self.message = Msg(text, voice)
        self.is_private = private
        self._u = User(id=chat, first_name="Test", contact=contact, bot=False)

    async def get_sender(self):
        return self._u


class FakeUser:
    def __init__(self):
        self.handlers = {}
        self.sent = []

    def on(self, _event):
        def deco(f):
            self.handlers[f.__name__] = f
            return f
        return deco

    @contextlib.asynccontextmanager
    async def action(self, *a):
        yield

    async def send_message(self, chat, text):
        self.sent.append(("text", chat, text))
        await self.handlers["on_outgoing"](Ev(chat, text))       # Telegram o'z xabarimizni ham qaytaradi

    async def send_file(self, chat, f, voice_note=False):
        self.sent.append(("voice", chat, len(f.getvalue())))
        await self.handlers["on_outgoing"](Ev(chat, "", voice=True))


@pytest.fixture
def env(fake_storage, fast_sleep, monkeypatch):
    for name, value in [("_seq", defaultdict(int)), ("_sent_times", defaultdict(deque)),
                        ("_ai_texts", defaultdict(set)), ("_ai_voice_pending", defaultdict(int)),
                        ("_last_call", {}), ("_ai_alerted", set())]:
        monkeypatch.setattr(userbot, name, value)
    monkeypatch.setattr(availability, "_last_active_mem", 0.0)
    monkeypatch.setattr(usage, "_alerted", set())

    bot_msgs = []

    async def notify(chat_id, name, username, text, summary, urgent, reply):
        bot_msgs.append(("notify", chat_id, name))

    async def call_owner(chat_id, name, *a):
        bot_msgs.append(("call", chat_id, name))

    async def alert(text):
        bot_msgs.append(("alert", text))

    async def send_media(*a):
        bot_msgs.append(("media",))

    monkeypatch.setattr(control_bot, "notify", notify)
    monkeypatch.setattr(control_bot, "call_owner", call_owner)
    monkeypatch.setattr(control_bot, "alert", alert)
    monkeypatch.setattr(control_bot, "send_media", send_media)

    ai_calls = []

    def make_result(**kw):
        base = {"reply": "Salom! Men Anna.", "summary": "xulosa", "urgent": False,
                "call_owner": False, "spam": False, "tokens": 50}
        base.update(kw)
        return base

    state = {"result": make_result()}

    async def generate(history, context=""):
        ai_calls.append(context)
        return state["result"]

    async def transcribe(data, name):
        return "Husan aka qachon bo'shaydi?"

    async def speak(text):
        return b"OggS-fake-audio"

    monkeypatch.setattr(ai, "generate", generate)
    monkeypatch.setattr(ai, "transcribe", transcribe)
    monkeypatch.setattr(ai, "speak", speak)

    user = FakeUser()
    userbot.setup(user, me_id=1)

    class Env:
        pass
    e = Env()
    e.user, e.storage, e.bot, e.ai_calls, e.state, e.make = user, fake_storage, bot_msgs, ai_calls, state, make_result
    e.incoming = lambda ev: run(user.handlers["on_incoming"](ev))
    e.outgoing = lambda ev: run(user.handlers["on_outgoing"](ev))
    return e


def test_text_reply_and_notify(env):
    env.incoming(Ev(100, "2*2 nechi?"))
    assert env.user.sent == [("text", 100, "Salom! Men Anna.")]
    assert ("notify", 100, "Test") in env.bot
    assert 100 not in env.storage.d_muted                     # AI xabari "egasi yozdi" deb sanalmadi
    assert env.storage.d_stats["messages"] == 1 and env.storage.d_stats["chats"] == [100]
    assert "notanish" in env.ai_calls[0]


def test_family_gets_no_ai_reply(env):
    env.storage.d_tags[200] = "oila"
    env.incoming(Ev(200, "Uyga qachon kelasan?"))
    assert env.user.sent == [] and env.ai_calls == []
    assert env.bot[0][0] == "notify" and "👨‍👩‍👧" in env.bot[0][2]


def test_client_tone_passed_to_ai(env):
    env.storage.d_tags[201] = "mijoz"
    env.incoming(Ev(201, "Narxi qancha?"))
    assert "mijoz" in env.ai_calls[0]
    assert env.bot[-1][2].startswith("💼")


def test_contact_is_tanish(env):
    env.incoming(Ev(202, "salom", contact=True))
    assert "tanish" in env.ai_calls[0] and "notanish" not in env.ai_calls[0]


def test_spam_from_stranger_is_silent(env):
    env.state["result"] = env.make(spam=True)
    env.incoming(Ev(300, "Siz iPhone yutdingiz!"))
    assert env.user.sent == [] and env.bot == []
    assert env.storage.d_stats["spam"] == 1


def test_spam_flag_ignored_for_known_contact(env):
    env.state["result"] = env.make(spam=True)
    env.incoming(Ev(301, "havola: bit.ly/x", contact=True))
    assert env.user.sent  # tanishga baribir javob beriladi


def test_voice_in_voice_out(env, monkeypatch):
    # bitta ovozli xabar = 3 AI so'rovi (matnga o'girish + javob + ovozli javob)
    monkeypatch.setattr(usage.config, "AI_CHAT_DAILY_LIMIT", 10)
    env.incoming(Ev(400, voice=True))
    kinds = [s[0] for s in env.user.sent]
    assert kinds == ["voice"]
    assert 400 not in env.storage.d_muted                     # AI ovozi egasi yozgan deb sanalmadi
    assert env.storage.d_history[400][0]["content"].startswith("[ovozli xabar]")
    assert env.storage.d_stats["ai_voice"] == 1 and env.storage.d_stats["ai_tts"] == 1


def test_voice_reply_falls_back_to_text_when_limit_reached(env):
    # AI_CHAT_DAILY_LIMIT=2: matnga o'girish + javob limitni tugatadi -> ovoz o'rniga matn
    env.incoming(Ev(401, voice=True))
    assert [s[0] for s in env.user.sent] == ["text"]


def test_call_owner(env):
    env.state["result"] = env.make(call_owner=True)
    env.incoming(Ev(500, "ha, chaqiring"))
    assert ("call", 500, "Test") in env.bot
    assert env.storage.d_stats["calls"] == 1


def test_owner_takeover_mutes_chat(env):
    env.outgoing(Ev(600, "Men o'zim yozaman"))
    assert 600 in env.storage.d_muted
    env.incoming(Ev(600, "yana savol"))
    assert env.user.sent == [] and env.ai_calls == []


def test_owner_writing_in_group_marks_active_only(env):
    env.outgoing(Ev(-100123, "guruhga yozdim", private=False))
    assert -100123 not in env.storage.d_muted
    assert availability._last_active_mem > 0


def test_auto_mode_owner_active_stays_silent(env):
    env.storage.d_mode = "auto"
    env.outgoing(Ev(-100123, "guruhga yozdim", private=False))  # egasi faol
    env.incoming(Ev(700, "salom"))
    assert env.user.sent == [] and env.bot == []


def test_off_mode_notifies_only(env):
    env.storage.d_mode = "off"
    env.incoming(Ev(701, "salom"))
    assert env.user.sent == [] and env.bot == [("notify", 701, "Test")]


def test_ai_limit_falls_back_to_template_and_alerts_once(env):
    # AI_CHAT_DAILY_LIMIT=2 (conftest)
    for text in ("1", "2"):
        env.incoming(Ev(800, text))
    assert len(env.ai_calls) == 2
    env.incoming(Ev(800, "3"))
    env.incoming(Ev(800, "4"))
    assert len(env.ai_calls) == 2                              # limitdan keyin AI chaqirilmadi
    alerts = [m for m in env.bot if m[0] == "alert"]
    assert len(alerts) == 1 and "limit" in alerts[0][1]


def test_template_sent_once_when_ai_fails(env, monkeypatch):
    async def boom(history, context=""):
        raise RuntimeError("AI ishlamayapti")
    monkeypatch.setattr(ai, "generate", boom)
    env.incoming(Ev(900, "salom"))
    env.incoming(Ev(900, "yana"))
    texts = [s for s in env.user.sent if s[0] == "text"]
    assert len(texts) == 1 and "band" in texts[0][2]           # shablon faqat bir marta
