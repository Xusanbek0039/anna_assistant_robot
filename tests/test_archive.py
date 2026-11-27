"""Chat arxivi: yozib borish, o'chirilganini aniqlash, HTML eksport va bot tugmalari."""
import asyncio
from datetime import datetime, timezone

import pytest
from telethon.tl.types import PeerChannel, PeerUser, User

import archive
import config
import control_bot
import runtime
import storage
from conftest import run


class FakeArchive:
    """storage.archive_* funksiyalarining xotiradagi nusxasi."""

    def __init__(self):
        self.msgs: dict[tuple, dict] = {}
        self.chats: dict[int, dict] = {}
        self.media: dict[str, tuple] = {}

    async def archive_add(self, doc):
        key = (doc["chat_id"], doc["msg_id"])
        if key in self.msgs:
            return False
        self.msgs[key] = dict(doc)
        return True

    async def archive_add_many(self, docs):
        return sum([await self.archive_add(d) for d in docs])

    async def archive_edit(self, chat_id, msg_id, text, ts):
        old = self.msgs.get((chat_id, msg_id))
        if old is None or old["text"] == text:
            return old
        snapshot = dict(old)
        old.setdefault("edits", []).append({"text": old["text"], "ts": old["date"]})
        old["text"] = text
        return snapshot

    async def archive_mark_deleted(self, chat_id, ids, ts):
        out = []
        for d in self.msgs.values():
            if d["msg_id"] in ids and not d.get("deleted") and \
                    (d["chat_id"] == chat_id if chat_id is not None else not d["channel"]):
                d.update(deleted=True, deleted_at=ts)
                out.append(dict(d))
        return out

    async def archive_touch_chat(self, chat_id, title, username, kind, ts, added=1, account_deleted=False):
        c = self.chats.setdefault(chat_id, {"chat_id": chat_id, "count": 0})
        c.update(title=title, username=username, kind=kind)
        c["count"] += added

    async def archive_chat(self, chat_id):
        return dict(self.chats[chat_id]) if chat_id in self.chats else None

    async def archive_messages(self, chat_id, only_deleted=False, limit=0):
        rows = sorted((dict(d) for d in self.msgs.values() if d["chat_id"] == chat_id
                       and (d.get("deleted") or not only_deleted)), key=lambda d: d["date"])
        return rows[-limit:] if limit else rows

    async def archive_media_put(self, data, mime):
        key = f"m{len(self.media)}"
        self.media[key] = (data, mime)
        return key

    async def archive_media_get(self, ids):
        return {i: self.media[i] for i in ids if i in self.media}

    async def archive_stats(self):
        return {"messages": len(self.msgs), "deleted": sum(1 for d in self.msgs.values() if d.get("deleted")),
                "chats": len(self.chats)}

    async def archive_chats(self, skip=0, limit=10, only_deleted=False, name=""):
        return list(self.chats.values())[skip:skip + limit]


class Msg:
    _next = 100

    def __init__(self, text="", out=False, chat=5, photo=False, channel=False):
        Msg._next += 1
        self.id = Msg._next
        self.message = text
        self.out = out
        self.sender_id = 1 if out else chat
        self.peer_id = PeerChannel(chat) if channel else PeerUser(chat)
        self.date = datetime(2026, 11, 19, 10, 30, tzinfo=timezone.utc)
        self.fwd_from = self.reply_to_msg_id = None
        for a in ("voice", "video_note", "gif", "video", "sticker", "audio", "document",
                  "geo", "venue", "contact", "poll"):
            setattr(self, a, None)
        self.photo = object() if photo else None
        self.media = object() if photo else None
        self.file = type("F", (), {"size": 10, "name": None, "mime_type": "image/jpeg"})() if photo else None

    async def download_media(self, file=None, thumb=None):
        return b"\xff\xd8jpeg"


class Ev:
    def __init__(self, msg, chat_entity, private=True, group=False):
        self.message = msg
        self.chat_id = chat_entity.id if private else -chat_entity.id
        self.is_private, self.is_group = private, group
        self._chat = chat_entity

    async def get_chat(self):
        return self._chat

    async def get_sender(self):
        return self._chat


class FakeUser:
    def __init__(self):
        self.handlers = {}

    def on(self, _ev):
        def deco(f):
            self.handlers[f.__name__] = f
            return f
        return deco


