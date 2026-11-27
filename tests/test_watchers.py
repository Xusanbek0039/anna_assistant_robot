"""🔔 Eslatmalar: raqam Telegram'da paydo bo'lsa / odam online bo'lsa — xabar."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl.functions.contacts import ResolvePhoneRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.tl.types import User, UserStatusOffline, UserStatusOnline, UserStatusRecently

import config
import watchers
from conftest import run


def _now():
    return datetime.now(timezone.utc)


def online():
    return UserStatusOnline(expires=_now() + timedelta(minutes=5))


def offline():
    return UserStatusOffline(was_online=_now())


def make_user(uid=5, first="Ali", username="alijon", status=None):
    return User(id=uid, first_name=first, username=username, access_hash=777, status=status or offline())


class FakeClient:
    """Telegram o'rniga: phones — Telegram'da topiladigan raqamlar, users — ID bo'yicha odamlar."""

    def __init__(self):
        self.phones: dict[str, User] = {}
        self.users: dict[int, User] = {}
        self.calls: list = []
        self.handlers: list = []
        self.flood = 0          # >0 bo'lsa raqam tekshiruvi FloodWait bilan rad etiladi

    async def __call__(self, req):
        self.calls.append(req)
        if isinstance(req, ResolvePhoneRequest):
            if self.flood:
                err = errors.FloodWaitError(request=req)
                err.seconds = self.flood
                raise err
            u = self.phones.get(req.phone)
            if u is None:
                raise errors.PhoneNotOccupiedError(request=req)
            return SimpleNamespace(users=[u])
        if isinstance(req, GetUsersRequest):
            return [self.users[i.user_id] for i in req.id if i.user_id in self.users]
        raise AssertionError(f"kutilmagan so'rov: {req!r}")

    async def get_entity(self, ref):
        if isinstance(ref, int):
            if ref in self.users:
                return self.users[ref]
            raise ValueError(ref)
        for u in self.users.values():
            if u.username == ref.lstrip("@"):
                return u
        raise ValueError(ref)

    def on(self, _event):
        def deco(fn):
            self.handlers.append(fn)
            return fn
        return deco

    def resolves(self) -> int:
        return sum(isinstance(c, ResolvePhoneRequest) for c in self.calls)


@pytest.fixture
def alerts(monkeypatch, fake_storage, fast_sleep):
    sent = []

    async def alert(text, buttons=None):
        sent.append((text, buttons))
    monkeypatch.setattr(watchers, "_alert", alert)
    monkeypatch.setattr(watchers, "_online_by", {})
    monkeypatch.setattr(watchers, "_pending", {})
    monkeypatch.setattr(watchers, "_phone_pause_until", 0.0)
    monkeypatch.setattr(watchers, "_lock", asyncio.Lock())
    return sent


# ---------- gapni tushunish ----------
def test_owner_sentence_about_phone():
    text = ("95446686 raqami tg ga qoshilsa tg acaunt ochsa yoki yashirindan chiqarsa menga habar ber")
    req = watchers.parse(text)
    assert req.kind == "phone" and req.number == "95446686"
    assert req.phone is None  # 8 ta raqam — O'zbekiston raqami emas
    assert "8 ta raqam" in watchers.bad_number_text(req.number)

    for t in ("954466886 raqami tg ga qoshilsa menga habar ber",
              "+998 95 446 68 86 akkaunt ochsa xabar ber",
              "95 446 68 86 raqami telegramda topilsa xabar ber",
              "954466886 yashirindan chiqarsa eslat"):
        req = watchers.parse(t)
        assert req and req.kind == "phone" and req.phone == "998954466886", t


@pytest.mark.parametrize("text,ref,name,repeat", [
    ("@ali_vali online bo'lsa menga habar ber", "@ali_vali", "", False),
    ("shu user t.me/ali_vali onlayn bo'lsa ayt", "@ali_vali", "", False),
    ("id 123456789 online bo'lsa xabar ber", "123456789", "", False),
    ("Ali har safar onlayn bo'lganda xabar ber", "", "Ali", True),
    ("Bolta aka online bo'lsa ayt", "", "Bolta aka", False),
    ("Kirill tg ga kirsa xabar ber", "", "Kirill", False),
    ("Sardor qachon onlayn bo'ladi, menga ayt", "", "Sardor", False),
])
def test_online_requests(text, ref, name, repeat):
    req = watchers.parse(text)
    assert req.kind == "online" and req.user_ref == ref and req.name == name and req.repeat == repeat


