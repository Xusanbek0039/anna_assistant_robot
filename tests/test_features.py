"""Tahrir ogohlantirishi, profil kuzatuvi, arxivdan qidirish, AI xulosa, va'dalar, haftalik hisobot, zaxira."""
import asyncio
import gzip
import os
from datetime import datetime, timezone

import pytest
from telethon.tl.types import User, UserProfilePhoto

import ai
import archive
import backup
import config
import control_bot
import finder
import profiles
import promises
import restore_backup
import runtime
import storage
import weekly
from conftest import run
from test_archive import Ev, Msg, _person, arc  # noqa: F401  (arc — fixture)
from test_gdrive import google  # noqa: F401  (fixture)


@pytest.fixture
def rt(monkeypatch):
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    monkeypatch.setattr(runtime, "_listeners", [])


# ---------- 1. tahrir ogohlantirishi ----------
@pytest.fixture
def edits(arc):  # noqa: F811
    got = []

    async def on_edit(chat_id, old, new_text):
        got.append((chat_id, old["text"], new_text))

    async def on_del(chat_id, docs):
        pass

    archive.setup(arc.user, 1, on_del, on_edit)
    return got


def _edit(arc, msg, new_text, person=None):  # noqa: F811
    msg.message = new_text
    run(arc.user.handlers["archive_edited"](Ev(msg, person or _person())))


def test_edit_alert_shows_old_and_new(arc, edits):  # noqa: F811
    m = Msg("1000 so'm beraman")
    run(arc.user.handlers["archive_new"](Ev(m, _person())))
    _edit(arc, m, "100 so'm beraman")
    assert edits == [(5, "1000 so'm beraman", "100 so'm beraman")]


def test_no_alert_for_reaction_own_edit_or_when_off(arc, edits):  # noqa: F811
    m = Msg("salom")
    run(arc.user.handlers["archive_new"](Ev(m, _person())))
    _edit(arc, m, "salom")                      # reaksiya/pin — matn o'zgarmagan
    mine = Msg("men yozdim", out=True)
    run(arc.user.handlers["archive_new"](Ev(mine, _person())))
    _edit(arc, mine, "men tahrirladim")         # o'zimizning tahririmiz
    run(runtime.update(edit_alerts=False))
    _edit(arc, m, "boshqa matn")                # o'chirib qo'yilgan
    assert edits == []
    assert arc.msgs[(5, mine.id)]["text"] == "men tahrirladim"   # arxivda baribir yangilanadi


def test_edited_alert_message(monkeypatch, arc):  # noqa: F811
    sent = []

    class Bot:
        async def send_message(self, uid, text, **kw):
            sent.append(text)

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    run(control_bot.edited_alert(5, {"text": "<eski>", "date": 1.7e9}, "yangi"))
    assert "&lt;eski&gt;" in sent[0] and "yangi" in sent[0] and "tahrirlandi" in sent[0]


# ---------- 2-3. profil kuzatuvi ----------
def _user(**kw):
    base = dict(id=5, first_name="Ali", last_name=None, username="ali")
    base.update(kw)
    return User(**base)


def test_profile_diff():
    old = profiles.snapshot(_user(photo=UserProfilePhoto(photo_id=1, dc_id=2)), "dasturchi")
    assert profiles.diff(old, dict(old)) == []
    new = profiles.snapshot(_user(first_name="Alisher", username="alisher",
                                  photo=UserProfilePhoto(photo_id=2, dc_id=2)), "dizayner")
    changes = profiles.diff(old, new)
    assert any("«Ali» → «Alisher»" in c for c in changes)
    assert any("@ali → @alisher" in c for c in changes)
    assert any("Bio" in c for c in changes) and any("Yangi profil rasmi" in c for c in changes)
    assert profiles.diff(old, profiles.snapshot(_user(deleted=True))) == ["❌ Akkauntini o'chirdi"]
    gone = profiles.diff(old, profiles.snapshot(_user()))
    assert any("yashirdi" in c for c in gone)
    # bio getUsers'da kelmaydi — "o'zgardi" deb hisoblanmaydi
    same_photo = profiles.snapshot(_user(photo=UserProfilePhoto(photo_id=1, dc_id=2)))   # about yo'q
    assert profiles.diff(old, same_photo) == []