@pytest.fixture
def arc(monkeypatch):
    fa = FakeArchive()
    for name in dir(fa):
        if name.startswith("archive_"):
            monkeypatch.setattr(storage, name, getattr(fa, name))
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    monkeypatch.setattr(archive, "DELETE_GATHER_SECONDS", 0.01)
    monkeypatch.setattr(archive, "_del_buf", {})
    monkeypatch.setattr(archive, "_del_last", {})
    monkeypatch.setattr(archive, "_del_tasks", {})
    alerts = []

    async def on_deleted(chat_id, docs):
        alerts.append((chat_id, docs))

    user = FakeUser()
    archive.setup(user, 1, on_deleted)
    fa.user, fa.alerts = user, alerts
    return fa


def _person(uid=5, **kw):
    return User(id=uid, first_name="Ali", username="ali", **kw)


def test_stores_private_chats_even_ignored_but_not_service_bots_or_self(arc):
    async def go():
        new = arc.user.handlers["archive_new"]
        await new(Ev(Msg("salom"), _person()))
        await new(Ev(Msg("Login code: 12345", chat=777000), User(id=777000, first_name="Telegram")))
        await new(Ev(Msg("bot", chat=9), User(id=9, first_name="B", bot=True)))
        await new(Ev(Msg("eslatma", out=True, chat=1), User(id=1, first_name="Men", is_self=True)))
        await new(Ev(Msg("guruh"), _person(7), private=False, group=True))  # guruhlar standart o'chiq
    run(go())
    assert [d["text"] for d in arc.msgs.values()] == ["salom"]
    assert arc.chats[5]["title"] == "Ali"


def test_deleting_marks_and_sends_one_grouped_alert(arc):
    async def go():
        new = arc.user.handlers["archive_new"]
        m1, m2, m3 = Msg("birinchi"), Msg("ikkinchi", out=True), Msg(photo=True)
        for m in (m1, m2, m3):
            await new(Ev(m, _person()))
        # Telegram shaxsiy chatda qaysi chatligini aytmaydi (chat_id=None) — id bo'yicha topiladi
        await archive._deleted(None, [m1.id, m2.id])
        await archive._deleted(None, [m3.id])
        await asyncio.sleep(0.1)
        return m1
    m1 = run(go())
    assert len(arc.alerts) == 1
    chat_id, docs = arc.alerts[0]
    assert chat_id == 5 and len(docs) == 3
    assert arc.msgs[(5, m1.id)]["deleted"] is True
    assert any(d.get("media_id") for d in docs)  # kichik rasm arxivga saqlangan


def test_alerts_can_be_turned_off(arc):
    async def go():
        m = Msg("x")
        await arc.user.handlers["archive_new"](Ev(m, _person()))
        await runtime.update(archive_alerts=False)
        await archive._deleted(None, [m.id])
        await asyncio.sleep(0.1)
    run(go())
    assert arc.alerts == []


def test_edit_keeps_old_text(arc):
    class EditEv(Ev):
        pass

    async def go():
        m = Msg("eski matn")
        await arc.user.handlers["archive_new"](Ev(m, _person()))
        m.message = "yangi matn"
        await arc.user.handlers["archive_edited"](EditEv(m, _person()))
        return m
    m = run(go())
    doc = arc.msgs[(5, m.id)]
    assert doc["text"] == "yangi matn" and doc["edits"][0]["text"] == "eski matn"


def test_build_html_escapes_and_marks(arc):
    info = {"chat_id": 5, "title": "Ali <script>", "username": "ali", "kind": "user"}
    docs = [
        {"chat_id": 5, "msg_id": 1, "out": False, "text": "<b>salom</b>", "date": 1.7e9},
        {"chat_id": 5, "msg_id": 2, "out": True, "text": "javob", "date": 1.7e9 + 60, "reply_to": 1,
         "deleted": True, "deleted_at": 1.7e9 + 120, "edits": [{"text": "jvob", "ts": 1.7e9 + 60}]},
        {"chat_id": 5, "msg_id": 3, "out": False, "text": "", "media": "rasm", "media_id": "m0", "date": 1.7e9 + 90},
    ]
    page = archive.build_html(info, docs, {"m0": (b"img", "image/jpeg")}, only_deleted=False)
    assert "<script>" not in page and "&lt;b&gt;salom&lt;/b&gt;" in page
    assert "msg del" in page and "o‘chirilgan" in page
    assert "Oldingi variantlar (1)" in page and "jvob" in page
    assert "data:image/jpeg;base64," in page
    assert "↩️ &lt;b&gt;salom" in page  # qaysi xabarga javob ekani


