"""Google Drive: token yangilash, fayl yaratish/yangilash va o'zgargan chatlarni yuklash (soxta server)."""
import json

import httpx
import pytest

import archive
import config
import gdrive
import storage
from conftest import run


class FakeGoogle:
    def __init__(self, token_error=None, folder_404=False):
        self.token_error, self.folder_404 = token_error, folder_404
        self.files: dict[str, dict] = {}
        self.folders: dict[str, dict] = {}
        self.chunks = 0
        self.drop_after_chunks: set[int] = set()
        self.status_after_chunks: dict[int, int] = {}
        self.calls: list[str] = []
        self._pending: dict[str, dict] = {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.path}")
        if req.url.host == "oauth2.googleapis.com":
            if self.token_error:
                return httpx.Response(400, json={"error": self.token_error})
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer at"
        path = req.url.path
        if path == "/drive/v3/files" and req.method == "GET":       # papkani qidirish
            q = req.url.params["q"]
            hit = [fid for fid, f in self.folders.items() if f"name = '{f['name']}'" in q and f"'{f['parent']}'" in q]
            return httpx.Response(200, json={"files": [{"id": hit[0]}] if hit else []})
        if path == "/drive/v3/files" and req.method == "POST":      # papka yaratish
            meta = json.loads(req.content)
            if self.folder_404:
                return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
            fid = f"d{len(self.folders) + 1}"
            self.folders[fid] = {"name": meta["name"], "parent": meta["parents"][0]}
            return httpx.Response(200, json={"id": fid})
        if req.method == "POST" and path == "/upload/drive/v3/files":
            meta = json.loads(req.content)
            if self.folder_404:
                return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
            fid = f"f{len(self.files) + 1}"
            self._pending["u1"] = {"id": fid, **meta}
            return httpx.Response(200, headers={"location": "https://upload.example/u1"})
        if req.method == "PATCH" and path.startswith("/upload/drive/v3/files/"):
            fid = path.rsplit("/", 1)[1]
            if fid not in self.files:
                return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
            self._pending["u1"] = {**self.files[fid], **json.loads(req.content)}
            return httpx.Response(200, headers={"location": "https://upload.example/u1"})
        if req.method == "PUT" and req.url.host == "upload.example":
            rng = req.headers.get("content-range")
            if not rng:                                    # bitta so'rovda (kichik fayl)
                return self._finish(req.content)
            spec, total = rng.split(" ", 1)[1].split("/")
            total, up = int(total), self._pending["u1"]
            buf = up.setdefault("buf", b"")
            if spec == "*":                                # "qancha oldingiz?" so'rovi
                if len(buf) >= total:
                    return self._finish(buf)
                return httpx.Response(308, headers={"range": f"bytes=0-{len(buf) - 1}"} if buf else {})
            start, end = map(int, spec.split("-"))
            assert start == len(buf) and end - start + 1 == len(req.content), "noto'g'ri bo'lak"
            self.chunks += 1
            if self.status_after_chunks.get(self.chunks):
                return httpx.Response(self.status_after_chunks[self.chunks], json={"error": {"message": "x"}})
            up["buf"] = buf = buf + req.content
            if self.drop_after_chunks and self.chunks in self.drop_after_chunks:
                raise httpx.ReadError("aloqa uzildi")   # Google oldi, lekin javob yetib kelmadi
            if len(buf) < total:
                return httpx.Response(308, headers={"range": f"bytes=0-{len(buf) - 1}"})
            return self._finish(buf)
        return httpx.Response(500)

    def _finish(self, data):
        f = self._pending.pop("u1")
        f.pop("buf", None)
        f["data"] = data
        self.files[f["id"]] = f
        return httpx.Response(200, json={"id": f["id"], "webViewLink": f"https://drive/{f['id']}"})


@pytest.fixture
def google(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setattr(gdrive, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(g.handler)))
    monkeypatch.setattr(gdrive, "_token", ("", 0.0))
    for name, val in [("GOOGLE_OAUTH_CLIENT_ID", "id"), ("GOOGLE_OAUTH_CLIENT_SECRET", "s"),
                      ("GOOGLE_OAUTH_REFRESH_TOKEN", "r"), ("GOOGLE_DRIVE_ROOT_FOLDER_ID", "root")]:
        monkeypatch.setattr(config, name, val)
    return g