@pytest.fixture
def prof(monkeypatch, rt):
    db, alerts, saved = {}, [], []

    async def get(uid):
        return dict(db[uid]) if uid in db else None

    async def set_(uid, **f):
        db.setdefault(uid, {}).update(f)

    async def add_photo(uid, pid):
        db.setdefault(uid, {}).setdefault("saved_photos", []).append(pid)

    async def chat(uid):
        return {"chat_id": uid, "title": "Ali", "kind": "user"}

    async def alert(uid, info, changes, photo, link):
        alerts.append((uid, changes, photo))

    async def save_photo(uid, pid, data, when=None):
        saved.append(pid)
        return "https://drive/p"

    for name, fn in [("profile_get", get), ("profile_set", set_), ("profile_add_saved_photo", add_photo),
                     ("archive_chat", chat)]:
        monkeypatch.setattr(storage, name, fn, raising=False)
    monkeypatch.setattr(profiles, "_alert", alert)
    monkeypatch.setattr(profiles, "_save_photo", save_photo)
    return db, alerts, saved


class _Tg:
    async def download_profile_photo(self, user, file=None):
        return b"JPEG"

    async def iter_profile_photos(self, user, limit=None):
        return
        yield


def test_first_snapshot_silent_then_changes_alerted(prof):
    db, alerts, saved = prof
    assert run(profiles.check_user(_Tg(), _user())) == []           # birinchi ko'rish — jim
    assert alerts == [] and db[5]["first"] == "Ali"
    run(profiles.check_user(_Tg(), _user(first_name="Alisher", photo=UserProfilePhoto(photo_id=9, dc_id=2))))
    (uid, changes, photo), = alerts
    assert uid == 5 and photo == b"JPEG" and saved == [9]
    assert any("Alisher" in c for c in changes)
    run(profiles.check_user(_Tg(), _user(first_name="Alisher", photo=UserProfilePhoto(photo_id=9, dc_id=2))))
    assert len(alerts) == 1                                         # o'zgarmagan — qayta xabar yo'q


def test_profile_alert_off_and_bots_ignored(prof):
    db, alerts, _ = prof
    run(profiles.check_user(_Tg(), _user()))
    run(runtime.update(profile_watch=False))
    run(profiles.check_user(_Tg(), _user(first_name="X")))
    assert alerts == [] and db[5]["first"] == "X"                   # snapshot yangilanadi, xabar yo'q
    assert run(profiles.check_user(_Tg(), _user(id=9, bot=True))) == []
    assert 9 not in db


def test_profile_photo_saved_once_to_drive(google, monkeypatch, rt):  # noqa: F811
    db, chat = {}, {"chat_id": 5, "title": "Ali", "username": None}

    async def get(uid):
        return dict(db.get(uid, {}))

    async def add_photo(uid, pid):
        db.setdefault(uid, {}).setdefault("saved_photos", []).append(pid)

    async def get_chat(cid):
        return dict(chat)

    async def chat_set(cid, **f):
        chat.update(f)

    for name, fn in [("profile_get", get), ("profile_add_saved_photo", add_photo),
                     ("archive_chat", get_chat), ("archive_chat_set", chat_set)]:
        monkeypatch.setattr(storage, name, fn, raising=False)
    assert run(profiles._save_photo(5, 77, b"IMG")).startswith("https://drive/")
    assert run(profiles._save_photo(5, 77, b"IMG")) is None          # bir rasm bir marta
    f, = google.files.values()
    folder = google.folders[f["parents"][0]]
    assert folder["name"] == "profil_rasmlari" and f["name"].endswith("_77.jpg")


