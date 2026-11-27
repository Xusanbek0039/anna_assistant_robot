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


def test_feature_panel_and_toggle(two, monkeypatch):
    applied = []
    monkeypatch.setattr(control_bot, "_acc_apply", lambda slot: applied.append(slot))
    text, rows = control_bot.acc_panel(2, first_time=True)
    assert "qaysi funksiyalar ishlasin" in text
    datas = [d for row in rows for d in _datas({"buttons": [row]})]
    assert b"acc:t:2:autoreply" in datas and b"acc:t:2:bio" in datas and b"acc:t:2:online" in datas
    assert all(len(d) <= 64 for d in datas)
    assert runtime.get("acc2_online") is False
    run(control_bot.acc_toggle(2, "online"))
    assert runtime.get("acc2_online") is True and applied == [2]
    assert "✅ 🟢 24 soat online" in [b.text for row in control_bot.acc_panel(2)[1] for b in row]


def test_autoreply_off_still_notifies_second_account(monkeypatch, fake_storage):
    """2-akkauntda kotiba o'chiq: AI javob bermaydi, lekin kim yozgani 📩 bo'lib keladi va Inbox'ga tushadi."""
    import userbot
    from telethon.tl.types import User as TgUser
    handlers, notes = [], []

    class U:
        def on(self, ev):
            def deco(fn):
                handlers.append(fn)
                return fn
            return deco

    async def notify(chat_id, name, username, text, summary, urgent, reply, reply_to=None, slot=1):
        notes.append((chat_id, name, text, reply, slot))

    async def no_ai(*a, **kw):
        raise AssertionError("kotiba o'chiq — AI chaqirilmasligi kerak")

    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    fake_storage.d_mode = "off"     # 2-akkaunt rejimi: ⚪️ O'chiq
    monkeypatch.setattr(userbot.control_bot, "notify", notify)
    monkeypatch.setattr(userbot.ai, "generate", no_ai)
    userbot.setup(U(), 99, slot=2)

    class Msg:
        message, media, file = "salom", None, None
        voice = video_note = photo = video = sticker = audio = document = None

    class Ev:
        chat_id = 5
        message = Msg()

        async def get_sender(self):
            return TgUser(id=5, first_name="Ali", username="ali")

    run(handlers[0](Ev()))
    assert notes == [(5, "Ali", "salom", None, 2)]
    assert fake_storage.d_inbox[5]["slot"] == 2


def test_second_bio_uses_its_own_backup(tmp_path, monkeypatch):
    import bio_updater
    import config
    monkeypatch.setattr(config, "BIO_BACKUP_FILE", str(tmp_path / "bio_backup.json"))
    assert bio_updater.backup_file(1).endswith("bio_backup.json")
    assert bio_updater.backup_file(2).endswith("bio_backup2.json")
    upd = bio_updater.BioUpdater(object(), slot=2)
    assert upd.flag == "acc2_bio" and upd.backup_file.endswith("bio_backup2.json")
    assert bio_updater.INSTANCE is not upd       # /bio buyrug'i 1-akkauntni boshqaradi


class Ask:
    def __init__(self):
        self.sent = []

    async def respond(self, text, **kw):
        self.sent.append((text, kw))


def test_asks_which_account_only_when_two(two):
    ev = Ask()
    assert run(control_bot._ask_account(ev, "chn")) is True
    text, kw = ev.sent[0]
    assert "qaysi akkaunt" in text
    assert _datas(kw) == [b"pick:chn:1", b"pick:chn:2"]
    control_bot.ctx.users.pop(2)
    assert run(control_bot._ask_account(Ask(), "chn")) is False   # bitta akkaunt — so'ramaydi


def test_find_uses_chosen_account(two, monkeypatch):
    _, c1, c2 = two
    control_bot._awaiting_slot["find"] = 2
    seen = []

    async def find_chats(client, name):
        seen.append(client)
        return []

    async def find_in(*a, **kw):
        seen.append(control_bot._fu())

    monkeypatch.setattr(control_bot.finder, "find_chats", find_chats)
    monkeypatch.setattr(control_bot, "_find_in", find_in)
    monkeypatch.setattr(control_bot, "_ai_ok", lambda: _false())
    run(control_bot._run_find(Ask(), "Ali yuborgan manzilni top"))
    assert seen and all(c is c2 for c in seen) and "find" not in control_bot._awaiting_slot
    run(control_bot._run_find(Ask(), "Ali yuborgan manzilni top"))      # keyingisi — yana 1-akkaunt
    assert seen[-1] is c1