def test_enabled_and_missing(google, monkeypatch):
    assert gdrive.enabled()
    monkeypatch.setattr(config, "GOOGLE_OAUTH_REFRESH_TOKEN", "")
    assert not gdrive.enabled() and gdrive.missing() == ["GOOGLE_OAUTH_REFRESH_TOKEN"]


def test_create_then_update_same_file(google):
    a = run(gdrive.upload("Ali.html", b"<html>1</html>"))
    assert a["id"] == "f1" and google.files["f1"]["parents"] == ["root"]
    b = run(gdrive.upload("Ali (@ali).html", b"<html>2</html>", file_id="f1"))
    assert b["id"] == "f1" and len(google.files) == 1
    assert google.files["f1"]["data"] == b"<html>2</html>" and google.files["f1"]["name"] == "Ali (@ali).html"
    assert sum(c.startswith("POST /token") for c in google.calls) == 1  # token keshlanadi


def test_file_deleted_in_drive_is_recreated(google):
    res = run(gdrive.upload("Ali.html", b"x", file_id="yoq"))
    assert res["id"] == "f1"


def test_bad_refresh_token_is_explained(google):
    google.token_error = "invalid_grant"
    with pytest.raises(gdrive.DriveError) as e:
        run(gdrive.upload("a.html", b"x"))
    assert "drive_auth.py" in gdrive.explain(e.value)


def test_sync_pending_uploads_changed_chats(google, monkeypatch):
    chats = {5: {"chat_id": 5, "title": "Ali", "username": "ali", "changed": 1.0, "drive_dirty": True},
             7: {"chat_id": 7, "title": "Vali", "changed": 2.0, "drive_dirty": True},
             9: {"chat_id": 9, "title": "Eski", "changed": 0.5, "drive_dirty": False}}

    async def pending(limit=50):
        return [dict(c) for c in chats.values() if c["drive_dirty"]][:limit]

    async def done(chat_id, changed, file_id, link):
        c = chats[chat_id]
        c.update(drive_file_id=file_id, drive_link=link)
        if c["changed"] == changed:
            c["drive_dirty"] = False

    async def chat(chat_id):
        return dict(chats[chat_id])

    async def messages(chat_id, only_deleted=False, limit=0):
        return [{"chat_id": chat_id, "msg_id": 1, "out": False, "text": f"salom {chat_id}", "date": 1.7e9}]

    async def media_get(ids):
        return {}

    async def chat_set(chat_id, **fields):
        chats[chat_id].update(fields)

    for name, fn in [("archive_drive_pending", pending), ("archive_drive_done", done), ("archive_chat", chat),
                     ("archive_messages", messages), ("archive_media_get", media_get),
                     ("archive_chat_set", chat_set)]:
        monkeypatch.setattr(storage, name, fn, raising=False)

    assert run(archive.drive_sync_pending()) == 2
    assert not chats[5]["drive_dirty"] and chats[5]["drive_link"].startswith("https://drive/")
    names = sorted(f["name"] for f in google.files.values())
    assert names == ["Ali (@ali) [5].html", "Vali [7].html"]
    assert b"salom 5" in next(f["data"] for f in google.files.values() if f["name"].startswith("Ali"))
    # har chatning o'z papkasi bor: root/<Ism [id]>/<Ism [id]>.html
    assert sorted(f["name"] for f in google.folders.values()) == ["Ali (@ali) [5]", "Vali [7]"]
    assert all(f["parent"] == "root" for f in google.folders.values())
    assert google.files["f1"]["parents"][0] == chats[5]["drive_folder_id"]

    chats[5].update(changed=3.0, drive_dirty=True)          # yangi xabar — o'sha fayl yangilanadi
    assert run(archive.drive_sync_pending()) == 1 and len(google.files) == 2


