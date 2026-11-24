"""📸 Instagram arxivi: eski yozishmalarni olish (backfill), Google Drive'ga yuklash, botda o'qish."""
import asyncio
import time

import pytest

import config
import gdrive
import instagram
import runtime
import storage
from conftest import run
from test_instagram import FakeCl, FakeIgStorage, STARTED, NEW, connect, text_item, raven, thread


class PagedCl(FakeCl):
    """direct_v2/threads/<id>/ so'rovini sahifalab beradigan soxta klient."""

    def __init__(self, pages: dict[str, list[dict]], title="Ali Valiyev"):
        super().__init__()
        self.pages, self.title = pages, title     # cursor -> xabarlar
        self.requests: list[tuple[str, str]] = []

    def private_request(self, endpoint, data=None, params=None, **kw):
        self.calls.append(endpoint)
        if self.error:
            raise self.error
        if endpoint.startswith("direct_v2/threads/"):
            tid = endpoint.split("/")[2]
            cursor = (params or {}).get("cursor", "")
            self.requests.append((tid, cursor))
            items, nxt = self.pages.get(cursor, ([], None))
            return {"thread": {"thread_id": tid, "thread_title": self.title, "is_group": False,
                               "items": list(reversed(items)), "oldest_cursor": nxt,
                               "users": [{"pk": 2, "username": "ali.v", "full_name": self.title}]}}
        return super().private_request(endpoint, data, params, **kw)


def page(items, next_cursor=None):
    return (items, next_cursor)


@pytest.fixture
def ig(monkeypatch):
    """test_instagram.py dagi fixture bilan bir xil toza holat."""
    fs = FakeIgStorage()
    for name in dir(fs):
        if name.startswith(("ig_", "get_mode")):
            monkeypatch.setattr(storage, name, getattr(fs, name))
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    monkeypatch.setattr(runtime, "_listeners", [])
    monkeypatch.setattr(storage, "set_runtime", lambda **kw: asyncio.sleep(0))
    st = instagram._State()
    st.started = STARTED
    monkeypatch.setattr(instagram, "st", st)
    monkeypatch.setattr(instagram, "st2", instagram._State("2"))
    monkeypatch.setattr(instagram, "_thread_acc", {})
    for name in ("_known", "_missing", "_muted_until", "_alerted"):
        monkeypatch.setattr(instagram, name, {})
    monkeypatch.setattr(instagram, "_ai_sent",
                        instagram.defaultdict(lambda: instagram.deque(maxlen=50)))
    for name in ("_check_lock", "_api_lock", "_login_lock", "_drive_lock"):
        monkeypatch.setattr(instagram, name, asyncio.Lock())
    monkeypatch.setattr(instagram, "_drive_folder_id", "")
    monkeypatch.setattr(instagram, "drive_state", {"ok": None, "problem": "", "last": 0.0, "files": 0})
    monkeypatch.setattr(instagram, "BACKFILL_PAUSE", 0)
    monkeypatch.setattr(instagram, "PROXY", "")
    alerts = []

    async def alert(text):
        alerts.append(text)

    instagram.setup(alert, None, None)
    out = type("O", (), {})()
    out.fs, out.alerts = fs, alerts
    return out


# ---------- eski yozishmalarni olish ----------
def test_backfill_walks_pages_until_cursor_ends(ig):
    old = [text_item(str(i), "2", f"eski {i}", ts=NEW - (30 - i) * 1_000_000) for i in range(3)]
    older = [text_item(f"b{i}", "2", f"juda eski {i}", ts=NEW - (60 - i) * 1_000_000) for i in range(3)]
    cl = PagedCl({"": page(old, "c1"), "c1": page(older, None)})
    connect(cl)
    added = run(instagram.backfill_thread("t1", 1000))
    assert added == 6 and len(ig.fs.archive) == 6
    assert [c for _, c in cl.requests] == ["", "c1"]            # cursor bo'yicha yurdi
    assert ig.fs.chats["t1"]["title"] == "Ali Valiyev" and ig.fs.chats["t1"]["count"] == 6
    assert "backfilled" in ig.fs.chats["t1"]                    # qayta olinmaydi
    assert ig.fs.archive[("t1", "0")]["text"] == "eski 0"


