"""Nazorat botini ulash: sessiya eskirsa Telethon qotib qolardi (serverda jim restart sikli)."""
import asyncio
import os

import pytest
from telethon import errors

import config
import tg_client
from conftest import run


class FakeBot:
    """TelegramClient o'rniga: start/get_me xatti-harakati sinovdan beriladi."""
    created: list = []

    def __init__(self, session, api_id, api_hash):
        self.session = session
        FakeBot.created.append(self)
        self.started = self.disconnected = False
        self.behaviour = FakeBot.next_behaviour
        FakeBot.next_behaviour = "ok"

    async def start(self, bot_token=None):
        self.started = True
        self.token = bot_token
        if self.behaviour == "hang":
            await asyncio.sleep(3600)
        if self.behaviour == "bad_token":
            raise errors.AccessTokenInvalidError(request=None)
        if self.behaviour == "flood":
            raise errors.FloodWaitError(request=None)
        if self.behaviour == "boom":
            raise RuntimeError("sessiya buzilgan")

    async def get_me(self):
        return None if self.behaviour == "no_me" else type("Me", (), {"username": "AnnaBot", "id": 7})()

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def bot_env(monkeypatch, tmp_path):
    FakeBot.created = []
    FakeBot.next_behaviour = "ok"
    monkeypatch.setattr(tg_client, "TelegramClient", FakeBot)
    monkeypatch.setattr(config, "BOT_SESSION", str(tmp_path / "anna_bot"))
    monkeypatch.setattr(config, "BOT_TOKEN", "1:testtoken")
    monkeypatch.setattr(tg_client, "BOT_CONNECT_TIMEOUT", 0.05)
    monkeypatch.setattr(tg_client, "BOT_ME_TIMEOUT", 0.05)
    return FakeBot


def session_file() -> str:
    return tg_client._bot_session_files()[0]


def test_connects_normally(bot_env):
    bot, me = run(tg_client.start_bot())
    assert me.username == "AnnaBot" and bot.token == "1:testtoken"
    assert len(bot_env.created) == 1 and not bot.disconnected


def test_hanging_start_times_out_and_retries_with_clean_session(bot_env):
    """Asosiy holat: eski sessiya bilan Telethon javob bermay qotib qoladi."""
    open(session_file(), "w").close()
    bot_env.next_behaviour = "hang"
    bot, me = run(tg_client.start_bot())
    assert me.username == "AnnaBot"
    assert len(bot_env.created) == 2                     # ikkinchi urinish
    assert bot_env.created[0].disconnected               # birinchisi uzildi
    assert not os.path.exists(session_file())            # buzuq sessiya tozalandi


def test_broken_session_retried_once(bot_env):
    open(session_file(), "w").close()
    bot_env.next_behaviour = "boom"
    bot, me = run(tg_client.start_bot())
    assert len(bot_env.created) == 2 and me.username == "AnnaBot"


def test_second_failure_exits_with_clear_message(bot_env, monkeypatch):
    class AlwaysBad(FakeBot):
        async def start(self, bot_token=None):
            raise RuntimeError("tarmoq yo'q")

    monkeypatch.setattr(tg_client, "TelegramClient", AlwaysBad)
    with pytest.raises(SystemExit) as e:
        run(tg_client.start_bot())
    assert "BOT_TOKEN" in str(e.value) and "internet" in str(e.value)


def test_invalid_token_exits_immediately_without_wiping(bot_env):
    open(session_file(), "w").close()
    bot_env.next_behaviour = "bad_token"
    with pytest.raises(SystemExit) as e:
        run(tg_client.start_bot())
    assert "BotFather" in str(e.value)
    assert len(bot_env.created) == 1                     # qayta urinilmaydi
    assert os.path.exists(session_file())


def test_flood_wait_waits_and_retries_instead_of_exiting(bot_env, monkeypatch):
    """FloodWait — dastur to'xtamaydi (to'xtasa server qayta ishga tushirib cheklovni uzaytirardi): kutib,
    qayta ulanadi va egasiga xabar beradi."""
    slept, told = [], []
    real_sleep = asyncio.sleep

    async def fake_sleep(sec, *a):
        slept.append(sec)
        await real_sleep(0)

    async def notify(text):
        told.append(text)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    bot_env.next_behaviour = "flood"
    bot, me = run(tg_client.start_bot(notify=notify))
    assert me.username == "AnnaBot" and len(bot_env.created) == 2
    assert slept and "kutyapman" in told[0]


def test_saved_session_from_db_is_reused(bot_env, monkeypatch):
    """Render qayta ishga tushganda fayl yo'qoladi — bazadagi sessiya bilan qayta login qilinmaydi."""
    import storage
    saved = {}

    async def get():
        return {"string": "SAVED", "bot_id": "1"} if saved.get("ok") else None

    async def set_(string, bot_id):
        saved.update(ok=True, bot_id=bot_id)

    async def delete():
        saved.clear()

    monkeypatch.setattr(storage, "bot_get_session", get, raising=False)
    monkeypatch.setattr(storage, "bot_set_session", set_, raising=False)
    monkeypatch.setattr(storage, "bot_delete_session", delete, raising=False)
    monkeypatch.setattr(tg_client, "StringSession", type("SS", (), {
        "__init__": lambda self, s=None: setattr(self, "s", s), "save": staticmethod(lambda sess: "STR")}))
    run(tg_client.start_bot())
    assert saved == {"ok": True, "bot_id": "1"}           # birinchi ulanishdan keyin bazaga yozildi
    run(tg_client.start_bot())
    assert getattr(bot_env.created[-1].session, "s", None) == "SAVED"   # ikkinchi safar — bazadagi sessiya


def test_get_me_returning_none_is_treated_as_bad_session(bot_env):
    bot_env.next_behaviour = "no_me"
    bot, me = run(tg_client.start_bot())
    assert len(bot_env.created) == 2 and me.username == "AnnaBot"


def test_wipe_removes_both_session_files(bot_env):
    for path in tg_client._bot_session_files():
        open(path, "w").close()
    assert tg_client._wipe_bot_session() is True
    assert not any(os.path.exists(p) for p in tg_client._bot_session_files())
    assert tg_client._wipe_bot_session() is False        # fayl yo'q — xato bermaydi