def test_export_includes_live_history(arc):
    class Live(FakeUser):
        async def get_entity(self, chat_id):
            return _person(chat_id)

        async def iter_messages(self, entity, limit=None):
            for t in ("telegramda turgan xabar",):
                from telethon.tl.types import Message
                m = Message(id=50, peer_id=PeerUser(5), date=datetime(2026, 1, 1, tzinfo=timezone.utc), message=t)
                yield m

    data, name, n = run(archive.export_html(Live(), 5))
    assert n == 1 and name.startswith("chat_Ali_") and name.endswith(".html")
    assert "telegramda turgan xabar" in data.decode()


# ---------- bot ----------
def test_menu_buttons_not_treated_as_plain_text():
    import inspect
    src = inspect.getsource(control_bot.setup)
    assert "not in MENU_TEXTS" in src
    texts = [b.button.text for row in control_bot.main_keyboard() for b in row]
    assert set(texts) == control_bot.MENU_TEXTS


def test_archive_buttons_fit_telegram_limit(arc):
    run(arc.archive_touch_chat(-1001234567890, "Guruh", None, "group", 1.0))
    text, rows = run(control_bot._archive_panel())
    assert "Chat arxivi" in text
    _, list_rows = run(control_bot._archive_list(0, False))
    for row in rows + list_rows + control_bot._html_buttons(-1001234567890):
        for btn in row:
            data = getattr(btn, "data", None) or btn.type.data
            assert data.startswith((b"arc:", b"chn:")) and len(data) <= 64


def test_deleted_alert_text(arc, monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, uid, text, **kw):
            sent.append((text, kw))

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    run(arc.archive_touch_chat(5, "Ali", "ali", "user", 1.0))
    docs = [{"chat_id": 5, "msg_id": 1, "out": False, "text": "<maxfiy>", "date": 1.7e9},
            {"chat_id": 5, "msg_id": 2, "out": True, "text": "", "media": "rasm", "date": 1.7e9}]
    run(control_bot.deleted_alert(5, docs))
    text, kw = sent[0]
    assert "uniki: 1, sizniki: 1" in text and "&lt;maxfiy&gt;" in text and "[rasm]" in text
    assert kw["buttons"]  # HTML yuklab olish tugmalari


def test_config_defaults():
    assert config.ARCHIVE_ENABLED and not config.ARCHIVE_GROUPS and config.ARCHIVE_MEDIA_MAX_KB > 0


def test_sync_existing_copies_only_people_and_then_only_new(arc, monkeypatch):
    from telethon.tl.types import Message

    async def last_id(chat_id):
        ids = [m for (c, m) in arc.msgs if c == chat_id]
        return max(ids, default=0)
    monkeypatch.setattr(storage, "archive_last_msg_id", last_id, raising=False)

    def tg_msg(i, text):
        return Message(id=i, peer_id=PeerUser(5), date=datetime(2026, 1, 1, tzinfo=timezone.utc), message=text)

    class Dialog:
        def __init__(self, entity, msgs, user=True, group=False):
            self.entity, self.id, self.msgs = entity, entity.id, msgs
            self.is_user, self.is_group = user, group
            self.message = msgs[-1] if msgs else None

    class Tg:
        def __init__(self, dialogs):
            self.dialogs = dialogs

        async def iter_dialogs(self):
            for d in self.dialogs:
                yield d

        async def iter_messages(self, entity, limit=None, min_id=0):
            d = next(x for x in self.dialogs if x.entity is entity)
            for m in reversed([m for m in d.msgs if m.id > min_id][-limit:]):
                yield m

    ali = Dialog(_person(5), [tg_msg(1, "eski 1"), tg_msg(2, "eski 2"), tg_msg(3, "eski 3")])
    service = Dialog(User(id=777000, first_name="Telegram"), [tg_msg(4, "Login code 1")])
    bot = Dialog(User(id=9, first_name="B", bot=True), [tg_msg(5, "bot")])
    tg = Tg([ali, service, bot])
    assert run(archive.sync_existing(tg, per_chat=2, pause=0)) == (1, 2)   # oxirgi 2 tasi
    assert sorted(d["text"] for d in arc.msgs.values()) == ["eski 2", "eski 3"]
    assert arc.chats[5]["title"] == "Ali"

    ali.msgs.append(tg_msg(6, "yangi"))
    ali.message = ali.msgs[-1]
    assert run(archive.sync_existing(tg, per_chat=2, pause=0)) == (1, 1)   # faqat yangisi
    assert run(archive.sync_existing(tg, per_chat=2, pause=0)) == (0, 0)