def test_backfill_stops_at_limit(ig):
    pages = {"": page([text_item(str(i), "2", "x", ts=NEW - i * 1000) for i in range(5)], "c1"),
             "c1": page([text_item(f"b{i}", "2", "y", ts=NEW - 99_000 - i * 1000) for i in range(5)], "c2")}
    cl = PagedCl(pages)
    connect(cl)
    run(instagram.backfill_thread("t1", 5))                     # 5 ta yetdi — ikkinchi sahifa so'ralmaydi
    assert len(cl.requests) == 1 and len(ig.fs.archive) == 5


def test_backfill_is_idempotent(ig):
    items = [text_item(str(i), "2", "x", ts=NEW - i * 1000) for i in range(3)]
    cl = PagedCl({"": page(items, None)})
    connect(cl)
    assert run(instagram.backfill_thread("t1", 100)) == 3
    ig.fs.chats["t1"].pop("backfilled")
    assert run(instagram.backfill_thread("t1", 100)) == 0       # ikkinchi marta yangi xabar yo'q
    assert len(ig.fs.archive) == 3


def test_backfill_request_does_not_mark_seen_or_open_media(ig):
    cl = PagedCl({"": page([raven("r1", ts=NEW - 5000), text_item("1", "2", "x", ts=NEW - 6000)], None)})
    connect(cl)
    saved = []

    async def send_media(data, filename, caption, video, as_file):
        saved.append(filename)
        return True

    async def download(url, limit=0, on_progress=None):
        return b"D", "image/jpeg"

    instagram.setup(lambda t: asyncio.sleep(0), send_media, None)
    object.__setattr__(instagram, "_download", download)
    run(instagram.backfill_thread("t1", 100))
    assert all("seen" not in c for c in cl.calls)               # «o'qildi» so'rovi yo'q
    assert all((p or {}) and True for p in [1])
    assert saved == ["instagram_r1.jpg"]                        # ochilmagan media saqlab olindi
    assert ig.fs.archive[("t1", "r1")]["view_once"] is True


def test_backfill_all_marks_failed_chat_and_continues(ig, monkeypatch):
    run(storage.ig_chat_touch("t1", "Ali", ["ali"], False, NEW / 1e6, 1))
    run(storage.ig_chat_touch("t2", "Vali", ["vali"], False, NEW / 1e6, 1))
    connect(FakeCl())
    calls = []

    async def fake(thread_id, limit, title=""):
        calls.append(thread_id)
        if thread_id == "t1":
            raise RuntimeError("tarmoq")
        return 7

    monkeypatch.setattr(instagram, "backfill_thread", fake)
    chats, msgs = run(instagram.backfill_all())
    assert sorted(calls) == ["t1", "t2"] and chats == 1 and msgs == 7
    # Yiqilgan chat ham «olindi» deb belgilanadi — har soatda qayta urinib Instagram'ni bezovta qilmaydi
    assert "backfilled" in ig.fs.chats["t1"]
    assert [r["thread_id"] for r in run(storage.ig_backfill_pending())] == ["t2"]   # (soxta funksiya belgilamaydi)


def test_backfill_all_stops_on_auth_error(ig, monkeypatch):
    for i in range(3):
        run(storage.ig_chat_touch(f"t{i}", f"C{i}", [], False, NEW / 1e6, 1))
    connect(FakeCl())
    calls = []

    async def fake(thread_id, limit, title=""):
        calls.append(thread_id)
        raise type("LoginRequired", (Exception,), {})()

    monkeypatch.setattr(instagram, "backfill_thread", fake)
    chats, _ = run(instagram.backfill_all())
    assert chats == 0 and len(calls) == 1 and instagram.st.client is None   # darhol to'xtadi


def test_backfill_needs_connection(ig):
    with pytest.raises(instagram.LoginError):
        run(instagram.backfill_thread("t1", 10))