def test_online_by_phone_number():
    req = watchers.parse("95 446 68 86 raqami online bo'lsa xabar ber")
    assert req.kind == "online" and req.phone == "998954466886" and not req.user_ref
    req = watchers.parse("954466886 online bo'lsa xabar ber")  # ID yoki raqam — ikkalasi sinab ko'riladi
    assert req.kind == "online" and req.user_ref == "954466886"


@pytest.mark.parametrize("text", [
    "Ali aytgan onlayn kursni top",
    "Ali online do'kon haqida xabar bergandi, top",
    "akkauntini ochirsa xabar ber",
    "Ali online bo'ldi",
    "nonga 25 ming ishlatdim",
    "o'chirilgan akkauntlarni ko'rsat",
    "Alidan 200 ming oldim",
    "123456789",
])
def test_other_messages_are_not_watch_requests(text):
    assert watchers.parse(text) is None


def test_command_form_is_lenient():
    assert watchers.parse("@alijon", strict=False).user_ref == "@alijon"
    req = watchers.parse("954466886", strict=False)
    assert req.kind == "phone" and req.phone == "998954466886"
    assert watchers.parse("Ali har safar", strict=False).repeat


def test_about_this():
    assert watchers.about_this("shu user online bo'lsa menga xabar ber")
    assert not watchers.about_this("online bo'lsang xabar ber")


@pytest.mark.parametrize("raw,expected", [
    ("+998 (95) 446-68-86", "998954466886"), ("954466886", "998954466886"), ("95446686", None),
    ("99895446686", None), ("+7 912 345 67 89", "79123456789"), ("0079123456789", "79123456789"),
])
def test_normalize_phone(raw, expected):
    assert watchers.normalize_phone(raw) == expected


def test_fmt_phone():
    assert watchers.fmt_phone("998954466886") == "+998 95 446 68 86"


# ---------- raqam ----------
def test_phone_watch_fires_when_number_appears(alerts, fake_storage):
    client = FakeClient()
    text, buttons = run(watchers.handle(client, watchers.parse("954466886 raqami tg ga qo'shilsa xabar ber")))
    assert "Eslatma qo'yildi" in text and "+998 95 446 68 86" in text and "kontaktlaringizga qo'shilmaydi" in text
    assert len(fake_storage.d_watches) == 1 and client.resolves() == 1

    run(watchers.check_once(client))  # hali vaqti kelmagan — Telegram'ga so'rov yo'q
    assert client.resolves() == 1 and not alerts

    fake_storage.d_watches[0]["last_check"] = 0
    run(watchers.check_once(client))  # hali ham Telegram'da yo'q
    assert client.resolves() == 2 and not alerts and fake_storage.d_watches[0]["last_check"] > 0

    client.phones["998954466886"] = make_user(9, "Yangi", "yangi")  # akkaunt ochdi / yashirishni olib tashladi
    fake_storage.d_watches[0]["last_check"] = 0
    run(watchers.check_once(client))
    assert len(alerts) == 1 and "Telegram'da paydo bo'ldi" in alerts[0][0] and "@yangi" in alerts[0][0]
    assert ("💬 Lichkaga o'tish", "wch:dm:9") in alerts[0][1][0]
    assert fake_storage.d_watches[0]["active"] is False and fake_storage.d_watches[0]["found_user_id"] == 9

    run(watchers.check_once(client))  # eslatma yopilgan — qayta xabar yo'q
    assert len(alerts) == 1 and client.resolves() == 3


def test_phone_already_on_telegram(alerts, fake_storage):
    client = FakeClient()
    client.phones["998954466886"] = make_user(9, "Bor", None)
    text, buttons = run(watchers.add_phone(client, "998954466886"))
    assert "allaqachon Telegram'da" in text and not fake_storage.d_watches
    assert ("🟢 Online bo'lsa ham xabar ber", "wch:on:9") in buttons[1]


def test_phone_watch_not_duplicated(alerts, fake_storage):
    client = FakeClient()
    run(watchers.add_phone(client, "998954466886"))
    text, _ = run(watchers.add_phone(client, "998954466886"))
    assert "allaqachon kuzatilyapti" in text and len(fake_storage.d_watches) == 1


def test_bad_number_does_not_create_watch(alerts, fake_storage):
    text, _ = run(watchers.handle(FakeClient(), watchers.parse("95446686 raqami tg ga qoshilsa habar ber")))
    assert text.startswith("❌") and not fake_storage.d_watches


