"""📥 Instagram havolasidan yuklash (post, reels, storis) va «tushunmadim» javobini yaxshilash."""
import asyncio

import pytest

import config
import control_bot
import gdrive
import instagram
import runtime
import storage
import watchers
from conftest import run
from test_instagram import FakeCl, FakeIgStorage, STARTED, connect


def photo_block(url="https://cdn/ig/one.jpg"):
    return {"media_type": 1, "image_versions2": {"candidates": [{"url": url, "width": 1080, "height": 1080}]},
            "original_width": 1080, "original_height": 1080}


def video_block(url="https://cdn/ig/clip.mp4"):
    return {"media_type": 2, "video_duration": 9.5,
            "video_versions": [{"url": url, "width": 720, "height": 1280}],
            "image_versions2": {"candidates": [{"url": "https://cdn/ig/t.jpg", "width": 320, "height": 568}]}}


def post(blocks=None, caption="Salom <dunyo>", owner="ali.v"):
    item = {"user": {"username": owner}, "caption": {"text": caption}}
    if blocks is None:
        item.update(photo_block())
    elif len(blocks) == 1:
        item.update(blocks[0])
    else:
        item.update({"media_type": 8, "carousel_media": blocks})
    return item


class LinkCl(FakeCl):
    """media_pk_from_code va media/<pk>/info/ ni soxta qiladigan klient."""

    def __init__(self, item=None, error=None):
        super().__init__(error=error)
        self.item = item if item is not None else post()
        self.infos: list[str] = []

    def media_pk_from_code(self, code):
        return f"pk-{code}"

    def private_request(self, endpoint, data=None, params=None, **kw):
        self.calls.append(endpoint)
        if self.error:
            raise self.error
        if endpoint.startswith("media/") and endpoint.endswith("/info/"):
            self.infos.append(endpoint.split("/")[1])
            return {"items": [self.item]}
        return super().private_request(endpoint, data, params, **kw)


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
    monkeypatch.setattr(instagram, "_api_lock", asyncio.Lock())
    monkeypatch.setattr(gdrive, "enabled", lambda: False)
    out = type("O", (), {})()
    out.sent, out.downloads, out.alerts = [], [], []

    async def send_media(data, filename, caption, video, as_file):
        out.sent.append({"data": data, "filename": filename, "caption": caption,
                         "video": video, "as_file": as_file})
        return True

    async def download(url, limit=0, on_progress=None):
        out.downloads.append((url, limit))
        if url.endswith(".mp4"):
            return b"VIDEO", "video/mp4"
        return b"IMAGE", "image/jpeg"

    async def alert(text):
        out.alerts.append(text)

    instagram.setup(alert, send_media, None)
    monkeypatch.setattr(instagram, "_download", download)
    return out


# ---------- havolani tanish ----------
@pytest.mark.parametrize("text,kind,code", [
    ("https://www.instagram.com/p/CxYz_123-ab/", "media", "CxYz_123-ab"),
    ("https://instagram.com/reel/AbC123/?igsh=xx", "media", "AbC123"),
    ("instagram.com/reels/AbC123", "media", "AbC123"),
    ("https://www.instagram.com/tv/XyZ987/", "media", "XyZ987"),
    ("https://www.instagram.com/ali.v/p/CxYz123/", "media", "CxYz123"),
    ("https://www.instagram.com/share/BAbCdEf123/", "media", "BAbCdEf123"),
])
def test_media_links_recognised(text, kind, code):
    links = instagram.parse_media_links(text)
    assert len(links) == 1 and links[0].kind == kind and links[0].code == code
    assert instagram.has_media_link(text)


def test_story_link_recognised():
    links = instagram.parse_media_links("qara https://www.instagram.com/stories/ali.v/3245678901234567890/")
    assert len(links) == 1 and links[0].kind == "story"
    assert links[0].pk == "3245678901234567890" and links[0].user == "ali.v"


def test_non_instagram_text_ignored():
    for text in ("salom", "https://t.me/kanal/5", "https://youtube.com/watch?v=x",
                 "instagram.com/ali.v", "@ali online bo'lsa xabar ber"):
        assert instagram.parse_media_links(text) == [] and not instagram.has_media_link(text)


def test_duplicate_and_many_links():
    same = "instagram.com/p/AAA111/ va yana instagram.com/p/AAA111/"
    assert len(instagram.parse_media_links(same)) == 1
    many = " ".join(f"instagram.com/p/code{i:03d}/" for i in range(9))
    assert len(instagram.parse_media_links(many)) == instagram.MAX_LINKS


