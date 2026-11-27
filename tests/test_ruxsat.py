"""🔑 /ruxsat: boshqa odamga botdan to'liq foydalanish ruxsati."""
import pytest

import control_bot
import storage
from conftest import run


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kw):
        self.sent.append((chat, text))
        return type("M", (), {"id": len(self.sent)})()

    async def get_entity(self, x):
        raise ValueError("yo'q")

    async def get_input_entity(self, x):
        raise ValueError("yo'q")

    async def __call__(self, req):
        return True


class User:
    async def get_entity(self, x):
        return type("U", (), {"first_name": "Ali", "last_name": None, "username": "ali", "title": None})()


@pytest.fixture
def rx(monkeypatch):
    db = {}

    async def add(uid, label):
        db[uid] = label

    async def remove(uid):
        return db.pop(uid, None) is not None

    async def lst():
        return [{"user_id": k, "label": v} for k, v in db.items()]

    monkeypatch.setattr(storage, "bot_users_add", add, raising=False)
    monkeypatch.setattr(storage, "bot_users_remove", remove, raising=False)
    monkeypatch.setattr(storage, "bot_users_list", lst, raising=False)
    monkeypatch.setattr(control_bot, "_allowed", {})
    monkeypatch.setattr(control_bot, "_notif_map", {})
    bot = Bot()
    monkeypatch.setattr(control_bot.ctx, "bot", bot, raising=False)
    monkeypatch.setattr(control_bot.ctx, "user", User(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "main_owner", 5626042722, raising=False)
    return bot, db


def test_grant_and_revoke(rx):
    bot, db = rx
    assert not control_bot.is_allowed(123456789)
    text = run(control_bot.grant("123456789"))
    assert "to'liq ruxsat" in text and db == {123456789: "Ali (@ali)"}
    assert control_bot.is_allowed(123456789) and control_bot.recipients() == [5626042722, 123456789]
    assert any(chat == 123456789 for chat, _ in bot.sent)          # unga xabar ketdi
    text, rows = control_bot.allowed_panel()
    assert "123456789" in text
    run(control_bot.revoke(123456789))
    assert not control_bot.is_allowed(123456789) and db == {}
    assert "hech kimga" in control_bot.allowed_panel()[0]


def test_owner_cannot_grant_self_and_bad_input(rx):
    assert "o'zingiz" in run(control_bot.grant("5626042722"))
    assert "topilmadi" in run(control_bot.grant("salom!"))


def test_notifications_go_to_everyone(rx):
    bot, _ = rx
    run(control_bot.grant("123456789"))
    bot.sent.clear()
    run(control_bot.alert("diqqat"))
    assert sorted(chat for chat, _ in bot.sent) == [123456789, 5626042722]


def test_answer_goes_only_to_who_asked(rx):
    bot, _ = rx
    run(control_bot.grant("123456789"))
    bot.sent.clear()

    async def inside_handler():
        token = control_bot._requester.set(123456789)
        try:
            await control_bot.alert("faqat sizga")
            return control_bot.ctx.owner_id
        finally:
            control_bot._requester.reset(token)

    assert run(inside_handler()) == 123456789
    assert [chat for chat, _ in bot.sent] == [123456789]
    assert control_bot.ctx.owner_id == 5626042722                  # handler tashqarisida — asosiy ega


def test_load_allowed(rx):
    _, db = rx
    db[555] = "Vali"
    run(control_bot.load_allowed())
    assert control_bot.is_allowed(555)
