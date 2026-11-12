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