def _ogg_voice() -> bytes:
    import io
    import math
    import struct
    import wave

    import tts
    rate = 16000
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 5))) for i in range(rate)))
    w.close()
    return tts.to_ogg_opus(buf.getvalue())


def test_export_downloads_photos_and_voices_for_old_messages(arc, monkeypatch):
    async def msg_set(cid, mid, **f):
        arc.msgs[(cid, mid)].update(f)

    async def chat_set(cid, **f):
        arc.chats.setdefault(cid, {"chat_id": cid}).update(f)
    monkeypatch.setattr(storage, "archive_msg_set", msg_set, raising=False)
    monkeypatch.setattr(storage, "archive_chat_set", chat_set, raising=False)
    voice = _ogg_voice()

    def doc(mid, media, **kw):
        return {"chat_id": 5, "msg_id": mid, "channel": False, "out": False, "text": "", "media": media,
                "date": 1.7e9 + mid, **kw}
    # mavjud chatdan nusxalangan (sync_existing) — media o'zi yo'q, faqat nomi
    for d in (doc(1, "rasm"), doc(2, "ovozli xabar"), doc(3, "rasm", deleted=True), doc(4, "rasm")):
        arc.msgs[(5, d["msg_id"])] = d
    arc.chats[5] = {"chat_id": 5, "title": "Ali", "count": 4}

    class F:
        def __init__(self, size, mime):
            self.size, self.mime_type = size, mime

    class M:
        def __init__(self, kind):
            self.media = True
            self.photo = object() if kind == "rasm" else None
            self.voice = object() if kind == "ovozli" else None
            self.sticker = self.video_note = None
            self.file = F(4, "image/jpeg") if self.photo else F(len(voice), "audio/ogg")

        async def download_media(self, file=None, thumb=None):
            return b"\xff\xd8JPG" if self.photo else voice

    calls = []

    class Tg:
        async def get_entity(self, cid):
            raise ValueError("Telegram'ga ulanmaymiz")

        async def get_input_entity(self, cid):
            return cid

        async def get_messages(self, entity, ids):
            calls.append(list(ids))
            return [{1: M("rasm"), 2: M("ovozli"), 4: None}.get(i) for i in ids]

    page, _, n = run(archive.export_html(Tg(), 5))
    page = page.decode()
    assert n == 4
    assert 3 not in calls[0]                                    # o'chirilganini Telegram'dan so'ramaymiz
    assert page.count("data:image/jpeg;base64,") == 1
    assert "<audio controls src='data:audio/mp4;base64," in page  # iPhone'da ham ijro bo'ladi
    assert arc.msgs[(5, 4)]["media_tried"] is True              # Telegram'da yo'q — qayta so'ralmaydi
    assert arc.chats[5]["drive_dirty"] is True                  # Drive'dagi HTML ham yangilanadi

    calls.clear()
    run(archive.export_html(Tg(), 5))
    assert calls == []                                          # hammasi arxivda — qayta yuklanmaydi


def test_html_budget_prefers_deleted_then_newest(monkeypatch):
    monkeypatch.setattr(archive, "HTML_MEDIA_BUDGET", 10)
    docs = [{"chat_id": 5, "msg_id": i, "out": False, "text": "", "media": "rasm", "media_id": f"m{i}",
             "date": 1.7e9 + i, "deleted": i == 1} for i in (1, 2, 3)]
    media = {f"m{i}": (bytes([i]) * 5, "image/jpeg") for i in (1, 2, 3)}
    page = archive.build_html({"chat_id": 5, "title": "Ali"}, docs, media, False)
    import base64
    assert base64.b64encode(bytes([1]) * 5).decode() in page     # o'chirilgani
    assert base64.b64encode(bytes([3]) * 5).decode() in page     # eng yangisi
    assert base64.b64encode(bytes([2]) * 5).decode() not in page


def test_message_archived_even_if_media_cannot_be_stored(arc, monkeypatch):
    """MongoDB media'ni saqlay olmasa (masalan, 16 MB dan katta) — xabarning o'zi baribir arxivga tushadi."""
    monkeypatch.setattr(config, "ARCHIVE_MEDIA_MAX_KB", 100000)

    async def too_large(data, mime):
        raise RuntimeError("DocumentTooLarge")
    monkeypatch.setattr(storage, "archive_media_put", too_large)
    m = Msg("izoh", photo=True)
    run(arc.user.handlers["archive_new"](Ev(m, _person())))
    doc = arc.msgs[(5, m.id)]
    assert doc["text"] == "izoh" and doc["media"] == "rasm" and doc["media_id"] is None


