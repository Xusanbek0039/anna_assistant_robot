import re

import pytest

import bio_updater
import control_bot
import runtime
from conftest import run


# ---------- "/" menyusi ----------
def test_menu_commands_meet_telegram_rules():
    names = [c for c, _ in control_bot.BOT_COMMANDS]
    assert len(names) == len(set(names)), "takroriy buyruq"
    for cmd, desc in control_bot.BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", cmd), cmd
        assert 3 <= len(desc) <= 256, cmd


def test_every_menu_command_has_handler():
    """Menyudagi har bir buyruq uchun control_bot.py da ishlovchi (pattern) bor."""
    import inspect
    source = inspect.getsource(control_bot)
    for cmd, _ in control_bot.BOT_COMMANDS:
        assert re.search(rf'pattern=r"\^/(\(start\|help\)|{cmd}\b)', source) or \
            (cmd == "help" and "start|help" in source), f"/{cmd} uchun ishlovchi yo'q"


def test_set_commands_owner_scope_and_fallback():
    from telethon.tl.types import BotCommandScopeDefault, BotCommandScopePeer

    class Bot:
        def __init__(self, fail_peer):
            self.fail_peer, self.calls = fail_peer, []

        async def get_input_entity(self, uid):
            if self.fail_peer:
                raise ValueError("egasi hali /start bosmagan")
            return "peer"

        async def __call__(self, req):
            self.calls.append((type(req.scope), len(req.commands)))

    ok = Bot(fail_peer=False)
    run(control_bot.set_commands(ok, 1))
    n = len(control_bot.BOT_COMMANDS)
    assert ok.calls == [(BotCommandScopePeer, n), (BotCommandScopeDefault, 0)]  # begonalarga menyu yo'q

    fallback = Bot(fail_peer=True)
    run(control_bot.set_commands(fallback, 1))
    assert fallback.calls == [(BotCommandScopeDefault, n)]


# ---------- boshqaruv paneli ----------
@pytest.fixture
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    monkeypatch.setattr(runtime, "_listeners", [])
    monkeypatch.setattr(bio_updater, "INSTANCE", None)


def test_panel_buttons_valid(fake_storage, fresh_runtime):
    text, rows = run(control_bot._panel())
    assert "Boshqaruv paneli" in text
    for row in rows:
        for btn in row:
            data = getattr(btn, "data", None) or btn.type.data  # Telethon versiyasiga qarab
            assert data.startswith(b"cfg:") and len(data) <= 64  # Telegram cheklovi
            action = data.decode()[4:]
            assert run(control_bot._do_action(action)) != "Noma'lum amal", action


@pytest.mark.parametrize("action,check", [
    ("mode_auto", lambda fs: fs.d_mode == "auto"),
    ("mode_off", lambda fs: fs.d_mode == "off"),
    ("bio_off", lambda fs: runtime.get("bio_enabled") is False),
    ("quote_off", lambda fs: runtime.get("quote_mode") == "off"),
    ("online_off", lambda fs: runtime.get("online") is False),
])
def test_panel_actions(fake_storage, fresh_runtime, action, check):
    run(control_bot._do_action(action))
    assert check(fake_storage)


def test_panel_marks_current_mode_and_toggles(fake_storage, fresh_runtime):
    fake_storage.d_mode = "auto"
    run(control_bot._do_action("online_off"))
    _, rows = run(control_bot._panel())
    labels = [b.text for row in rows for b in row]
    assert "✅ 🤖 Aqlli" in labels
    assert "🟢 Online yoqish" in labels           # o'chiq bo'lsa — "yoqish" tugmasi chiqadi


def test_bio_actions_without_module(fake_storage, fresh_runtime):
    assert "ishlamayapti" in run(control_bot._do_action("bio_now"))
    assert "ishlamayapti" in run(control_bot._do_action("bio_restore"))


class _FakeMsg:
    def __init__(self, i):
        self.id = i


class _FakeBot:
    def __init__(self, fail_first=False):
        self.files, self.texts, self.fail_first = [], [], fail_first

    async def send_file(self, chat, f, **kw):
        if self.fail_first and not self.files:
            self.files.append(None)
            raise RuntimeError("boom")
        self.files.append((f.name, f.read(), kw))
        return _FakeMsg(len(self.files))

    async def send_message(self, chat, text, **kw):
        self.texts.append((text, kw))
        return _FakeMsg(100 + len(self.texts))


def _media_bot(monkeypatch, **kw):
    bot = _FakeBot(**kw)
    monkeypatch.setattr(control_bot.ctx, "bot", bot, raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    monkeypatch.setattr(control_bot, "_notif_map", {})
    return bot


def test_send_media_photo_goes_as_photo(monkeypatch):
    bot = _media_bot(monkeypatch)
    run(control_bot.send_media(5, "Ali <b>", b"jpg", "photo.jpg", kind="rasm", view_once=True, caption="a<b"))
    name, data, kw = bot.files[0]
    assert name == "photo.jpg" and data == b"jpg"
    assert kw["force_document"] is False and kw["voice_note"] is False
    assert "Bir marta" in kw["caption"] and "a&lt;b" in kw["caption"] and "Ali &lt;b&gt;" in kw["caption"]
    assert control_bot._notif_map == {1: 5}


def test_send_media_voice_and_round_video(monkeypatch):
    bot = _media_bot(monkeypatch)
    run(control_bot.send_media(5, "Ali", b"ogg", "voice.ogg", kind="ovozli xabar"))
    assert bot.files[0][2]["voice_note"] is True
    run(control_bot.send_media(5, "Ali", b"mp4", "video_note.mp4", kind="video-xabar"))
    kw = bot.files[1][2]
    assert kw["video_note"] is True and kw["caption"] is None
    assert len(bot.texts) == 1 and bot.texts[0][1]["reply_to"] == 2   # izoh alohida xabar bo'lib keladi


def test_send_media_falls_back_to_document(monkeypatch):
    bot = _media_bot(monkeypatch, fail_first=True)
    run(control_bot.send_media(5, "Ali", b"x", "video.mp4", kind="video"))
    assert bot.files[1][2]["force_document"] is True and bot.files[1][1] == b"x"
