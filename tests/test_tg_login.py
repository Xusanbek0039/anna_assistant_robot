"""🔑 Telegram akkauntni nazorat boti orqali ulash (tg_login.py)."""
import asyncio

import pytest
from telethon import errors

import config
import storage
import tg_login
from conftest import run


class Store:
    def __init__(self, owner=None, session=None):
        self.owner, self.session, self.saved = owner, session, None

    async def tg_get_owner(self):
        return self.owner

    async def tg_set_owner(self, uid):
        self.owner = uid

    async def tg_get_session(self, slot=1):
        return {"string": self.session} if self.session and slot == 1 else None

    async def tg_set_session(self, string, uid, slot=1):
        self.saved = (string, uid) if slot == 1 else (string, uid, slot)


@pytest.fixture
def store(monkeypatch):
    st = Store(owner=42)
    for name in ("tg_get_owner", "tg_set_owner", "tg_get_session", "tg_set_session"):
        monkeypatch.setattr(storage, name, getattr(st, name), raising=False)
    monkeypatch.setattr(config, "OWNER_ID", 0)
    monkeypatch.setattr(config, "PHONE", "")
    monkeypatch.setattr(tg_login.StringSession, "save", staticmethod(lambda s: "SESSION"))
    return st


class Event:
    def __init__(self, text, sender=42, private=True):
        self.raw_text, self.sender_id, self.is_private = text, sender, private
        self.deleted = False
        self.responses = []

    async def delete(self):
        self.deleted = True

    async def respond(self, text, **kw):
        self.responses.append(text)


class Bot:
    def __init__(self):
        self.sent, self.handler = [], None

    def add_event_handler(self, handler, _event):
        self.handler = handler

    def remove_event_handler(self, handler):
        self.handler = None

    async def send_message(self, chat, text, **kw):
        self.sent.append((chat, text))


class User:
    """TelegramClient o'rniga: kod 12345, 2FA paroli «sir»."""

    def __init__(self, password=False):
        self.password, self.session, self.authorized = password, object(), False
        self.codes_sent, self.signed = [], []

    def is_connected(self):
        return True

    async def send_code_request(self, phone):
        self.codes_sent.append(phone)
        return type("Sent", (), {"phone_code_hash": "h"})()

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        if password is not None:
            if password != "sir":
                raise errors.PasswordHashInvalidError(request=None)
        elif code != "12345":
            raise errors.PhoneCodeInvalidError(request=None)
        elif self.password:
            raise errors.SessionPasswordNeededError(request=None)
        self.signed.append(phone or "password")
        self.authorized = True

    async def get_me(self):
        return type("Me", (), {"id": 42, "first_name": "Husan"})()


async def _drive(bot, user, messages):
    """via_bot ni ishga tushirib, egasining xabarlarini birma-bir yuboradi."""
    task = asyncio.create_task(tg_login.via_bot(bot, user))
    events = []
    for text in messages:
        for _ in range(50):
            await asyncio.sleep(0)
        ev = text if isinstance(text, Event) else Event(text)
        events.append(ev)
        await bot.handler(ev)
    await asyncio.wait_for(task, 2)
    return events


def test_normalize_phone_and_code():
    assert tg_login.normalize_phone("+998 90 123-45-67") == "+998901234567"
    assert tg_login.normalize_phone("90 123 45 67") == "+998901234567"
    assert tg_login.normalize_phone("salom") is None
    assert tg_login.code_digits("1 2 3 4 5") == "12345"
    assert tg_login.code_digits("12") is None


def test_login_with_code_and_password(store):
    bot, user = Bot(), User(password=True)
    evs = run(_drive(bot, user, ["+998901234567", "1 2 3 4 0", "1 2 3 4 5", "xato", "sir"]))
    assert user.codes_sent == ["+998901234567"]
    assert user.authorized
    assert all(e.deleted for e in evs[1:])           # kod va parolli xabarlar o'chirildi
    assert not evs[0].deleted
    assert store.saved == ("SESSION", 42)            # sessiya bazaga saqlandi
    texts = " ".join(t for _, t in bot.sent)
    assert "Kod noto'g'ri" in texts and "Parol noto'g'ri" in texts and "Akkauntga kirildi" in texts
    assert bot.handler is None                       # vaqtinchalik ishlovchi olib tashlandi


