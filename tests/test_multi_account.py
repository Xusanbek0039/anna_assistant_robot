"""📱 2-akkaunt: bildirishnomalar va javoblar to'g'ri akkauntga bog'lanadi."""
import pytest

import control_bot
import groups
import runtime
from conftest import run


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kw):
        self.sent.append((text, kw))
        return type("M", (), {"id": len(self.sent)})()


class Client:
    def __init__(self, uid):
        self.uid, self.sent, self.disconnected = uid, [], False

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def get_me(self):
        return type("Me", (), {"id": self.uid, "first_name": "Ikkinchi", "last_name": None,
                               "username": "second", "bot": False})()

    async def disconnect(self):
        self.disconnected = True


class Ev:
    def __init__(self):
        self.responses = []

    async def respond(self, text, **kw):
        self.responses.append(text)


@pytest.fixture
def two(monkeypatch):
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    bot, c1, c2 = Bot(), Client(1), Client(2)
    for k, v in {"bot": bot, "user": c1, "owner_id": 1, "users": {1: c1, 2: c2},
                 "names": {1: "", 2: "Ikkinchi"}}.items():
        monkeypatch.setattr(control_bot.ctx, k, v, raising=False)
    monkeypatch.setattr(control_bot, "_notif_map", {})
    monkeypatch.setattr(control_bot, "_notif_slot", {})
    return bot, c1, c2


def _datas(kw):
    return [getattr(b, "data", None) or b.type.data for row in kw["buttons"] for b in row]


def test_second_account_notification_is_marked(two):
    bot, _, _ = two
    run(control_bot.notify(77, "Ali", None, "salom", "", False, None, slot=2))
    text, kw = bot.sent[-1]
    assert "📱 <b>2-akkaunt</b>: Ikkinchi" in text
    assert b"reply:77:2" in _datas(kw) and not any(d.startswith(b"arc:") for d in _datas(kw))
    assert control_bot._notif_map == {1: 77} and control_bot._notif_slot == {1: 2}

    run(control_bot.notify(78, "Vali", None, "salom", "", False, None))     # 1-akkaunt — o'zgarmagan
    text, kw = bot.sent[-1]
    assert "akkaunt" not in text and b"reply:78" in _datas(kw)
    assert 2 not in control_bot._notif_slot


def test_reply_goes_from_the_right_account(two):
    _, c1, c2 = two
    run(control_bot._send_as_owner(77, "javob", Ev(), slot=2))
    run(control_bot._send_as_owner(78, "javob1", Ev()))
    assert c2.sent == [(77, "javob")] and c1.sent == [(78, "javob1")]


def test_reply_to_missing_account_is_refused(two):
    _, c1, c2 = two
    control_bot.ctx.users.pop(2)
    ev = Ev()
    run(control_bot._send_as_owner(77, "javob", ev, slot=2))
    assert "ulanmagan" in ev.responses[0] and not c1.sent and not c2.sent


def test_same_account_cannot_be_added_twice(two, monkeypatch):
    control_bot.ctx.users.pop(2)
    same = Client(1)   # asosiy akkaunt bilan bir xil id
    assert "allaqachon" in run(control_bot.connect_second(same))
    assert same.disconnected and 2 not in control_bot.ctx.users


def test_group_message_from_both_accounts_cloned_once(monkeypatch):
    handlers = []

    class U:
        def on(self, ev):
            def deco(fn):
                handlers.append(fn)
                return fn
            return deco

    monkeypatch.setattr(groups, "_clients", {})
    monkeypatch.setattr(groups, "_seen", {})
    monkeypatch.setattr(groups, "_queue", None)
    groups.setup(U(), None, None, slot=1)
    groups.setup(U(), None, None, slot=2)
    ev = type("E", (), {"chat_id": -100, "message": type("M", (), {"id": 5})()})()
    for h in handlers:
        run(h(ev))
    assert groups._q().qsize() == 1
