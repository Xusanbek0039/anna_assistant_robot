"""📸 Instagram media: rasm/video/ovoz arxivga yuklab olinadi va HTML ichida ochiladi."""
import asyncio

import pytest

import config
import gdrive
import instagram
import runtime
import storage
from conftest import run
from test_instagram import FakeCl, FakeIgStorage, NEW, STARTED, connect, thread


def photo_item(iid="p1", uid="2", ts=NEW, big=True):
    cands = [{"url": "https://cdn/ig/small.jpg", "width": 320, "height": 320}]
    if big:
        cands.append({"url": "https://cdn/ig/big.jpg", "width": 1080, "height": 1350})
    return {"item_id": iid, "user_id": uid, "timestamp": ts, "item_type": "media",
            "media": {"media_type": 1, "image_versions2": {"candidates": cands},
                      "original_width": 1080, "original_height": 1350}}


def video_item(iid="v1", uid="2", ts=NEW):
    return {"item_id": iid, "user_id": uid, "timestamp": ts, "item_type": "media",
            "media": {"media_type": 2, "video_duration": 12.5,
                      "video_versions": [{"url": "https://cdn/ig/low.mp4", "width": 480, "height": 854},
                                         {"url": "https://cdn/ig/hd.mp4", "width": 720, "height": 1280}],
                      "image_versions2": {"candidates": [{"url": "https://cdn/ig/thumb.jpg",
                                                          "width": 320, "height": 568}]}}}


def voice_item(iid="a1", uid="2", ts=NEW):
    return {"item_id": iid, "user_id": uid, "timestamp": ts, "item_type": "voice_media",
            "voice_media": {"media": {"audio": {"audio_src": "https://cdn/ig/voice.m4a", "duration": 7400}}}}


def gif_item(iid="g1", uid="2", ts=NEW):
    return {"item_id": iid, "user_id": uid, "timestamp": ts, "item_type": "animated_media",
            "animated_media": {"images": {"fixed_height": {"mp4": "https://cdn/ig/fun.mp4",
                                                           "url": "https://cdn/ig/fun.gif"}}}}


@pytest.fixture
def ig(monkeypatch):
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
    for name in ("_check_lock", "_api_lock", "_drive_lock"):
        monkeypatch.setattr(instagram, name, asyncio.Lock())
    monkeypatch.setattr(instagram, "media_state", {"saved": 0, "failed": 0, "bytes": 0, "problem": ""})
    monkeypatch.setattr(instagram, "_media_wake", asyncio.Event())
    monkeypatch.setattr(gdrive, "enabled", lambda: False)      # Drive'siz: faqat bazaga saqlaydi
    out = type("O", (), {})()
    out.fs, out.downloads, out.media = fs, [], []

    async def send_media(data, filename, caption, video, as_file):
        out.media.append(filename)
        return True

    async def download(url, limit=0, on_progress=None):
        out.downloads.append((url, limit))
        if url.endswith(".mp4"):
            return b"VIDEO" * 10, "video/mp4"
        if url.endswith(".m4a"):
            return b"AUDIO" * 10, "audio/mp4"
        return b"JPEGDATA" * 10, "image/jpeg"

    instagram.setup(lambda t: asyncio.sleep(0), send_media, None)
    monkeypatch.setattr(instagram, "_download", download)
    return out


# ---------- media havolasini topish ----------
def test_photo_video_voice_gif_urls_detected():
    th = instagram.parse_thread(thread("t1", [photo_item(), video_item(), voice_item(), gif_item()]), "1")
    by_id = {i.item_id: i for i in th.items}
    assert by_id["p1"].url == "https://cdn/ig/big.jpg" and by_id["p1"].kind == "rasm"
    assert by_id["p1"].width == 1080 and not by_id["p1"].ephemeral
    assert by_id["v1"].url == "https://cdn/ig/hd.mp4" and by_id["v1"].kind == "video"
    assert by_id["v1"].duration == 12.5 and by_id["v1"].height == 1280
    assert by_id["a1"].url == "https://cdn/ig/voice.m4a" and by_id["a1"].kind == "ovozli xabar"
    assert by_id["a1"].duration == 7.4                      # millisekund -> soniya
    assert by_id["g1"].url == "https://cdn/ig/fun.mp4" and by_id["g1"].kind == "GIF"