# ---------- online ----------
def test_online_once(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user()
    text, _ = run(watchers.handle(client, watchers.parse("@alijon online bo'lsa menga xabar ber")))
    assert "Eslatma qo'yildi" in text and "bir marta" in text

    run(watchers.on_status(5, offline()))
    assert not alerts
    run(watchers.on_status(5, online()))
    assert len(alerts) == 1 and "hozir online" in alerts[0][0] and "@alijon" in alerts[0][0]
    assert fake_storage.d_watches[0]["active"] is False

    run(watchers.on_status(5, offline()))
    run(watchers.on_status(5, online()))
    assert len(alerts) == 1  # bir martalik — yopildi


def test_online_every_time_with_cooldown(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user()
    text, _ = run(watchers.handle(client, watchers.parse("@alijon har safar online bo'lganda xabar ber")))
    assert "Har safar" in text

    run(watchers.on_status(5, online()))
    assert len(alerts) == 1
    assert ("🛑 Kuzatuvni to'xtatish", f"wch:del:{fake_storage.d_watches[0]['id']}") in alerts[0][1][-1]
    run(watchers.on_status(5, offline()))
    run(watchers.on_status(5, online()))  # 10 daqiqa o'tmagan — takrorlanmaydi
    assert len(alerts) == 1

    watchers._ol()[5][0]["last_fired"] -= config.WATCH_ONLINE_COOLDOWN_MINUTES * 60 + 1
    run(watchers.on_status(5, offline()))
    run(watchers.on_status(5, online()))
    assert len(alerts) == 2 and fake_storage.d_watches[0]["active"] is True


def test_online_detected_by_polling(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user()
    run(watchers.handle(client, watchers.Request("online", user_ref="5")))
    run(watchers.check_once(client))
    assert not alerts
    client.users[5] = make_user(status=online())
    run(watchers.check_once(client))
    assert len(alerts) == 1


def test_expired_online_counts_as_offline():
    assert watchers.status_kind(UserStatusOnline(expires=_now() - timedelta(minutes=1))) == "offline"
    assert watchers.status_kind(UserStatusRecently()) == "hidden"


def test_already_online_waits_for_next_time(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user(status=online())
    text, _ = run(watchers.add_online(client, client.users[5]))
    assert "hozir online" in text
    run(watchers.on_status(5, online()))
    assert not alerts  # hali ham o'sha online — keyingi safarini kutamiz
    run(watchers.on_status(5, offline()))
    run(watchers.on_status(5, online()))
    assert len(alerts) == 1


def test_hidden_last_seen_warns(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user(status=UserStatusRecently())
    text, _ = run(watchers.add_online(client, client.users[5]))
    assert "yashirgan" in text


def test_online_by_phone_resolves_user(alerts, fake_storage):
    client = FakeClient()
    text, buttons = run(watchers.handle(client, watchers.parse("95 446 68 86 raqami online bo'lsa xabar ber")))
    assert "topilmadi" in text and buttons == [[("📱 Telegram'da paydo bo'lsa xabar ber", "wch:ph:998954466886")]]
    client.phones["998954466886"] = make_user(9, "Vali", "vali")
    text, _ = run(watchers.handle(client, watchers.parse("95 446 68 86 raqami online bo'lsa xabar ber")))
    assert "Eslatma qo'yildi" in text and fake_storage.d_watches[0]["user_id"] == 9


def test_unknown_user(alerts, fake_storage):
    text, _ = run(watchers.handle(FakeClient(), watchers.parse("@nobody online bo'lsa xabar ber")))
    assert text.startswith("❌") and not fake_storage.d_watches


def test_name_lookup_with_choice(alerts, fake_storage, monkeypatch):
    import finder
    client = FakeClient()
    client.users[5] = make_user(5, "Ali Valiyev", "ali")
    client.users[6] = make_user(6, "Ali Karimov", "alik")

    async def find_chats(_client, name):
        return [finder.Candidate(id=5, title="Ali Valiyev", score=3.5),
                finder.Candidate(id=6, title="Ali Karimov", score=3.5)]
    monkeypatch.setattr(finder, "find_chats", find_chats)
    text, buttons = run(watchers.handle(client, watchers.parse("Ali online bo'lsa xabar ber")))
    assert text == "Kimni kuzatay?" and len(buttons) == 2
    key = buttons[1][0][1].split(":")[2]
    text, _ = run(watchers.pick(client, key, 1))
    assert "Ali Karimov" in text and fake_storage.d_watches[0]["user_id"] == 6


def test_realtime_event_handler(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user()
    watchers.setup(client, watchers._alert)
    run(watchers.add_online(client, client.users[5]))
    handler = client.handlers[-1]
    run(handler(SimpleNamespace(user_id=5, status=None)))     # «yozmoqda...» — e'tiborsiz
    run(handler(SimpleNamespace(user_id=99, status=online())))  # kuzatilmaydigan odam
    assert not alerts
    run(handler(SimpleNamespace(user_id=5, status=online())))
    assert len(alerts) == 1


def test_list_and_remove(alerts, fake_storage):
    client = FakeClient()
    client.users[5] = make_user()
    run(watchers.add_phone(client, "998954466886"))
    run(watchers.add_online(client, client.users[5], repeat=True))
    text, buttons = run(watchers.list_view())
    assert "+998 95 446 68 86" in text and "Ali" in text and "har safar" in text
    assert [b[0] for b in buttons[0]] == ["🗑 1", "🗑 2"]
    assert run(watchers.remove(buttons[0][1][1].split(":")[2]))
    assert 5 not in watchers._ol()
    text, _ = run(watchers.list_view())
    assert "Ali" not in text


# ---------- Telegram raqam tekshiruvini cheklaganda («14 soat tekshirilmadi» chalkashligi) ----------
def test_flood_pause_is_saved_and_restored(alerts, fake_storage, monkeypatch):
    """Cheklov vaqti bazaga yoziladi: dastur qayta ishga tushganda darhol so'rov yuborib,
    cheklovni yana uzaytirmasligi kerak."""
    monkeypatch.setattr(watchers, "_phone_pause_until", 0.0)
    client = FakeClient()
    run(watchers.add_phone(client, "998954466886"))
    rows = run(fake_storage.watch_list())
    before = rows[0].get("last_check")
    client.flood = 3600 * 5                                  # Telegram 5 soat kutishni so'radi
    run(watchers.check_phone(client, rows[0]))
    assert watchers.phone_pause_left() > 3600 * 4
    assert fake_storage.d_runtime.get("watch_phone_pause", 0) > 0
    saved = fake_storage.d_runtime["watch_phone_pause"]
    row = run(fake_storage.watch_list())[0]
    assert row.get("flood_until") and row.get("last_check") == before   # tekshirilmadi — vaqt o'zgarmaydi

    monkeypatch.setattr(watchers, "_phone_pause_until", 0.0)  # «qayta ishga tushish»
    run(watchers._load_pause())
    assert watchers._phone_pause_until == saved


def test_flood_state_is_explained_in_list(alerts, fake_storage, monkeypatch):
    import time as _t
    monkeypatch.setattr(watchers, "_phone_pause_until", _t.time() + 3600 * 5)
    client = FakeClient()
    run(watchers.add_phone(client, "998954466886"))
    text, buttons = run(watchers.list_view())
    assert "Telegram raqam tekshiruvini vaqtincha cheklab qo'ydi" in text
    assert "5.0 soat" in text and "xato emas" in text
    assert watchers.ADD_BUTTON in buttons[-1]                # «➕ Yangi eslatma» har doim bor


def test_list_shows_last_check_when_not_flooded(alerts, fake_storage, monkeypatch):
    import time as _t
    monkeypatch.setattr(watchers, "_phone_pause_until", 0.0)
    client = FakeClient()
    run(watchers.add_phone(client, "998954466886"))
    rows = run(fake_storage.watch_list())
    run(fake_storage.watch_update(rows[0]["id"], last_check=_t.time() - 300))
    text, _ = run(watchers.list_view())
    assert "Oxirgi tekshiruv: 5 daqiqa oldin" in text and "cheklab" not in text


def test_successful_check_clears_flood_mark(alerts, fake_storage, monkeypatch):
    monkeypatch.setattr(watchers, "_phone_pause_until", 0.0)
    client = FakeClient()
    run(watchers.add_phone(client, "998954466886"))
    rows = run(fake_storage.watch_list())
    run(fake_storage.watch_update(rows[0]["id"], flood_until=1.0))
    run(watchers.check_phone(client, run(fake_storage.watch_list())[0]))   # raqam topilmadi
    row = run(fake_storage.watch_list())[0]
    assert row["flood_until"] == 0 and row["last_check"] > 0


def test_empty_list_offers_add_button(alerts, fake_storage):
    text, buttons = run(watchers.list_view())
    assert "Hozir eslatma yo'q" in text and buttons == [[watchers.ADD_BUTTON]]
    assert "Yangi eslatma" in watchers.ADD_HELP
