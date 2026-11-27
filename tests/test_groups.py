"""👥 Guruh xabarlarini klonlash (groups.py)."""
import pytest

import groups
import storage
from conftest import run


class Store:
    def __init__(self):
        self.rows: dict[int, str] = {}

    async def group_clone_list(self):
        return [{"chat_id": k, "title": v} for k, v in self.rows.items()]

    async def group_clone_add(self, chat_id, title):
        self.rows[chat_id] = title

    async def group_clone_remove(self, chat_id):
        self.rows.pop(chat_id, None)


@pytest.fixture
def store(monkeypatch):
    st = Store()
    for name in ("group_clone_list", "group_clone_add", "group_clone_remove"):
        monkeypatch.setattr(storage, name, getattr(st, name), raising=False)
    monkeypatch.setattr(groups, "_enabled", {})
    monkeypatch.setattr(groups, "_cache", {})
    return st


def _groups(n):
    return [{"id": -100 - i, "title": f"Guruh {i}", "username": None} for i in range(n)]


def test_toggle_is_saved_and_loaded(store):
    assert run(groups.toggle(-100, "Oila")) is True
    assert store.rows == {-100: "Oila"} and groups.is_enabled(-100)
    groups._enabled.clear()
    run(groups.load())                       # qayta ishga tushgandan keyin ham yoqiq
    assert groups.is_enabled(-100)
    assert run(groups.toggle(-100, "Oila")) is False
    assert store.rows == {} and not groups.is_enabled(-100)


def test_panel_pages_and_enabled_first(store):
    run(groups.toggle(-124, "Guruh 24"))
    text, rows = groups.panel(_groups(25), 0)
    assert "25 ta guruh" in text and "klonlanyapti: <b>1</b>" in text
    assert rows[0][0] == ("✅ Guruh 24", "grp:t:-124:0")        # yoqilgani tepada
    assert rows[1][0][0].startswith("▫️")
    assert ("➡️", "grp:p:1") in rows[-2]
    _, last = groups.panel(_groups(25), 99)                      # sahifa chegaradan oshsa — oxirgisi
    assert ("⬅️", "grp:p:1") in last[-2]
    assert all(len(d.encode()) <= 64 for row in rows for _, d in row)


def test_message_link():
    pub = type("C", (), {"username": "uzdev", "id": 5, "megagroup": True})()
    priv = type("C", (), {"username": None, "id": 123, "megagroup": True})()
    small = type("C", (), {"username": None, "id": 9, "megagroup": False})()
    assert groups.message_link(pub, 7) == "https://t.me/uzdev/7"
    assert groups.message_link(priv, 7) == "https://t.me/c/123/7"
    assert groups.message_link(small, 7) is None


class Sender:
    def __init__(self, first, username=None):
        self.first_name, self.last_name, self.username = first, None, username


class Msg:
    def __init__(self, text="", reply=None, media=None, file=None):
        self.message, self.id = text, 11
        self._reply = reply
        self.is_reply = reply is not None
        self.media = media
        self.file = file
        self.voice = self.video_note = self.video = self.sticker = self.document = None
        self.photo = media if media == "photo" else None

    async def get_reply_message(self):
        return self._reply

    async def get_sender(self):
        return Sender("Vali")

    async def download_media(self, file=None):
        return b"jpg-bytes"


class Event:
    def __init__(self, msg, chat_id=-100):
        self.message, self.chat_id = msg, chat_id

    async def get_chat(self):
        return type("C", (), {"title": "Dasturchilar", "username": "uzdev", "id": 5, "megagroup": True})()

    async def get_sender(self):
        return Sender("Ali", "ali")


def _capture(monkeypatch):
    sent = {"text": [], "media": []}

    async def send_text(text, link, **kw):
        sent["text"].append((text, link, kw))

    async def send_media(*args, **kw):
        sent["media"].append((args, kw))
        return 100 + len(sent["media"])

    monkeypatch.setattr(groups, "_send_text", send_text)
    monkeypatch.setattr(groups, "_send_media", send_media)
    return sent


def test_clone_text_with_reply(monkeypatch, store):
    sent = _capture(monkeypatch)
    run(groups.clone(Event(Msg("Salom <hammaga>", reply=Msg("ertaga uchrashamiz")))))
    text, link, kw = sent["text"][0]
    assert "👥 <b>Dasturchilar</b>" in text and "<b>Ali</b> (@ali)" in text
    assert "Salom &lt;hammaga&gt;" in text                     # HTML xavfsiz
    assert "↩️ <i>Vali: ertaga uchrashamiz</i>" in text
    assert link == "https://t.me/uzdev/11" and kw == {}


def test_clone_reply_to_photo_sends_replied_photo_too(monkeypatch, store):
    sent = _capture(monkeypatch)
    file = type("F", (), {"size": 1000, "mime_type": "image/jpeg", "name": None, "ext": ".jpg"})()
    replied = Msg("", media="photo", file=file)
    run(groups.clone(Event(Msg("Ол", reply=replied))))
    assert len(sent["media"]) == 1
    args, mkw = sent["media"][0]
    assert args[1] == "👥 Dasturchilar · ↩️ Vali" and args[2] == b"jpg-bytes"
    assert mkw["kind"] == "rasm" and mkw["link_reply"] is False
    text, link, tkw = sent["text"][0]
    assert "↩️ <i>Vali: [rasm]</i>" in text and "Ол" in text
    assert tkw.get("reply_to") == 101 and link == "https://t.me/uzdev/11"


def test_clone_photo_goes_as_media(monkeypatch, store):
    sent = _capture(monkeypatch)
    file = type("F", (), {"size": 1000, "mime_type": "image/jpeg", "name": None, "ext": ".jpg"})()
    run(groups.clone(Event(Msg("rasm izohi", media="photo", file=file))))
    assert not sent["text"]
    args, kw = sent["media"][0]
    assert args[2] == b"jpg-bytes" and kw["kind"] == "rasm" and kw["caption"] == "rasm izohi"
    assert kw["link_reply"] is False                           # nusxaga reply qilinsa guruhga ketmaydi


def test_empty_service_message_skipped(monkeypatch, store):
    sent = _capture(monkeypatch)
    run(groups.clone(Event(Msg(""))))
    assert sent == {"text": [], "media": []}


def test_only_enabled_groups_are_queued(monkeypatch, store):
    handlers = []

    class User:
        def on(self, ev):
            def deco(fn):
                handlers.append((ev, fn))
                return fn
            return deco

    monkeypatch.setattr(groups, "_queue", None)
    groups.setup(User(), None, None)
    ev, _ = handlers[0]
    run(groups.toggle(-100, "Dasturchilar"))
    on = type("E", (), {"is_group": True, "chat_id": -100})()
    off = type("E", (), {"is_group": True, "chat_id": -200})()
    private = type("E", (), {"is_group": False, "chat_id": -100})()
    assert ev.func(on) and not ev.func(off) and not ev.func(private)