# ---------- ☁️ Google Drive ----------
@pytest.fixture
def drive(monkeypatch):
    up = []

    async def ensure_folder(name, parent=None):
        return f"folder-{name}"

    async def upload(name, data=None, mime="text/html", file_id=None, parent=None, path=None):
        up.append({"name": name, "data": data, "file_id": file_id, "parent": parent})
        return {"id": f"file{len(up)}", "webViewLink": f"https://drive.google.com/file/d/file{len(up)}/view"}

    monkeypatch.setattr(gdrive, "enabled", lambda: True)
    monkeypatch.setattr(gdrive, "missing", lambda: [])
    monkeypatch.setattr(gdrive, "ensure_folder", ensure_folder)
    monkeypatch.setattr(gdrive, "upload", upload)
    return up


def _archive_chat(fs, tid="t1", title="Ali Valiyev", n=2):
    docs = [{"thread_id": tid, "msg_id": f"m{i}", "date": NEW / 1e6 + i, "out": False,
             "sender": title, "username": "ali.v", "kind": "", "text": f"xabar {i}",
             "media": None, "view_once": False, "ai": False} for i in range(n)]
    run(storage.ig_archive_add_many(docs))
    run(storage.ig_chat_touch(tid, title, ["ali.v"], False, NEW / 1e6 + n, n))


def test_drive_upload_creates_html_in_instagram_folder(ig, drive):
    _archive_chat(ig.fs)
    link = run(instagram.drive_sync_thread("t1"))
    assert len(drive) == 1
    f = drive[0]
    assert f["name"] == "Ali Valiyev (@ali.v).html" and f["parent"] == "folder-Instagram"
    assert f["file_id"] is None and "📸 Ali Valiyev".encode() in f["data"] and b"xabar 1" in f["data"]
    assert link.endswith("/view") and ig.fs.chats["t1"]["drive_link"] == link
    assert ig.fs.chats["t1"]["drive_dirty"] is False            # yuklandi — belgi olindi


def test_drive_updates_same_file_and_only_changed(ig, drive):
    _archive_chat(ig.fs)
    run(instagram.drive_sync_thread("t1"))
    assert run(instagram.drive_sync_pending()) == 0             # o'zgarish yo'q — qayta yuklanmaydi
    run(storage.ig_archive_add_many([{"thread_id": "t1", "msg_id": "new", "date": NEW / 1e6 + 9,
                                      "out": False, "sender": "Ali", "text": "yangi", "media": None,
                                      "kind": "", "view_once": False, "ai": False}]))
    run(storage.ig_chat_touch("t1", "Ali Valiyev", ["ali.v"], False, NEW / 1e6 + 9, 1))
    assert run(instagram.drive_sync_pending()) == 1
    assert len(drive) == 2 and drive[1]["file_id"] == "file1"   # o'sha fayl yangilandi
    assert b"yangi" in drive[1]["data"]


def test_drive_recreates_missing_folder(ig, drive, monkeypatch):
    _archive_chat(ig.fs)
    calls = []
    real = gdrive.upload

    async def flaky(name, data=None, mime="text/html", file_id=None, parent=None, path=None):
        calls.append(file_id)
        if len(calls) == 1:
            raise gdrive.DriveError(404, "File not found")
        return await real(name, data, mime, file_id, parent=parent)

    monkeypatch.setattr(gdrive, "upload", flaky)
    run(storage.ig_chat_set("t1", drive_file_id="eski"))
    link = run(instagram.drive_sync_thread("t1"))
    assert calls == ["eski", None] and link                     # yangi fayl qilib yaratildi


def test_drive_fatal_error_stops_batch(ig, drive, monkeypatch):
    _archive_chat(ig.fs, "t1")
    _archive_chat(ig.fs, "t2", "Vali")

    async def boom(*a, **kw):
        raise gdrive.DriveError(401, "invalid_grant")

    monkeypatch.setattr(gdrive, "upload", boom)
    with pytest.raises(gdrive.DriveError):
        run(instagram.drive_sync_pending())


def test_drive_skipped_when_toggled_off(ig, drive, monkeypatch):
    _archive_chat(ig.fs)
    monkeypatch.setitem(runtime._values, "ig_drive", False)
    assert instagram.drive_on() is False
    assert "o'chirilgan" in run(instagram.drive_now()) and drive == []
    assert "yoqildi" in run(instagram.toggle("ig_drive")) and runtime.get("ig_drive") is True
    assert instagram.drive_on() is True