# ---------- yuklash ----------
def test_single_photo_downloaded_and_sent(ig):
    cl = LinkCl(post())
    connect(cl)
    link = instagram.parse_media_links("https://www.instagram.com/p/AbC123/")[0]
    assert run(instagram.download_link(link)) == "✅ Yuborildi"
    assert cl.infos == ["pk-AbC123"]
    assert [u for u, _ in ig.downloads] == ["https://cdn/ig/one.jpg"]
    sent = ig.sent[0]
    assert sent["filename"] == "instagram_pk-AbC123_1.jpg" and sent["data"] == b"IMAGE"
    assert sent["video"] is None and not sent["as_file"]
    assert "@ali.v" in sent["caption"] and "Salom &lt;dunyo&gt;" in sent["caption"]
    assert "yuklandi" in sent["caption"]


def test_reels_video_keeps_duration(ig):
    connect(LinkCl(post([video_block()])))
    link = instagram.parse_media_links("instagram.com/reel/XX1abcdefgh/")[0]
    run(instagram.download_link(link))
    sent = ig.sent[0]
    assert sent["filename"].endswith(".mp4") and sent["video"] == {"duration": 9.5, "w": 720, "h": 1280}


class FakeBar:
    """control_bot.Progress o'rniga: set/render chaqiruvlarini yozib boradi."""

    def __init__(self):
        self.states: list[tuple[str, float | None]] = []

    def set(self, text=None, fraction=None, done=0, total=0):
        self.states.append((text, fraction))

    async def render(self):
        pass


def test_album_sends_every_file_with_counter(ig):
    connect(LinkCl(post([photo_block("https://cdn/ig/a.jpg"), video_block("https://cdn/ig/b.mp4"),
                         photo_block("https://cdn/ig/c.jpg")])))
    bar = FakeBar()
    link = instagram.parse_media_links("instagram.com/p/AL1abcdefgh/")[0]
    assert run(instagram.download_link(link, bar)) == "✅ Yuborildi (3 ta fayl)"
    texts = [t for t, _ in bar.states if t]
    assert any("1/3" in t and "yuklanmoqda" in t for t in texts)
    assert any("3/3" in t for t in texts) and any("Telegram'ga yuborilmoqda" in t for t in texts)
    assert any("🎬" in t for t in texts) and any("🖼" in t for t in texts)   # tur belgisi
    assert [s["filename"] for s in ig.sent] == ["instagram_pk-AL1abcdefgh_1.jpg", "instagram_pk-AL1abcdefgh_2.mp4",
                                                "instagram_pk-AL1abcdefgh_3.jpg"]
    assert "1/3" in ig.sent[0]["caption"] and "3/3" in ig.sent[2]["caption"]
    assert texts[0].endswith("olinmoqda...")      # avval ma'lumot, keyin fayllar


def test_album_limited(ig):
    connect(LinkCl(post([photo_block(f"https://cdn/ig/{i}.jpg") for i in range(15)])))
    link = instagram.parse_media_links("instagram.com/p/AL2abcdefgh/")[0]
    run(instagram.download_link(link))
    assert len(ig.sent) == instagram.ALBUM_MAX


def test_story_uses_pk_directly(ig):
    cl = LinkCl(post([video_block()], owner=""))
    connect(cl)
    link = instagram.parse_media_links("https://www.instagram.com/stories/ali.v/3245678901234567890/")[0]
    run(instagram.download_link(link))
    assert cl.infos == ["3245678901234567890"]               # kod emas, to'g'ridan-to'g'ri id
    assert "storis" in ig.sent[0]["caption"] and "@ali.v" in ig.sent[0]["caption"]


def test_no_media_in_link(ig):
    connect(LinkCl({"user": {"username": "ali"}, "caption": {"text": "faqat matn"}}))
    link = instagram.parse_media_links("instagram.com/p/NM1abcdefgh/")[0]
    result = run(instagram.download_link(link))
    assert "media topilmadi" in result and "yopiq akkaunt" in result and ig.sent == []


def test_not_connected(ig):
    instagram.st.client = None
    link = instagram.parse_media_links("instagram.com/p/X1abcdefghi/")[0]
    assert "ulanmagan" in run(instagram.download_link(link))