def test_wrong_folder_stops_sync(google, monkeypatch):
    google.folder_404 = True

    async def pending(limit=50):
        return [{"chat_id": 5, "title": "Ali", "changed": 1.0}]

    async def messages(chat_id, only_deleted=False, limit=0):
        return []

    async def none(*a, **k):
        return {}
    monkeypatch.setattr(storage, "archive_drive_pending", pending, raising=False)
    monkeypatch.setattr(storage, "archive_messages", messages)
    monkeypatch.setattr(storage, "archive_chat", lambda cid: none())
    monkeypatch.setattr(storage, "archive_media_get", none)
    monkeypatch.setattr(storage, "archive_chat_set", none, raising=False)
    with pytest.raises(gdrive.DriveError) as e:
        run(archive.drive_sync_pending())
    assert "GOOGLE_DRIVE_ROOT_FOLDER_ID" in gdrive.explain(e.value)


def test_media_uploaded_to_chat_folder(google, monkeypatch, tmp_path):
    import os
    import runtime
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS, drive_media=True))
    msgs = {(5, 11): {"chat_id": 5, "msg_id": 11, "out": False, "media": "video", "mime": "video/mp4",
                      "file_name": None, "date": 1.7e9, "drive_media": "pending"},
            (5, 12): {"chat_id": 5, "msg_id": 12, "out": True, "media": "rasm", "mime": "",
                      "file_name": None, "date": 1.7e9 + 5, "drive_media": "pending"},
            (5, 13): {"chat_id": 5, "msg_id": 13, "out": False, "media": "rasm", "mime": "",
                      "file_name": None, "date": 1.7e9 + 9, "drive_media": "pending"}}
    chat = {"chat_id": 5, "title": "Ali", "username": None}

    async def pending(limit=20):
        return [dict(d) for d in msgs.values() if d.get("drive_media") == "pending"][:limit]

    async def msg_set(cid, mid, **f):
        msgs[(cid, mid)].update(f)

    async def media_done(cid, mid, fid, link):
        msgs[(cid, mid)].update(drive_media="done", drive_media_id=fid, drive_media_link=link)

    async def get_chat(cid):
        return dict(chat)

    async def chat_set(cid, **f):
        chat.update(f)

    for name, fn in [("archive_media_pending", pending), ("archive_msg_set", msg_set),
                     ("archive_media_done", media_done), ("archive_chat", get_chat), ("archive_chat_set", chat_set)]:
        monkeypatch.setattr(storage, name, fn, raising=False)

    class File:
        def __init__(self, size, mime):
            self.size, self.mime_type = size, mime

    class TgMsg:
        def __init__(self, content, mime):
            self.media, self.file, self.content = True, File(len(content), mime), content

        async def download_media(self, file=None):
            path = os.path.join(file, "video.mp4" if self.file.mime_type == "video/mp4" else "photo.jpg")
            with open(path, "wb") as f:
                f.write(self.content)
            return path

    class User:
        async def get_messages(self, cid, ids):
            return {11: TgMsg(b"V" * 9_000_000, "video/mp4"), 12: TgMsg(b"JPEG", "image/jpeg"), 13: None}[ids]

    assert run(archive.drive_media_pending(User())) == 2
    assert msgs[(5, 13)]["drive_media"] == "lost"          # yuklashga ulgurmay o'chirilgan
    video = next(f for f in google.files.values() if f["name"].endswith("video.mp4"))
    assert len(video["data"]) == 9_000_000                  # 4 MB bo'laklab, to'liq yetib bordi
    assert video["parents"] == [chat["drive_folder_id"]] and video["mimeType"] == "video/mp4"
    assert "_u_11_" in video["name"] and any("_men_12_" in f["name"] for f in google.files.values())
    assert msgs[(5, 11)]["drive_media_link"].startswith("https://drive/")


def test_wants_drive_media(google, monkeypatch):
    import runtime
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS, drive_media=True))
    assert archive.wants_drive_media({"media": "video"})
    assert archive.wants_drive_media({"media": "fayl", "mime": "image/png"})
    assert not archive.wants_drive_media({"media": "fayl", "mime": "application/pdf"})
    assert not archive.wants_drive_media({"media": "stiker"})
    monkeypatch.setattr(config, "DRIVE_MEDIA_FILES", True)
    assert archive.wants_drive_media({"media": "fayl", "mime": "application/pdf"})
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS, drive_media=False))
    assert not archive.wants_drive_media({"media": "video"})