def test_drive_now_reports_missing_config(ig, monkeypatch):
    monkeypatch.setattr(gdrive, "enabled", lambda: False)
    monkeypatch.setattr(gdrive, "missing", lambda: ["GOOGLE_DRIVE_ROOT_FOLDER_ID"])
    assert "GOOGLE_DRIVE_ROOT_FOLDER_ID" in run(instagram.drive_now())


@pytest.mark.parametrize("info,name", [
    ({"title": "Ali Valiyev", "usernames": ["ali.v"]}, "Ali Valiyev (@ali.v).html"),
    ({"title": "Guruh: Do'stlar", "usernames": ["a", "b", "c"]}, "Guruh- Do'stlar (@a, @b).html"),
    ({"title": "a/b:c*d?e", "usernames": []}, "a-b-c-d-e.html"),
    ({"thread_id": "999"}, "999.html"),
])
def test_drive_file_name_is_safe(info, name):
    assert instagram.drive_file_name(info) == name


# ---------- botda yozishmani o'qish ----------
def test_chat_view_shows_messages_newest_first(ig):
    docs = [{"thread_id": "t1", "msg_id": f"m{i}", "date": NEW / 1e6 + i, "out": i % 2 == 1,
             "sender": "Siz" if i % 2 else "Ali Valiyev", "username": "" if i % 2 else "ali.v",
             "kind": "", "text": f"xabar {i}", "media": None, "view_once": False, "ai": False}
            for i in range(20)]
    run(storage.ig_archive_add_many(docs))
    run(storage.ig_chat_touch("t1", "Ali Valiyev", ["ali.v"], False, NEW / 1e6 + 19, 20))
    text, rows = run(instagram.chat_view("t1", 0))
    assert "Ali Valiyev" in text and "@ali.v" in text and "20 ta xabar" in text
    assert "xabar 19" in text and "xabar 5" in text and "xabar 4" not in text   # 15 tasi
    assert text.index("xabar 19") < text.index("xabar 18")                      # yangidan eskiga
    assert "🫵 <b>Siz</b>" in text
    datas = [d for row in rows for _, d in row]
    assert "ig:view:t1:1" in datas and "ig:html:t1" in datas
    assert all(len(d.encode()) <= 64 for d in datas)
    text2, rows2 = run(instagram.chat_view("t1", 1))
    assert "xabar 4" in text2 and "xabar 0" in text2
    assert "ig:view:t1:0" in [d for row in rows2 for _, d in row]                # «Yangiroq»


def test_chat_view_marks_media_unsent_and_ai(ig, drive):
    docs = [
        {"thread_id": "t1", "msg_id": "a", "date": NEW / 1e6, "out": False, "sender": "Ali",
         "kind": "rasm", "media": "rasm", "text": "", "view_once": True, "ai": False},
        {"thread_id": "t1", "msg_id": "b", "date": NEW / 1e6 + 1, "out": False, "sender": "Ali",
         "kind": "", "media": None, "text": "o'chirdim", "view_once": False, "ai": False,
         "deleted": True},
        {"thread_id": "t1", "msg_id": "c", "date": NEW / 1e6 + 2, "out": True, "sender": "Siz",
         "kind": "", "media": None, "text": "Anna javobi", "view_once": False, "ai": True},
    ]
    run(storage.ig_archive_add_many(docs))
    run(storage.ig_chat_touch("t1", "Ali", ["ali"], False, NEW / 1e6 + 2, 3))
    run(storage.ig_chat_set("t1", drive_link="https://drive.google.com/x"))
    text, _ = run(instagram.chat_view("t1", 0))
    assert "🔥 [rasm]" in text and "🗑 <i>qaytarib olingan</i>" in text and "🤖" in text
    assert "drive.google.com/x" in text


def test_chat_view_empty(ig):
    run(storage.ig_chat_touch("t1", "Ali", [], False, NEW / 1e6, 0))
    text, rows = run(instagram.chat_view("t1", 0))
    assert "hali arxivlangan xabar yo'q" in text
    assert [d for row in rows for _, d in row] == ["ig:html:t1", "ig:chats:0"]