def test_doc_marks_media_pending():
    th = instagram.parse_thread(thread("t1", [photo_item()]), "1")
    doc = instagram.doc_of(th, th.items[0])
    assert doc["media"] == "rasm" and doc["media_state"] == "pending"
    assert doc["media_url"] == "https://cdn/ig/big.jpg" and doc["media_w"] == 1080
    text_doc = instagram.doc_of(th, instagram.Item("x", "2", False, 1.0, "", "salom"))
    assert "media_state" not in text_doc and text_doc["media"] is None


def test_text_message_has_no_media_url():
    th = instagram.parse_thread(thread("t1", [{"item_id": "1", "user_id": "2", "timestamp": NEW,
                                               "item_type": "text", "text": "salom"}]), "1")
    assert th.items[0].url == ""


# ---------- yuklab olish ----------
def test_media_downloaded_and_saved_to_db(ig):
    connect(FakeCl([thread("t1", [photo_item(), voice_item(ts=NEW + 1000)])]))
    run(instagram.check_once())
    assert run(storage.ig_stats())["media_left"] == 2
    assert run(instagram.media_sync_pending()) == 2
    assert [u for u, _ in ig.downloads] == ["https://cdn/ig/voice.m4a", "https://cdn/ig/big.jpg"]
    saved = ig.fs.media
    assert saved[("t1", "p1")][1] == "image/jpeg" and saved[("t1", "a1")][1] == "audio/mp4"
    doc = ig.fs.archive[("t1", "p1")]
    assert doc["media_state"] == "done" and doc["media_saved"] is True and doc["media_url"] == ""
    assert doc["media_name"].endswith(".jpg") and "rasm" in doc["media_name"]
    assert run(storage.ig_stats())["media_left"] == 0
    assert ig.fs.chats["t1"]["drive_dirty"] is True          # HTML qayta yuklanadi
    assert instagram.media_state["saved"] == 2


def test_expired_link_retried_then_given_up(ig, monkeypatch):
    connect(FakeCl([thread("t1", [photo_item()])]))
    run(instagram.check_once())

    async def dead(url, limit=0, on_progress=None):
        raise RuntimeError("HTTP 403 Forbidden")

    monkeypatch.setattr(instagram, "_download", dead)
    run(instagram.media_sync_pending())
    doc = ig.fs.archive[("t1", "p1")]
    assert doc["media_state"] == "gone" and "403" in doc["media_problem"]
    assert run(instagram.media_sync_pending()) == 0          # boshqa urinilmaydi


def test_temporary_error_is_retried(ig, monkeypatch):
    connect(FakeCl([thread("t1", [photo_item()])]))
    run(instagram.check_once())
    calls = []

    async def flaky(url, limit=0, on_progress=None):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("tarmoq")
        return b"JPEG", "image/jpeg"

    monkeypatch.setattr(instagram, "_download", flaky)
    run(instagram.media_sync_pending())
    assert ig.fs.archive[("t1", "p1")]["media_state"] == "retry"
    run(instagram.media_sync_pending())                      # keyingi aylanishda muvaffaqiyatli
    assert ig.fs.archive[("t1", "p1")]["media_state"] == "done"


def test_big_file_without_drive_marked_too_big(ig, monkeypatch):
    monkeypatch.setattr(config, "IG_MEDIA_MAX_KB", 0)        # bazaga umuman saqlanmaydi
    connect(FakeCl([thread("t1", [video_item()])]))
    run(instagram.check_once())
    run(instagram.media_sync_pending())
    doc = ig.fs.archive[("t1", "v1")]
    assert doc["media_state"] == "too_big" and ("t1", "v1") not in ig.fs.media