# ---------- katta video: bo'laklab yuklash va uzilgan joydan davom etish ----------
def _big_file(tmp_path, size):
    p = tmp_path / "video.mp4"
    p.write_bytes(bytes(i % 251 for i in range(size)))
    return str(p)


def test_big_video_uploaded_in_chunks(google, tmp_path):
    path = _big_file(tmp_path, 3 * gdrive.CHUNK + 12345)       # ~24 MB: 4 bo'lak
    res = run(gdrive.upload("v.mp4", mime="video/mp4", path=path))
    assert google.chunks == 4
    assert google.files[res["id"]]["data"] == open(path, "rb").read()


def test_upload_resumes_after_connection_drop(google, tmp_path, fast_sleep):
    path = _big_file(tmp_path, 3 * gdrive.CHUNK + 7)
    google.drop_after_chunks = {2, 3}                           # 2- va 3-bo'lakdan keyin aloqa uzildi
    res = run(gdrive.upload("v.mp4", mime="video/mp4", path=path))
    assert google.files[res["id"]]["data"] == open(path, "rb").read()   # boshidan emas — davomidan
    assert google.chunks == 4                                   # hech bir bo'lak qayta yuborilmadi


def test_upload_retries_on_google_5xx(google, tmp_path, fast_sleep):
    path = _big_file(tmp_path, 2 * gdrive.CHUNK)
    google.status_after_chunks = {1: 503}
    res = run(gdrive.upload("v.mp4", mime="video/mp4", path=path))
    assert google.files[res["id"]]["data"] == open(path, "rb").read()


def test_upload_gives_up_on_4xx(google, tmp_path, fast_sleep):
    path = _big_file(tmp_path, 2 * gdrive.CHUNK)
    google.status_after_chunks = {2: 403}
    with pytest.raises(gdrive.DriveError) as e:
        run(gdrive.upload("v.mp4", mime="video/mp4", path=path))
    assert e.value.status == 403


# ---------- o'chib ketadigan media va navbat ----------
@pytest.fixture
def media_store(monkeypatch):
    import runtime
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS, drive_media=True))
    msgs, chat = {}, {"chat_id": 5, "title": "Ali", "username": None}

    async def add(doc):
        msgs[(doc["chat_id"], doc["msg_id"])] = dict(doc)
        return True

    async def pending(limit=20):
        p = [dict(d) for d in msgs.values() if d.get("drive_media") == "pending"]
        h = sorted((dict(d) for d in msgs.values() if d.get("drive_media") == "history"), key=lambda d: -d["date"])
        return (p + h)[:limit]

    async def msg_set(cid, mid, **f):
        msgs[(cid, mid)].update(f)

    async def media_done(cid, mid, fid, link):
        msgs[(cid, mid)].update(drive_media="done", drive_media_id=fid, drive_media_link=link)

    async def get_chat(cid):
        return dict(chat)

    async def chat_set(cid, **f):
        chat.update(f)

    async def noop(*a, **k):
        return None

    for name, fn in [("archive_add", add), ("archive_media_pending", pending), ("archive_msg_set", msg_set),
                     ("archive_media_done", media_done), ("archive_chat", get_chat), ("archive_chat_set", chat_set),
                     ("archive_touch_chat", noop), ("archive_media_put", noop)]:
        monkeypatch.setattr(storage, name, fn, raising=False)
    return msgs


class _File:
    def __init__(self, size, mime):
        self.size, self.mime_type, self.name = size, mime, None