def test_chats_view_shows_progress_and_opens_chat(ig):
    _archive_chat(ig.fs, "t1", "Ali Valiyev", 3)
    text, rows = run(instagram.chats_view(0))
    assert "1 ta chat, 3 ta xabar" in text and "Yana 1 ta chat" in text   # tarix hali olinmagan
    assert rows[0][0][1] == "ig:view:t1:0"
    run(storage.ig_chat_set("t1", backfilled=time.time()))
    text, _ = run(instagram.chats_view(0))
    assert "olinmoqda" not in text


def test_panel_shows_archive_and_drive_state(ig, drive):
    _archive_chat(ig.fs)
    text, rows = run(instagram.panel())
    assert "1 ta chat, 2 ta xabar" in text and "Instagram" in text
    datas = [d for row in rows for _, d in row]
    assert "ig:set:ig_drive" in datas and "ig:drive" in datas
    run(instagram.drive_sync_thread("t1"))
    instagram.drive_state.update(ok=True, last=time.time(), files=1)
    text, _ = run(instagram.panel())
    assert "1 ta fayl yuklandi" in text


def test_panel_without_drive_config(ig, monkeypatch):
    monkeypatch.setattr(gdrive, "enabled", lambda: False)
    monkeypatch.setattr(gdrive, "missing", lambda: ["GOOGLE_OAUTH_CLIENT_ID"])
    text, rows = run(instagram.panel())
    assert "GOOGLE_OAUTH_CLIENT_ID" in text
    assert "ig:drive" not in [d for row in rows for _, d in row]


def test_unsent_message_triggers_drive_reupload(ig, drive):
    """Xabar qaytarib olinsa — Drive'dagi nusxa ham yangilanadi (o'chirilgani belgilangan holda)."""
    from test_instagram import thread as ig_thread
    items = [text_item("1", "2", "qoladi", ts=NEW), text_item("2", "2", "o'chadi", ts=NEW + 1_000_000)]
    cl = FakeCl([ig_thread("t1", items)])
    connect(cl)
    run(instagram.check_once())
    run(instagram.drive_sync_thread("t1"))
    assert ig.fs.chats["t1"]["drive_dirty"] is False
    cl.inbox = [ig_thread("t1", items[:1])]
    run(instagram.check_once())
    run(instagram.check_once())                       # ikkinchi tekshiruvda tasdiqlanadi
    assert ig.fs.chats["t1"]["drive_dirty"] is True
    assert run(instagram.drive_sync_pending()) == 1
    assert b"o&#x27;chadi" in drive[-1]["data"] and b"tdel" in drive[-1]["data"]


def test_backfill_loop_waits_briefly_until_connected(ig, monkeypatch):
    """Ulanmagan bo'lsa sikl qisqa kutadi — botdan ulangach tarix darhol olinadi (1 soat kutmaydi)."""
    sleeps, ran = [], []
    real = asyncio.sleep

    async def fake_sleep(sec, *a, **kw):
        sleeps.append(sec)
        if len(sleeps) == 2:          # ikkinchi aylanishda ulandi
            connect(FakeCl())
        if len(sleeps) >= 4:
            raise asyncio.CancelledError
        return await real(0)

    async def fake_all(alert=None):
        ran.append(True)
        return 1, 1

    monkeypatch.setattr(instagram.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(instagram, "backfill_all", fake_all)
    run(storage.ig_chat_touch("t1", "Ali", [], False, NEW / 1e6, 1))
    with pytest.raises(asyncio.CancelledError):
        run(instagram.backfill_loop(None))
    short, idle = instagram.BACKFILL_START_DELAY, instagram.BACKFILL_IDLE
    # 1-aylanish: ulanmagan — qisqa kutdi; 2-aylanish: ulandi va tarixni oldi; keyin uzoq kutadi
    assert sleeps[:3] == [short, short, idle] and ran == [True]


def test_backfill_loop_off_when_sync_disabled(ig, monkeypatch):
    monkeypatch.setattr(config, "IG_SYNC_LIMIT", 0)
    run(instagram.backfill_loop(None))      # darhol tugaydi, hech narsa qilmaydi