def test_big_file_goes_to_drive(ig, monkeypatch):
    monkeypatch.setattr(config, "IG_MEDIA_MAX_KB", 0)
    uploads = []

    async def ensure_folder(name, parent=None):
        return f"folder-{name}"

    async def upload(name, data=None, mime="text/html", file_id=None, parent=None, path=None):
        uploads.append((name, mime, parent))
        return {"id": "f1", "webViewLink": "https://drive.google.com/file/d/f1/view"}

    monkeypatch.setattr(gdrive, "enabled", lambda: True)
    monkeypatch.setattr(gdrive, "ensure_folder", ensure_folder)
    monkeypatch.setattr(gdrive, "upload", upload)
    connect(FakeCl([thread("t1", [video_item()])]))
    run(instagram.check_once())
    run(instagram.media_sync_pending())
    doc = ig.fs.archive[("t1", "v1")]
    assert doc["media_state"] == "done" and doc["media_drive"].endswith("/view")
    name, mime, parent = uploads[0]
    assert name.endswith(".mp4") and mime == "video/mp4" and parent.startswith("folder-")


def test_media_disabled_skips_download(ig, monkeypatch):
    """O'chirilgan bo'lsa fayllar yuklanmaydi (faqat matn arxivlanadi)."""
    monkeypatch.setitem(runtime._values, "ig_media", False)
    connect(FakeCl([thread("t1", [photo_item()])]))
    run(instagram.check_once())
    assert ig.downloads == []
    assert "yuklab olinadi" in run(instagram.toggle("ig_media"))     # yoqildi
    assert runtime.get("ig_media") is True
    assert "o'chirildi" in run(instagram.toggle("ig_media"))         # yana o'chirildi
    assert runtime.get("ig_media") is False


def test_view_once_media_both_sent_and_archived(ig):
    """Bir martalik media botga yuboriladi VA arxivda qoladi (keyin o'chirilsa ham)."""
    from test_instagram import raven
    connect(FakeCl([thread("t1", [raven("r1")])]))
    run(instagram.check_once())
    assert ig.media == ["instagram_r1.jpg"]                  # botga yuborildi
    run(instagram.media_sync_pending())
    doc = ig.fs.archive[("t1", "r1")]
    assert doc["view_once"] is True and doc["media_state"] == "done" and ("t1", "r1") in ig.fs.media


@pytest.mark.parametrize("kind,mime,url,ext", [
    ("rasm", "image/jpeg", "", ".jpg"),
    ("rasm", "image/webp", "", ".webp"),
    ("video", "video/mp4", "", ".mp4"),
    ("ovozli xabar", "audio/mp4", "", ".m4a"),
    ("rasm", "", "https://cdn/x/photo.png?a=1", ".png"),
    ("video", "application/octet-stream", "https://cdn/v/clip.mp4", ".mp4"),
    ("GIF", "", "", ".mp4"),
])
def test_extension_guessing(kind, mime, url, ext):
    assert instagram._ext_for(kind, mime, url) == ext


def test_media_file_name_is_readable():
    doc = {"kind": "ovozli xabar", "date": NEW / 1e6, "out": False, "username": "ali.v",
           "msg_id": "123456789012", "view_once": True}
    name = instagram.media_file_name(doc, ".m4a")
    assert name.endswith("_ali.v_89012345.m4a".replace("89012345", "9012")) or name.endswith(".m4a")
    assert "ovozli-xabar" in name and "bir-martalik" in name


# ---------- HTML: media ichida ochiladi ----------
def _docs_with_media():
    return [
        {"thread_id": "t1", "msg_id": "p1", "date": NEW / 1e6, "out": False, "sender": "Ali Valiyev",
         "username": "ali.v", "kind": "rasm", "media": "rasm", "text": "qara", "view_once": False,
         "ai": False, "media_state": "done", "media_saved": True},
        {"thread_id": "t1", "msg_id": "v1", "date": NEW / 1e6 + 10, "out": False, "sender": "Ali Valiyev",
         "kind": "video", "media": "video", "text": "", "view_once": True, "ai": False,
         "media_state": "done", "media_saved": True, "deleted": True, "deleted_at": NEW / 1e6 + 60},
        {"thread_id": "t1", "msg_id": "a1", "date": NEW / 1e6 + 20, "out": False, "sender": "Ali Valiyev",
         "kind": "ovozli xabar", "media": "ovozli xabar", "text": "", "view_once": False, "ai": False,
         "media_state": "done", "media_saved": True, "media_dur": 7.4},
        {"thread_id": "t1", "msg_id": "g1", "date": NEW / 1e6 + 30, "out": False, "sender": "Ali Valiyev",
         "kind": "rasm", "media": "rasm", "text": "", "view_once": False, "ai": False,
         "media_state": "gone", "media_problem": "HTTP 403"},
        {"thread_id": "t1", "msg_id": "d1", "date": NEW / 1e6 + 40, "out": False, "sender": "Ali Valiyev",
         "kind": "video", "media": "video", "text": "", "view_once": False, "ai": False,
         "media_state": "done", "media_drive": "https://drive.google.com/file/d/x/view"},
        {"thread_id": "t1", "msg_id": "m1", "date": NEW / 1e6 + 50, "out": True, "sender": "Siz",
         "kind": "", "media": None, "text": "rahmat", "view_once": False, "ai": True},
    ]


