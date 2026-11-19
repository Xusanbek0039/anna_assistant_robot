"""O'chirilgan akkauntlar: so'rovni tanish, ismni taxmin qilish, arxivga ko'chirish."""
from types import SimpleNamespace

import pytest
from telethon.tl.types import Message, PeerUser, User

import archive
import config
import deleted_accounts as da
import finder
import storage
from conftest import run


@pytest.mark.parametrize("text", [
    "o'chirilgan akkauntlarni ko'rsat",
    "delete qilib yuborilgan hisoblarni sanab ber",
    "delete akkauntdagi yozishmalar",
    "deleted account",
    "удалённые аккаунты покажи",
    "akkauntini o'chirganlar kimlar",
])
def test_asks_for_list(text):
    assert da.asks_for_list(text)


@pytest.mark.parametrize("text", [
    "Ali yuborgan manzilni top",
    "nonga 25 ming ishlatdim",
    "o'chirilgan xabarlarni top",
    "kechagi yozuvni hisobdan o'chir",
])
def test_not_list(text):
    assert not da.asks_for_list(text)


def test_name_words_strips_filler():
    assert da.name_words("o'chirilgan akkauntlardan Aziz akani top") == "Aziz akani"
    assert da.name_words("o'chirilgan akkauntlarni ko'rsat") == ""


def test_wants_chat():
    assert finder.wants_chat("Aziz bilan chatni top")
    assert finder.wants_chat("Dilnoza bilan yozishmani topib ber")
    assert not finder.wants_chat("Ali yuborgan manzilni top")


def _doc(text, out=True, i=1):
    return {"chat_id": 9, "msg_id": i, "text": text, "out": out, "date": 1_700_000_000 + i}


def test_heuristic_name_prefers_titled_form():
    docs = [_doc("Salom Aziz, qalaysiz?", i=1), _doc("Aziz aka, ertaga uchrashamizmi?", i=2),
            _doc("Rahmat Aziz aka", i=3), _doc("Salom Sardor", out=False, i=4)]
    assert da.heuristic_name(docs) == "Aziz aka"


def test_heuristic_name_empty():
    assert da.heuristic_name([_doc("ok"), _doc("ha mayli")]) == ""


def test_chat_label_and_describe():
    assert archive.chat_label({"chat_id": 9, "title": "Aziz aka"}) == "Aziz aka"
    guessed = {"chat_id": 9, "title": "9", "account_deleted": True, "guess_title": "Dilnoza",
               "guess_conf": "medium", "guess_hint": "«Salom Dilnoza» deb yozgansiz"}
    assert archive.chat_label(guessed) == "≈ Dilnoza"
    assert "taxminiy" in da.describe(guessed) and "Dilnoza" in da.describe(guessed)
    assert archive.chat_label({"chat_id": 9, "title": "9", "account_deleted": True}) == "O'chirilgan akkaunt #9"
    assert "siz saqlagan" in da.describe({"chat_id": 9, "title": "Aziz aka"})


class FakeDB:
    def __init__(self):
        self.chats: dict[int, dict] = {}
        self.msgs: dict[tuple, dict] = {}
        self.profiles: dict[int, dict] = {}

    async def archive_messages(self, chat_id, only_deleted=False, limit=0):
        return sorted((d for d in self.msgs.values() if d["chat_id"] == chat_id), key=lambda d: d["date"])

    async def archive_chat_set(self, chat_id, **fields):
        self.chats.setdefault(chat_id, {"chat_id": chat_id}).update(fields)

    async def profile_get(self, uid):
        return self.profiles.get(uid)

    async def archive_deleted_accounts(self, limit=300):
        return [dict(c) for c in self.chats.values() if c.get("account_deleted")]

    async def archive_mark_account_deleted(self, chat_id, title=""):
        c = self.chats.setdefault(chat_id, {"chat_id": chat_id, "title": str(chat_id), "count": 0})
        c["account_deleted"] = True
        if title:
            c["title"] = title

    async def archive_last_msg_id(self, chat_id):
        return max((d["msg_id"] for d in self.msgs.values() if d["chat_id"] == chat_id), default=0)

    async def archive_add_many(self, docs):
        n = 0
        for d in docs:
            n += (d["chat_id"], d["msg_id"]) not in self.msgs
            self.msgs.setdefault((d["chat_id"], d["msg_id"]), d)
        return n

    async def archive_touch_chat(self, chat_id, title, username, kind, ts, added=1, account_deleted=False):
        c = self.chats.setdefault(chat_id, {"chat_id": chat_id, "count": 0})
        c["count"] = c.get("count", 0) + added
        c["account_deleted"] = c.get("account_deleted") or account_deleted

    async def get_stats(self):
        return {}

    async def stat_inc(self, **k):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for name in dir(fake):
        if not name.startswith("_") and name not in ("chats", "msgs", "profiles"):
            monkeypatch.setattr(storage, name, getattr(fake, name))
    return fake