def test_stranger_is_ignored_when_owner_known(store):
    bot, user = Bot(), User()
    stranger = Event("+998900000000", sender=99)
    run(_drive(bot, user, [stranger, "+998901234567", "1 2 3 4 5"]))
    assert user.codes_sent == ["+998901234567"]


def test_setup_code_required_when_owner_unknown(store, monkeypatch, caplog):
    store.owner = None
    monkeypatch.setattr(tg_login.secrets, "randbelow", lambda n: 123456)
    bot, user = Bot(), User()
    wrong = Event("/tglogin 000000", sender=7)
    run(_drive(bot, user, [wrong, Event("/tglogin 123456", sender=7),
                           Event("+998901234567", sender=7), Event("1 2 3 4 5", sender=7)]))
    assert wrong.responses                           # noto'g'ri kod — ruxsat yo'q
    assert user.authorized
    assert "/tglogin 123456" in caplog.text          # kod faqat server logida


def test_expired_code_asks_phone_again(store):
    class Expiring(User):
        first = True

        async def sign_in(self, phone=None, code=None, **kw):
            if self.first:
                self.first = False
                raise errors.PhoneCodeExpiredError(request=None)
            return await super().sign_in(phone, code, **kw)

    bot, user = Bot(), Expiring()
    run(_drive(bot, user, ["+998901234567", "1 2 3 4 5", "+998901234567", "1 2 3 4 5"]))
    assert len(user.codes_sent) == 2 and user.authorized


def test_open_client_prefers_working_session(store, monkeypatch, tmp_path):
    store.session = "DBSTRING"
    made = []

    class Client:
        def __init__(self, session):
            self.session = session
            made.append(self)

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return self.session == ("string", "DBSTRING")   # fayl sessiyasi bekor, bazadagisi ishlaydi

        async def disconnect(self):
            self.closed = True

    monkeypatch.setattr(tg_login, "create_user_client", Client)
    monkeypatch.setattr(tg_login, "StringSession", lambda s=None: ("string", s))
    monkeypatch.setattr(config, "SESSION_STRING", "")
    monkeypatch.setattr(config, "USER_SESSION", str(tmp_path / "anna"))
    (tmp_path / "anna.session").write_text("x")
    client = run(tg_login.open_client())
    assert len(made) == 2 and made[0].closed
    assert client is made[1]


def test_open_client_skips_broken_string(store, monkeypatch, tmp_path):
    store.session = "buzilgan"
    monkeypatch.setattr(config, "SESSION_STRING", "")
    monkeypatch.setattr(config, "USER_SESSION", str(tmp_path / "anna"))
    monkeypatch.setattr(tg_login, "has_terminal", lambda: False)
    client = run(tg_login.open_client())      # ValueError bilan yiqilmaydi — bo'sh sessiya (bot login)
    assert isinstance(client.session, tg_login.StringSession) and client.session.auth_key is None


def test_hanging_session_is_skipped(store, monkeypatch, tmp_path):
    """Telegram javob bermasa — cheksiz kutilmaydi, keyingi sessiya sinaladi."""
    import asyncio
    store.session = "DB"
    made = []

    class Client:
        def __init__(self, session):
            self.session = session
            made.append(self)

        async def connect(self):
            if self.session == ("string", "ENV"):
                await asyncio.sleep(3600)        # qotib qolgan ulanish

        async def is_user_authorized(self):
            return True

        async def disconnect(self):
            self.closed = True

    monkeypatch.setattr(tg_login, "create_user_client", Client)
    monkeypatch.setattr(tg_login, "StringSession", lambda s=None: ("string", s))
    monkeypatch.setattr(tg_login, "CONNECT_TIMEOUT", 0.05)
    monkeypatch.setattr(config, "SESSION_STRING", "ENV")
    monkeypatch.setattr(config, "USER_SESSION", str(tmp_path / "anna"))
    client = run(tg_login.open_client())
    assert client.session == ("string", "DB") and made[0].closed