async def _false():
    return False


def test_inbox_filtered_by_account(two, fake_storage):
    run(fake_storage.upsert_inbox(10, "Ali", None, "", False, "salom"))
    run(fake_storage.upsert_inbox(20, "Vali", None, "", False, "hey", 2))
    ev = Ask()
    run(control_bot._inbox_for(ev, 2))
    assert len(ev.sent) == 1 and "Vali" in ev.sent[0][0] and "2-akkaunt" in ev.sent[0][0]
    assert b"reply:20:2" in _datas(ev.sent[0][1])        # javob 2-akkauntdan ketadi
    ev1 = Ask()
    run(control_bot._inbox_for(ev1, 1))
    assert len(ev1.sent) == 1 and "Ali" in ev1.sent[0][0]


def test_groups_listed_per_account(monkeypatch):
    class D:
        def __init__(self, gid, name):
            self.id, self.name, self.is_group, self.entity = gid, name, True, None

    class C:
        def __init__(self, ds):
            self.ds = ds

        async def iter_dialogs(self):
            for d in self.ds:
                yield d

    monkeypatch.setattr(groups, "_clients", {1: C([D(-1, "Ish")]), 2: C([D(-2, "Oila")])})
    monkeypatch.setattr(groups, "_cache", {})
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    assert [g["title"] for g in run(groups.my_groups(only=2))] == ["Oila"]
    assert [g["title"] for g in run(groups.my_groups(only=1))] == ["Ish"]
    text, rows = groups.panel(run(groups.my_groups(only=2)), 0, slot=2, acc_name="Ikkinchi")
    assert "2-akkaunt" in text and rows[0][0][1] == "grp:t:-2:0:2"


def test_saved_channels_list_per_account(two, monkeypatch):
    import channels
    import storage
    monkeypatch.setattr(channels, "_enabled", {-11: "Bir", -22: "Ikki"})
    infos = {-11: {"title": "Bir", "count": 5}, -22: {"title": "Ikki", "count": 7, "via": 2}}

    async def chat(cid):
        return infos.get(cid)

    monkeypatch.setattr(storage, "archive_chat", chat)
    text, rows = run(control_bot._saved_list(2, channels_only=True))
    labels = [b.text for row in rows for b in row]
    assert any("Ikki · 7" in t for t in labels) and not any("Bir" in t for t in labels)
    assert b"arc:html:-22:a" in _datas({"buttons": rows})


def test_second_account_archive_uses_own_collections(monkeypatch):
    import storage

    class DB:
        def __getattr__(self, name):
            return f"col:{name}"

        def __getitem__(self, name):
            return f"col:{name}"

    monkeypatch.setattr(storage, "_real_db", lambda: DB())
    assert storage._get_db().archive == "col:archive"
    with storage.use_slot(2):
        db = storage._get_db()
        assert db.archive == "col:archive2" and db.archive_chats == "col:archive_chats2"
        assert db["archive"] == "col:archive2"
        assert db.inbox == "col:inbox2" and db.history == "col:history2"   # Inbox va AI tarixi ham alohida
        assert db.settings == "col:settings" and db.contacts == "col:contacts"   # sozlamalar umumiy
    assert storage.arc_slot() == 1


def test_second_account_messages_go_to_its_archive(monkeypatch):
    import archive
    import storage
    seen = []

    async def fake_store(event):
        seen.append(storage.arc_slot())

    monkeypatch.setattr(archive, "_store", fake_store)
    monkeypatch.setattr(archive, "_clients", {})
    monkeypatch.setattr(archive, "_arc_slots", set())
    c2 = []

    class U:
        def on(self, ev):
            def deco(fn):
                c2.append(fn)
                return fn
            return deco

    archive.setup_account(U(), 77, 2)
    run(c2[0](type("E", (), {})()))
    assert seen == [2] and archive.arc_slots() == [1, 2] and archive._me_ids[2] == 77
    archive.forget_client(2)
    assert archive.arc_slots() == [1]


def test_notification_html_button_points_to_right_archive(two):
    bot, _, _ = two
    run(control_bot.notify(77, "Ali", None, "salom", "", False, None, slot=2))
    assert b"arc2:html:77:a" in _datas(bot.sent[-1][1])
    run(control_bot.notify(78, "Vali", None, "salom", "", False, None))
    assert b"arc1:html:78:a" in _datas(bot.sent[-1][1])