class _TgMsg:
    """Telethon xabari o'rnida (view_once=True — bir marta ko'riladigan)."""

    def __init__(self, mid, content=b"JPEGDATA", view_once=False, kind="rasm"):
        from datetime import datetime, timezone
        from telethon.tl.types import PeerUser
        self.id, self.content, self.out = mid, content, False
        self.peer_id, self.sender_id = PeerUser(5), 5
        self.date = datetime(2026, 11, 19, tzinfo=timezone.utc)
        self.message, self.fwd_from, self.reply_to_msg_id = "", None, None
        for a in ("voice", "video_note", "gif", "sticker", "audio", "document", "geo", "venue", "contact", "poll"):
            setattr(self, a, None)
        self.photo = object() if kind == "rasm" else None
        self.video = object() if kind == "video" else None
        self.media = type("Media", (), {"ttl_seconds": 10 if view_once else None})()
        self.file = _File(len(content), "image/jpeg" if kind == "rasm" else "video/mp4")

    async def download_media(self, file=None, thumb=None):
        import os
        if file is bytes:
            return self.content
        path = os.path.join(file, "photo.jpg" if self.photo else "video.mp4")
        with open(path, "wb") as f:
            f.write(self.content)
        return path


def test_view_once_saved_immediately_even_if_opened_later(google, media_store, monkeypatch, tmp_path):
    import os
    import tempfile
    from telethon.tl.types import User
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    class Ev:
        is_private, is_group, chat_id = True, False, 5

        def __init__(self, m):
            self.message = m

        async def get_chat(self):
            return User(id=5, first_name="Ali")

    run(archive._store(Ev(_TgMsg(77, b"SIRLI-RASM", view_once=True))))
    doc = media_store[(5, 77)]
    assert doc["drive_media"] == "pending" and doc["view_once"]
    assert doc["drive_local"] and os.path.isfile(doc["drive_local"])     # kelgan zahoti diskka olindi

    class Opened:   # egasi rasmni ochdi — Telegram uni o'chirdi
        async def get_messages(self, cid, ids):
            return None

    assert run(archive.drive_media_pending(Opened())) == 1
    up = next(iter(google.files.values()))
    assert up["data"] == b"SIRLI-RASM" and "_bir-martalik_" in up["name"]
    assert not os.path.exists(os.path.dirname(doc["drive_local"]))      # vaqtinchalik fayl tozalandi


def test_temp_file_removed_when_upload_fails(google, media_store, monkeypatch, tmp_path):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    media_store[(5, 1)] = {"chat_id": 5, "msg_id": 1, "media": "video", "mime": "video/mp4", "date": 1.7e9,
                           "drive_media": "pending", "out": False}
    google.status_after_chunks = {1: 400}

    class Tg:
        async def get_messages(self, cid, ids):
            return _TgMsg(1, b"V" * 1000, kind="video")

    assert run(archive.drive_media_pending(Tg())) == 0
    assert media_store[(5, 1)]["drive_attempts"] == 1 and media_store[(5, 1)]["drive_media"] == "pending"
    assert [p for p in tmp_path.iterdir() if p.name.startswith("anna_drive_")] == []   # disk to'lmaydi


def test_too_big_video_skipped_and_new_media_goes_first(google, media_store, monkeypatch):
    monkeypatch.setattr(config, "DRIVE_MEDIA_MAX_MB", 1)
    base = {"chat_id": 5, "mime": "video/mp4", "media": "video", "out": False}
    media_store[(5, 1)] = {**base, "msg_id": 1, "date": 1.0, "drive_media": "history"}
    media_store[(5, 2)] = {**base, "msg_id": 2, "date": 2.0, "drive_media": "pending"}
    media_store[(5, 3)] = {**base, "msg_id": 3, "date": 3.0, "drive_media": "history"}
    order = []

    class Tg:
        async def get_messages(self, cid, ids):
            order.append(ids)
            return _TgMsg(ids, b"x" * (2 * 1024 * 1024 if ids == 3 else 10), kind="video")

    assert run(archive.drive_media_pending(Tg())) == 2
    assert order == [2, 3, 1]                          # yangi kelgani birinchi, keyin tarix (yangisidan)
    assert media_store[(5, 3)]["drive_media"] == "too_big"


def test_history_media_queued_from_old_chats(google, monkeypatch):
    import runtime
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS, drive_media=True))
    assert archive._as_history({"media": "video"})["drive_media"] == "history"
    assert "drive_media" not in archive._as_history({"media": "stiker"})
    assert "drive_media" not in archive._as_history({"media": None})
    monkeypatch.setattr(config, "DRIVE_MEDIA_HISTORY", False)
    assert "drive_media" not in archive._as_history({"media": "video"})