def test_private_or_deleted_post_explained(ig):
    connect(LinkCl(error=type("MediaNotFound", (Exception,), {})("not found")))
    link = instagram.parse_media_links("instagram.com/p/X2abcdefghi/")[0]
    result = run(instagram.download_link(link))
    assert result.startswith("❌") and ig.sent == []


def test_session_error_disconnects(ig):
    connect(LinkCl(error=type("LoginRequired", (Exception,), {})()))
    link = instagram.parse_media_links("instagram.com/p/X3abcdefghi/")[0]
    run(instagram.download_link(link))
    assert instagram.st.client is None and ig.alerts     # egasi ogohlantirildi


def test_download_failure_reported(ig, monkeypatch):
    connect(LinkCl(post()))

    async def dead(url, limit=0, on_progress=None):
        raise RuntimeError("juda katta")

    monkeypatch.setattr(instagram, "_download", dead)
    link = instagram.parse_media_links("instagram.com/p/X4abcdefghi/")[0]
    assert "Yuklab bo'lmadi" in run(instagram.download_link(link))


def test_multiple_links_numbered(ig):
    connect(LinkCl(post()))
    links = instagram.parse_media_links("instagram.com/p/A1abcdefghi/ instagram.com/p/A2abcdefghi/")
    result = run(instagram.download_links(links))
    assert result.count("✅") == 2 and result.startswith("1/2 ") and "2/2 " in result


# ---------- «tushunmadim» javobini yaxshilash ----------
class Ev:
    def __init__(self, text, eid=7):
        self.raw_text = self.text = text
        self.id = eid
        self.sent = []

    async def respond(self, text, **kw):
        self.sent.append((text, kw))
        return self


def test_plain_number_offers_reminder(monkeypatch):
    monkeypatch.setattr(control_bot, "_watch_pending", {})
    ev = Ev("+998 99 250 20 11")
    run(control_bot._dont_understand(ev))
    text, kw = ev.sent[0]
    assert "eslatma deb tushundim" in text and "Telegram'da paydo bo'lsa" in text
    datas = [getattr(b, "data", None) or b.type.data for row in kw["buttons"] for b in row]
    assert b"wch:ok:7" in datas and b"wch:no" in datas
    assert control_bot._watch_pending["7"] == "+998 99 250 20 11"


def test_plain_username_offers_reminder(monkeypatch):
    monkeypatch.setattr(control_bot, "_watch_pending", {})
    ev = Ev("@alijon")
    run(control_bot._dont_understand(ev))
    assert "eslatma deb tushundim" in ev.sent[0][0] and "online bo'lsa" in ev.sent[0][0]


def test_plain_other_text_lists_abilities(monkeypatch):
    monkeypatch.setattr(control_bot, "_watch_pending", {})
    ev = Ev("qanday ketyapti")
    run(control_bot._dont_understand(ev))
    text = ev.sent[0][0]
    assert "Tushunmadim" in text
    for word in ("Eslatma", "Instagram'dan yuklash", "Qidirish", "Buxgalter", "Javob yozish"):
        assert word in text
    assert control_bot._watch_pending == {}


def test_describe_request_texts():
    phone = watchers.Request("phone", phone="998954466886", number="954466886")
    assert "+998 95 446 68 86" in watchers.describe_request(phone)
    once = watchers.Request("online", user_ref="@ali")
    assert "bir marta" in watchers.describe_request(once) and "@ali" in watchers.describe_request(once)
    every = watchers.Request("online", repeat=True, user_ref="@ali")
    assert "har safar" in watchers.describe_request(every)


def test_help_and_commands_mention_link_download():
    assert "/igyukla" in control_bot.HELP and "reels" in control_bot.HELP
    assert "igyukla" in [c for c, _ in control_bot.BOT_COMMANDS]


@pytest.mark.parametrize("text,is_ref", [
    ("+998 99 250 20 11", True),
    ("998992502011", True),
    ("@alijon", True),
    ("  @ali.v  ", False),              # nuqta — username'da bo'lmaydi
    ("nonga 25 ming ishlatdim", False),
    ("Alidan 200 ming oldim", False),
    ("95 446 68 86 raqami tg ga qo'shilsa xabar ber", False),   # to'liq gap — watchers.parse ushlaydi
    ("salom", False),
    ("25000", False),                   # 5 xonali — telefon emas
])
def test_only_ref_regex(text, is_ref):
    assert bool(control_bot.ONLY_REF_RE.match(text)) is is_ref