def test_mongo_blob_capped(arc, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_MEDIA_MAX_KB", 100000)   # 100 MB — MongoDB'ga sig'maydi
    m = Msg(photo=True)
    m.photo, m.video = None, object()
    m.file.mime_type, m.file.size = "video/mp4", archive.MONGO_BLOB_MAX + 1
    assert run(archive._save_blob(m)) is None                       # 12 MB dan katta — saqlanmaydi


def test_sync_full_history_for_selected_groups_channels_and_second_account(arc, monkeypatch):
    """Belgilangan guruh/kanal va 2-akkauntdagi shu vaqtgacha bo'lgan BARCHA xabarlar saqlanadi."""
    from telethon.tl.types import Message
    import channels
    import groups

    async def last_id(chat_id):
        ids = [m for (c, m) in arc.msgs if c == chat_id]
        return max(ids, default=0)

    async def chat_set(chat_id, **fields):
        arc.chats.setdefault(chat_id, {"chat_id": chat_id}).update(fields)

    monkeypatch.setattr(storage, "archive_last_msg_id", last_id, raising=False)
    monkeypatch.setattr(storage, "archive_chat_set", chat_set, raising=False)
    monkeypatch.setattr(groups, "_enabled", {-100: "Radiklaa"})
    monkeypatch.setattr(channels, "_enabled", {-200: "Yangiliklar"})

    def tg_msg(i, text):
        return Message(id=i, peer_id=PeerUser(5), date=datetime(2026, 1, 1, tzinfo=timezone.utc), message=text)

    class Dialog:
        def __init__(self, entity, msgs, *, user=False, group=False, channel=False):
            self.entity, self.id, self.msgs = entity, entity.id, msgs
            self.is_user, self.is_group, self.is_channel = user, group, channel
            self.name = getattr(entity, "title", None) or getattr(entity, "first_name", "")
            self.message = msgs[-1] if msgs else None

    class Tg:
        def __init__(self, dialogs):
            self.dialogs = dialogs

        async def iter_dialogs(self):
            for d in self.dialogs:
                yield d

        async def iter_messages(self, entity, limit=None, min_id=0):
            d = next(x for x in self.dialogs if x.entity is entity)
            rows = [m for m in d.msgs if m.id > min_id]
            if limit is not None:
                rows = rows[-limit:]
            for m in reversed(rows):
                yield m

    grp_ent = type("G", (), {"id": -100, "title": "Radiklaa", "username": None})()
    chn_ent = type("C", (), {"id": -200, "title": "Yangiliklar", "username": "news", "broadcast": True})()
    grp_msgs = [tg_msg(i, f"g{i}") for i in range(1, 6)]
    chn_msgs = [tg_msg(i, f"c{i}") for i in range(1, 5)]

    # Guruhda bitta eng oxirgi xabar oldin yozilib qolgan bo'lsa ham — eski 1..4 xabarlar baribir olinishi shart
    arc.msgs[(-100, 5)] = {"chat_id": -100, "msg_id": 5, "text": "g5"}
    arc.chats[-100] = {"chat_id": -100, "title": "Radiklaa", "count": 1, "last": 0}

    tg = Tg([
        Dialog(grp_ent, grp_msgs, group=True),
        Dialog(chn_ent, chn_msgs, channel=True),
    ])
    chats, added = run(archive.sync_existing(tg, per_chat=2, pause=0))
    assert chats == 2 and added == 8  # g1..g4 (4 ta) + c1..c4 (4 ta)
    assert arc.chats[-100]["full_synced"] is True and arc.chats[-200]["full_synced"] is True

    # 2-akkaunt (full=True): shaxsiy chatdagi per_chat=2 dan ham ko'p BARCHA xabarlar olinadi
    usr_ent = _person(10)
    usr_msgs = [tg_msg(i, f"u{i}") for i in range(1, 6)]
    monkeypatch.setattr(archive, "_me_id", 10)          # 1-akkaunt id=10
    monkeypatch.setitem(archive._me_ids, 2, 999)        # 2-akkaunt id=999 -> id=10 o'tkazib yuborilmasin
    with storage.use_slot(2):
        c2, a2 = run(archive.sync_existing(Tg([Dialog(usr_ent, usr_msgs, user=True)]), per_chat=2, pause=0, full=True))
    assert c2 == 1 and a2 == 5 and arc.chats[10]["full_synced"] is True