def test_second_account_has_same_control_panel(two, fake_storage, monkeypatch):
    async def get_status():
        return type("S", (), {"reply": True, "reason": "doim", "notify": True, "ai_context": ""})()

    monkeypatch.setattr(control_bot.availability, "get_status", get_status)
    text, rows = run(control_bot._panel(2))
    datas = _datas({"buttons": rows})
    assert "Boshqaruv paneli" in text and "2-akkaunt" in text
    for action in (b"cfg2:mode_auto", b"cfg2:mode_on", b"cfg2:mode_off", b"cfg2:bio_on", b"cfg2:bio_now",
                   b"cfg2:online_on", b"cfg2:bio_restore", b"cfg2:groups"):
        assert action in datas, action
    assert not any(d.startswith(b"cfg:") for d in datas)          # 1-akkaunt sozlamasiga tegmaydi


def test_second_account_mode_and_flags_are_separate(two, fake_storage, monkeypatch):
    import storage
    modes = {}

    async def set_mode(m):
        modes[storage.arc_slot()] = m

    monkeypatch.setattr(storage, "set_mode", set_mode)
    monkeypatch.setattr(control_bot, "_acc_apply", lambda slot: None)
    run(control_bot._do_action("mode_off", 2))
    assert modes == {2: "off"} and runtime.get("acc2_autoreply") is False
    run(control_bot._do_action("online_on", 2))
    assert runtime.get("acc2_online") is True and runtime.get("online") == runtime.DEFAULTS["online"]


def test_settings_keys_per_account():
    import storage
    assert storage._sid("mode") == "mode"
    with storage.use_slot(2):
        assert storage._sid("mode") == "mode2" and storage._sid("owner_active") == "owner_active2"


def test_watch_alert_from_second_account_is_marked(monkeypatch):
    import storage
    import watchers
    got = []

    async def alert(text, buttons=None):
        got.append((text, buttons))

    monkeypatch.setattr(watchers, "_alert", alert)
    with storage.use_slot(2):
        run(watchers._send("🔔 Eslatma!", [[("🛑 To'xtatish", "wch:del:abc")]]))
    text, buttons = got[0]
    assert text.startswith("📱 <b>2-akkaunt</b>") and buttons == [[("🛑 To'xtatish", "wch2:del:abc")]]


def test_reports_sent_for_each_account(monkeypatch):
    import report
    import storage
    import weekly
    sent = []

    async def make():
        return f"hafta {storage.arc_slot()}", []

    async def send(text, ids):
        sent.append(text)

    async def daily(name, at, key, job, **kw):
        await job()

    monkeypatch.setattr(weekly, "make", make)
    monkeypatch.setattr(weekly.schedule, "daily", daily)
    run(weekly.run(send, lambda: [1, 2]))
    assert sent == ["hafta 1", "📱 <b>2-akkaunt</b>\nhafta 2"]


def test_second_telegram_always_notifies_even_when_muted_or_alerts_off(two, fake_storage, monkeypatch):
    """2-akkauntda har bir kiruvchi xabar botga keladi (muted va new_msg_alerts=False bo'lsa ham)."""
    import runtime
    import userbot
    from telethon.tl.types import User

    bot, _, _ = two
    monkeypatch.setitem(runtime._values, "new_msg_alerts", False)
    run(control_bot.notify(77, "Ali", None, "salom", "", False, None, slot=2))
    assert len(bot.sent) == 1 and "salom" in bot.sent[0][0]

    handlers, notes = [], []

    class U:
        def on(self, ev):
            def deco(fn):
                handlers.append(fn)
                return fn
            return deco

    async def fake_notify(chat_id, name, username, content, *a, slot=1, **kw):
        notes.append((chat_id, content, slot))

    monkeypatch.setattr(control_bot, "notify", fake_notify)
    fake_storage.d_muted.add(5)
    userbot.setup(U(), 99, slot=2)

    class Msg:
        message, media, file, id, is_reply = "yangi xabar", None, None, 10, False
        voice = video_note = video = sticker = document = photo = None

    class Ev:
        chat_id, message = 5, Msg()
        async def get_sender(self):
            return User(id=5, first_name="Ali", contact=True)

    run(handlers[0](Ev()))
    assert notes == [(5, "yangi xabar", 2)]