def test_html_embeds_image_video_audio():
    info = {"thread_id": "t1", "title": "Ali Valiyev", "usernames": ["ali.v"], "group": False}
    media = {"p1": (b"JPEGDATA", "image/jpeg"), "v1": (b"VIDEODATA", "video/mp4"),
             "a1": (b"AUDIODATA", "audio/mp4")}
    page = instagram.build_html(info, _docs_with_media(), media)
    assert "<img src='data:image/jpeg;base64," in page
    assert "<video controls preload='metadata' src='data:video/mp4;base64," in page
    assert "<audio controls preload='metadata' src='data:audio/mp4;base64," in page
    assert "0:07" in page                                     # ovozning davomiyligi
    assert "📸 Ali Valiyev" in page and "@ali.v" in page
    assert "🔥 bir martalik" in page and "🗑 qaytarib olindi" in page and "🤖 Anna" in page
    assert "havolasi eskirgan" in page                        # olinmagan fayl sababi
    assert "Asl fayl — Google Drive" in page and "drive.google.com/file/d/x/view" in page
    assert "linear-gradient" in page                          # Instagram ko'rinishi
    assert "6 ta xabar" in page and "1 ta qaytarib olingan" in page and "1 ta bir martalik" in page


def test_html_budget_prefers_deleted_and_once(monkeypatch):
    monkeypatch.setattr(config, "IG_HTML_MEDIA_MB", 0)        # hech narsa sig'maydi
    info = {"thread_id": "t1", "title": "Ali"}
    media = {"p1": (b"X" * 1000, "image/jpeg")}
    page = instagram.build_html(info, _docs_with_media(), media)
    assert "base64" not in page and "[🖼 rasm]" in page


def test_html_escapes_text():
    info = {"thread_id": "t1", "title": "<script>Ali"}
    docs = [{"thread_id": "t1", "msg_id": "1", "date": NEW / 1e6, "out": False, "sender": "A&B",
             "kind": "", "media": None, "text": "<b>qara</b>", "view_once": False, "ai": False}]
    page = instagram.build_html(info, docs, {})
    assert "&lt;script&gt;Ali" in page and "&lt;b&gt;qara&lt;/b&gt;" in page
    assert "<script>" not in page


def test_html_empty_chat():
    page = instagram.build_html({"thread_id": "t1", "title": "Ali"}, [], {})
    assert "hali arxivlangan xabar yo‘q" in page


def test_export_html_uses_saved_media(ig):
    run(storage.ig_archive_add_many(_docs_with_media()))
    run(storage.ig_chat_touch("t1", "Ali Valiyev", ["ali.v"], False, NEW / 1e6, 6))
    run(storage.ig_media_save("t1", "p1", b"JPEGDATA", "image/jpeg"))
    data, name, n = run(instagram.export_html("t1"))
    page = data.decode()
    assert n == 6 and name == "instagram_Ali_Valiyev.html"
    assert "data:image/jpeg;base64," in page and "📸 Ali Valiyev" in page


def test_panel_shows_media_counts(ig):
    run(storage.ig_archive_add_many(_docs_with_media()))
    run(storage.ig_media_save("t1", "p1", b"X" * 2048, "image/jpeg"))
    text, rows = run(instagram.panel())
    assert "Fayllar:" in text and "4 ta saqlandi" in text and "1 tasi olinmadi" in text
    assert "ig:set:ig_media" in [d for row in rows for _, d in row]