# ---------- 4. arxivdan qidirish ----------
def test_archive_results_marked_and_not_forwarded():
    live = [finder.Found(chat_id=5, id=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc), mine=False,
                         text="wifi paroli 123")]
    arch = [finder.from_archive({"chat_id": 5, "msg_id": 2, "date": 1.7e9, "out": False,
                                 "text": "karta 8600", "deleted": True}),
            finder.from_archive({"chat_id": 5, "msg_id": 1, "date": 1.7e9, "out": False, "text": "wifi paroli 123",
                                 "edits": [{"text": "wifi paroli 999"}]})]
    merged = finder.merge_archive(live, arch)
    assert len(merged) == 2                                          # bir xil xabar ikki marta chiqmaydi
    assert next(f for f in merged if f.id == 1).old_text == "wifi paroli 999"
    assert [f.id for f in finder.forwardable(merged)] == [1]         # o'chirilgani forward qilinmaydi
    text = finder.format_found("Ali", finder.Query(name="Ali"), "", merged, 2)
    assert "o'chirilgan" in text and "avval: «wifi paroli 999»" in text
    assert "(o'chirilgan xabar)" in finder._ai_line(arch[0], 1)


# ---------- 5-6. AI xulosa va va'dalar ----------
def test_parse_summary_is_defensive():
    res = ai.parse_summary({"summary": " Kelishildi ", "topics": ["pul", ""], "promises": [
        {"who": "Siz", "text": "pul o'tkazaman", "due": "2026-11-20"},
        {"who": "u", "text": "hujjat yuboraman", "due": "ertaga"},
        "noto'g'ri", {"who": "men", "text": "  "}]})
    assert res["summary"] == "Kelishildi" and res["topics"] == ["pul"]
    assert res["promises"] == [{"who": "men", "text": "pul o'tkazaman", "due": "2026-11-20"},
                               {"who": "u", "text": "hujjat yuboraman", "due": None}]
    assert ai.parse_summary({})["promises"] == []


def test_chat_lines_keep_newest_and_marks():
    docs = [{"date": 1.7e9 + i, "out": i % 2 == 0, "text": f"xabar {i}"} for i in range(50)]
    docs[-1].update(deleted=True)
    text = archive.chat_lines_from(docs, "Ali", max_chars=200)
    assert "xabar 49" in text and "xabar 0" not in text             # eng yangilari qoladi
    assert "(o'chirilgan)" in text and "Ali" in text and "Siz" in text


def test_only_my_promises_saved(monkeypatch):
    saved = []

    async def add(chat_id, title, text, due, source):
        saved.append(text)
        return f"id{len(saved)}"
    monkeypatch.setattr(storage, "promise_add", add, raising=False)
    out = run(promises.save_promises(5, "Ali", [{"who": "men", "text": "pul", "due": None},
                                                {"who": "u", "text": "hujjat", "due": None}], "summary"))
    assert saved == ["pul"] and out[0]["id"] == "id1"


def test_scan_stops_when_ai_limit_reached(monkeypatch):
    calls = []

    async def active(since, limit):
        return [{"chat_id": i} for i in range(5)]

    async def summarize(chat_id, days):
        calls.append(chat_id)
        raise promises.NoAI("limit")
    monkeypatch.setattr(storage, "archive_active_chats", active, raising=False)
    monkeypatch.setattr(promises, "summarize", summarize)
    assert run(promises.scan_recent()) == [] and calls == [0]


def test_summary_text_escapes():
    text = control_bot._summary_text({"chat_id": 5, "title": "<Ali>"}, 7, 10,
                                     {"summary": "a<b", "topics": ["x"], "open_questions": ["?<"],
                                      "promises": [{"who": "men", "text": "<pul>", "due": "2026-11-20"}]}, [{}])
    assert "<Ali>" not in text and "a&lt;b" in text and "&lt;pul&gt;" in text and "2026-11-20" in text


def test_new_buttons_fit_telegram_limit():
    pid = "a" * 24
    rows = (control_bot._promise_buttons(pid) + control_bot._summary_buttons(-1001234567890)
            + control_bot._watch_panel()[1])
    for row in rows:
        for b in row:
            data = getattr(b, "data", None) or b.type.data
            assert len(data) <= 64


# ---------- 7. haftalik hisobot ----------
def test_weekly_report():
    now = 1.8e9
    stats = {"active": [{"_id": 5, "inc": 30, "out": 12}, {"_id": 7, "inc": 3, "out": 0}],
             "deleted": [{"_id": 5, "n": 4}], "edited": [{"_id": 7, "n": 2}],
             "unanswered": [{"_id": 7, "date": now - 3 * 86400, "text": "<javob bering>", "out": False}],
             "chats": {5: {"title": "Ali", "username": "ali"}, 7: {"title": "Vali"}}}
    text, ids = weekly.build(stats, {"done": 10, "pending": 2, "history": 1}, now=now)
    assert "📥 33 ta kelgan, 📤 12 ta yuborilgan" in text and "Ali (@ali) — 4 ta" in text
    assert "3 kundan beri" in text and "&lt;javob bering&gt;" in text and ids == [7]
    assert "3 ta navbatda" in text