def test_name_chat_uses_profile_name_first(db):
    db.chats[9] = {"chat_id": 9, "title": "9", "account_deleted": True}
    db.profiles[9] = {"deleted": True, "known_name": "Aziz Karimov"}
    res = run(da.name_chat(db.chats[9], use_ai=False))
    assert res == {"title": "Aziz Karimov"} and db.chats[9]["title"] == "Aziz Karimov"


def test_name_chat_keeps_saved_title(db):
    db.chats[9] = {"chat_id": 9, "title": "Aziz aka", "account_deleted": True}
    assert run(da.name_chat(db.chats[9], use_ai=False)) is None


def test_name_chat_rule_fallback_and_no_repeat(db):
    db.chats[9] = {"chat_id": 9, "title": "9", "account_deleted": True}
    for i, t in enumerate(["Salom Dilnoza", "Dilnoza opa, rahmat", "ha", "ertaga ko'rishamiz"], 1):
        db.msgs[(9, i)] = _doc(t, i=i)
    res = run(da.name_chat(db.chats[9], use_ai=False))
    assert res["guess_title"] == "Dilnoza opa" and res["guess_source"] == "rule"
    # o'sha yozishma uchun qayta urinilmaydi
    assert run(da.name_chat(dict(db.chats[9]), use_ai=False)) is None


def test_name_chat_ai(db, monkeypatch):
    db.chats[9] = {"chat_id": 9, "title": "9", "account_deleted": True}
    for i in range(1, 5):
        db.msgs[(9, i)] = _doc(f"gap {i}", i=i)

    async def fake_guess(docs):
        return {"name": "Taksi haydovchisi", "confidence": "low", "hint": "yo'l haqida gaplashgansiz"}, 50

    monkeypatch.setattr(da, "ai_guess", fake_guess)
    res = run(da.name_chat(db.chats[9], use_ai=True))
    assert res["guess_title"] == "Taksi haydovchisi" and res["guess_source"] == "ai"


def _msg(mid, text, out=False):
    return Message(id=mid, peer_id=PeerUser(9), date=None, message=text, out=out)


def test_scan_archives_deleted_account_chats(db, monkeypatch):
    monkeypatch.setattr(config, "DRIVE_MEDIA_HISTORY", False)
    gone = User(id=9, deleted=True)
    alive = User(id=5, first_name="Ali")
    dialogs = [SimpleNamespace(entity=gone, is_user=True, id=9, message=_msg(3, "x")),
               SimpleNamespace(entity=alive, is_user=True, id=5, message=_msg(1, "y"))]

    class Client:
        async def iter_dialogs(self):
            for d in dialogs:
                yield d

        async def iter_messages(self, ent, limit=None, min_id=0):
            assert ent is gone
            for m in (_msg(3, "Salom Aziz", out=True), _msg(2, "salom"), _msg(1, "qalaysiz")):
                if m.id > min_id:
                    yield m

    found, added = run(da.scan(Client()))
    assert (found, added) == (1, 3)
    assert db.chats[9]["account_deleted"] and 5 not in db.chats
    assert run(da.scan(Client())) == (1, 0)  # ikkinchi marta — hammasi arxivda


def test_is_deleted_account():
    assert archive.is_deleted_account(User(id=1, deleted=True))
    assert not archive.is_deleted_account(User(id=1, first_name="Ali"))
    assert not archive.is_deleted_account(None)
