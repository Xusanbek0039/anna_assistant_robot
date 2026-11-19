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
            f = self._pending.pop("u1")
            f["data"] = req.content
            self.files[f["id"]] = f
            return httpx.Response(200, json={"id": f["id"], "webViewLink": f"https://drive/{f['id']}"})
        return httpx.Response(500)


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