# ---------- 8. zaxira ----------
class _Coll:
    def __init__(self, docs):
        self.docs, self.ops = docs, []

    def find(self, query):
        docs = self.docs

        class Cur:
            def __aiter__(self):
                self._it = iter(docs)
                return self

            async def __anext__(self):
                try:
                    return next(self._it)
                except StopIteration:
                    raise StopAsyncIteration
        return Cur()

    async def bulk_write(self, ops, ordered=False):
        self.ops += ops


def test_backup_uploads_and_restores_round_trip(google, monkeypatch, tmp_path, rt):  # noqa: F811
    from bson import Binary, ObjectId
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    oid = ObjectId()
    colls = {"archive": _Coll([{"_id": oid, "text": "salom", "n": 5, "f": 1.5}]),
             "archive_media": _Coll([{"_id": ObjectId(), "data": Binary(b"\x00\xffIMG")}]),
             "locks": _Coll([{"_id": "main"}])}

    async def names():
        return sorted(colls)
    monkeypatch.setattr(storage, "collection_names", names, raising=False)
    monkeypatch.setattr(storage, "raw_collection", lambda n: colls[n], raising=False)
    # eski zaxira papkalari: 2 tasi muddati o'tgan
    root = run(__import__("gdrive").ensure_folder(backup.FOLDER))
    for day in ("2020-01-01", "2020-01-02", "2999-01-01", "boshqa papka"):
        google.folders[f"old-{day}"] = {"name": day, "parent": root}

    res = run(backup.run_backup())
    names_up = sorted(f["name"] for f in google.files.values())
    assert names_up == ["archive.jsonl.gz", "archive_media.jsonl.gz"]   # locks — zaxiralanmaydi
    assert res["docs"] == 2 and res["removed"] == 2
    assert sorted(google.trashed) == ["2020-01-01", "2020-01-02"]      # boshqalariga tegilmadi
    assert [p for p in tmp_path.iterdir() if p.name.startswith("anna_backup_")] == []
    run(backup.run_backup())                                          # bir kunda ikkinchi marta
    assert len(google.files) == 2                                     # nusxalar ko'paymadi — yangilandi

    # tiklash: Drive'dagi fayllarni diskka qo'yib, restore_backup bilan qaytarish
    for f in google.files.values():
        (tmp_path / f["name"]).write_bytes(f["data"])
    target = {"archive": _Coll([]), "archive_media": _Coll([])}
    monkeypatch.setattr(storage, "raw_collection", lambda n: target[n], raising=False)
    for path, name in restore_backup.find_files(str(tmp_path), None):
        run(restore_backup.restore_file(path, name))
    doc = target["archive"].ops[0]._doc
    assert doc == {"_id": oid, "text": "salom", "n": 5, "f": 1.5}      # turlar aynan tiklandi
    assert bytes(target["archive_media"].ops[0]._doc["data"]) == b"\x00\xffIMG"


def test_backup_requires_drive(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_OAUTH_REFRESH_TOKEN", "")
    with pytest.raises(RuntimeError):
        run(backup.run_backup())


def test_schedule_runs_once_per_day(monkeypatch, fast_sleep):
    import schedule
    state, ran = {}, []

    async def get_rt():
        return dict(state)

    async def set_rt(**v):
        state.update(v)
    monkeypatch.setattr(storage, "get_runtime", get_rt)
    monkeypatch.setattr(storage, "set_runtime", set_rt)

    async def job():
        ran.append(1)
        if len(ran) >= 1:
            pass

    async def go():
        task = asyncio.create_task(schedule.daily("t", "04:00", "last_t", job))
        for _ in range(50):
            await asyncio.sleep(0)
        task.cancel()
    run(go())
    assert ran == [1] and state["last_t"]                              # kuniga bir marta
