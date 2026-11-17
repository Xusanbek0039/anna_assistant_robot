from datetime import datetime, timezone
from types import SimpleNamespace

from telethon.tl import types

import finder
from conftest import run


# ---------- so'rovni tushunish ----------
def test_looks_like_search():
    assert finder.looks_like_search("Ali menga tashlagan usernameni top")
    assert finder.looks_like_search("Vali yuborgan raqamni topib ber")
    assert finder.looks_like_search("Sardor bilan chatdan havolani qidir")
    assert not finder.looks_like_search("nonga 25 ming ishlatdim")
    assert not finder.looks_like_search("Alidan 200 ming oldim")


def test_parse_query():
    q = finder.parse_query("Ali menga tashlagan usernameni top")
    assert q.kind == "username" and q.name == "Ali" and q.only_them
    q = finder.parse_query("Vali aka yuborgan telefon raqamni topib ber iltimos")
    assert q.kind == "phone" and q.name == "Vali aka" and not q.only_them
    q = finder.parse_query("Sardor bilan chatdan havolani qidir")
    assert q.kind == "link" and q.name == "Sardor"
    q = finder.parse_query("Ali")
    assert q.kind == "all" and q.name == "Ali"
    assert finder.parse_query("Ali email").kind == "email"
    assert finder.parse_query("Ali karta").kind == "card"


def test_score_name_with_suffixes():
    assert finder.score_name("Alidan", "Ali Valiyev") > 0
    assert finder.score_name("Alining", "Ali") > 0
    assert finder.score_name("Ali", "Alisher") < finder.score_name("Ali", "Ali")
    assert finder.score_name("Ali", "Valijon") == 0
    assert finder.score_name("@ali_dev", "Kimdir", "ali_dev") >= 10


# ---------- ajratish ----------
def test_extract_text():
    text = ("Mana @john_doe va t.me/jane_smith, sayt https://example.com/page. "
            "Tel: +998 90 123 45 67, yana 901234567. Pochta: a.b@mail.uz, karta 8600 1234 5678 9012")
    got = finder.extract(text)
    kinds = {}
    for k, v in got:
        kinds.setdefault(k, []).append(v)
    assert kinds["username"] == ["@john_doe", "@jane_smith"]
    assert "https://example.com/page" in kinds["link"]
    assert kinds["phone"] == ["+998 90 123 45 67"]  # bir xil raqam takrorlanmaydi
    assert kinds["email"] == ["a.b@mail.uz"]
    assert kinds["card"] == ["8600 1234 5678 9012"]
    assert all("8600" not in p for p in kinds["phone"])


def test_extract_email_not_username_and_dates_not_phone():
    got = finder.extract("yoz: ali@gmail.com, sana 2024-05-12")
    assert ("username", "@gmail") not in got
    assert not [v for k, v in got if k == "phone"]


def test_extract_contact_and_text_url():
    media = types.MessageMediaContact(phone_number="998901112233", first_name="Ali", last_name="",
                                      vcard="", user_id=42)
    ents = [types.MessageEntityTextUrl(offset=0, length=4, url="https://t.me/secret_user")]
    got = finder.extract("bosh", ents, media)
    assert ("phone", "+998 90 111 22 33 (Ali)") in got
    assert ("username", "@secret_user") in got
    assert any(k == "username" and v.startswith("id:42") for k, v in got)


# ---------- Telegram bilan ----------
class FakeClient:
    def __init__(self, dialogs, messages):
        self.dialogs, self.messages = dialogs, messages

    async def iter_dialogs(self, limit=None):
        for d in self.dialogs:
            yield d

    async def iter_messages(self, chat_id, limit=None, wait_time=None):
        self.wait_time, self.limit = wait_time, limit
        for m in self.messages.get(chat_id, [])[:limit]:
            yield m


def _dlg(id_, name, username=None, user=True):
    return SimpleNamespace(id=id_, name=name, is_user=user, entity=SimpleNamespace(username=username))


def _msg(text, out=False):
    return SimpleNamespace(message=text, out=out, entities=None, media=None,
                           date=datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_find_chats_and_search():
    client = FakeClient(
        [_dlg(1, "Ali Valiyev"), _dlg(2, "Sardor"), _dlg(-100, "Ali fanlari", user=False)],
        {1: [_msg("mana @new_user"), _msg("@my_own", out=True), _msg("salom"), _msg("eski @new_user")]})
    cands = run(finder.find_chats(client, "Alidan"))
    assert [c.id for c in cands][:1] == [1]  # shaxsiy chat guruhdan ustun

    all_hits, scanned = run(finder.search_chat(client, 1, "username"))
    assert client.limit is None and client.wait_time == 0  # butun tarix, keraksiz kutishsiz
    assert scanned == 4 and [h.value for h in all_hits] == ["@new_user", "@my_own"]
    hits, only = finder.only_from_them(all_hits, True)
    assert [h.value for h in hits] == ["@new_user"] and only
    # u yuborganlari orasida yo'q bo'lsa — hammasi (balki o'zingiz yuborgansiz)
    mine = [h for h in all_hits if h.mine]
    assert finder.only_from_them(mine, True) == (mine, False)
    hits = all_hits

    text = finder.format_hits("Ali Valiyev", hits, "username", 4, False)
    assert "@new_user" in text and "Ali Valiyev" in text
    assert "topilmadi" in finder.format_hits("Ali", [], "phone", 10, True)


# ---------- aqlli qidiruv ----------
import json  # noqa: E402

import control_bot  # noqa: E402


def test_looks_like_search_anything():
    assert finder.looks_like_search("Ali menga manzil tashlagandi topib ber")
    assert finder.looks_like_search("wifi parolini qidir")
    assert finder.looks_like_search("Sardor kecha tashlagan pdf faylni top")
    assert finder.looks_like_search("Ali qachon uchrashamiz degandi, top")
    assert not finder.looks_like_search("Alidan 200 ming topib oldim")  # bu — buxgalterga
    assert finder.parse_query("Sardor tashlagan rasmni top").media == "photo"
    assert finder.parse_query("Ali lokatsiya yuborgandi top").media == "location"


def test_stem_and_local_keywords():
    assert finder.stem("manzilni") == "manzil"
    assert finder.stem("kitoblarni") == "kitob"
    q = finder.parse_query("Ali yuborgan manzilni top")
    assert finder.keywords_for(q, "Ali Valiyev") == ["manzil"]  # ism kalit so'zga kirmaydi


class SearchClient:
    """iter_messages: search= bo'yicha matndan, filter= bo'yicha media turidan, aks holda oxirgilar."""

    def __init__(self, msgs):
        self.msgs, self.calls = msgs, []

    async def iter_messages(self, chat_id, limit=None, search=None, filter=None):
        self.calls.append((chat_id, search, type(filter).__name__ if filter else None))
        for m in self.msgs:
            if search and search.lower() not in (m.message or "").lower():
                continue
            if filter is not None and getattr(m, "photo", None) is None:
                continue
            yield m


def _m(id_, text, out=False, day=1, **kw):
    base = dict(id=id_, message=text, out=out, chat_id=5, entities=None, media=None,
                date=datetime(2026, 11, day, tzinfo=timezone.utc))
    base.update(kw)
    return SimpleNamespace(**base)


def test_collect_keywords_media_recent_and_days():
    msgs = [_m(4, "rasm", photo=object(), day=10), _m(3, "uchrashuv manzili: Chilonzor 5", day=9),
            _m(2, "salom", day=5), _m(1, "eski manzil", day=1, out=True)]
    c = SearchClient(msgs)
    q = finder.Query(name="Ali", media="photo", days=3, only_them=True)
    got = run(finder.collect(c, 5, q, ["manzil"], now=datetime(2026, 11, 11, tzinfo=timezone.utc)))
    assert [f.id for f in got] == [4, 3]  # 1 — eski va o'zi yuborgan
    assert got[0].media == "📷 rasm"
    assert ("InputMessagesFilterPhotos" in [x[2] for x in c.calls])

    # hech narsa topilmasa — oxirgi xabarlar (AI tanlashi uchun)
    got = run(finder.collect(c, 5, finder.Query(name="Ali"), ["yoq_narsa"]))
    assert [f.id for f in got] == [4, 3, 2, 1]


def test_media_label_location():
    geo = SimpleNamespace(lat=41.3, long=69.2)
    m = SimpleNamespace(geo=geo, venue=None)
    assert "maps.google.com/?q=41.3,69.2" in finder.media_label(m)


def _fake_ai(monkeypatch, *payloads):
    import ai
    queue = list(payloads)

    class Completions:
        async def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(queue.pop(0))))],
                                   usage=SimpleNamespace(total_tokens=50))

    monkeypatch.setattr(ai, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_ai_parse_and_pick(monkeypatch):
    _fake_ai(monkeypatch,
             {"contact": "Ali", "kind": "other", "media": "location", "keywords": ["manzil", "адрес"],
              "only_them": True, "days": 7, "what": "uchrashuv manzili"},
             {"answer": "Chilonzor 5", "ids": [2, 99, "x"]})
    q, tokens = run(finder.ai_parse("Ali menga o'tgan hafta uchrashuv manzilini tashlagandi"))
    assert (q.name, q.kind, q.media, q.days, q.only_them, q.ai) == ("Ali", "all", "location", 7, True, True)
    assert finder.keywords_for(q, "Ali") == ["manzil", "адрес"] and tokens == 50
    items = [finder.Found(5, 1, None, False, "salom"), finder.Found(5, 2, None, False, "Chilonzor 5")]
    answer, picked, _ = run(finder.ai_pick(q.text, items))
    assert answer == "Chilonzor 5" and [f.id for f in picked] == [2]
    text = finder.format_found("Ali", q, answer, picked, 2)
    assert "Chilonzor 5" in text and "uchrashuv manzili" in text


def test_local_pick():
    items = [finder.Found(5, 1, None, False, "salom"), finder.Found(5, 2, None, False, "wifi parol: 1234")]
    assert [f.id for f in finder.local_pick(items, ["parol"])] == [2]


# ---------- bot oqimi ----------
class _Status:
    def __init__(self, ev):
        self.ev = ev

    async def edit(self, text, **kw):
        self.ev.edits.append((text, kw))


class _Ev:
    def __init__(self):
        self.sent, self.edits = [], []

    async def respond(self, text, **kw):
        self.sent.append((text, kw))
        return _Status(self)


def test_run_find_without_ai(fake_storage, monkeypatch):
    msgs = [_m(2, "wifi paroli: qwerty123"), _m(1, "salom")]
    client = SearchClient(msgs)
    client.iter_dialogs = FakeClient([_dlg(5, "Ali Valiyev")], {}).iter_dialogs
    monkeypatch.setattr(control_bot.ctx, "user", client, raising=False)

    async def no_ai():
        return False
    monkeypatch.setattr(control_bot, "_ai_ok", no_ai)
    ev = _Ev()
    run(control_bot._run_find(ev, "Ali yuborgan wifi parolini top"))
    text, kw = ev.edits[-1]
    assert "qwerty123" in text and "Ali Valiyev" in text
    btn = kw["buttons"][0][0]
    data = getattr(btn, "data", None) or btn.type.data
    assert data.startswith(b"fsave:") and len(data) <= 64
    assert control_bot._find_saved[data.decode().split(":")[1]] == [(5, 2)]


def test_run_find_global_when_no_name(fake_storage, monkeypatch):
    client = SearchClient([_m(7, "wifi parol 555")])
    monkeypatch.setattr(control_bot.ctx, "user", client, raising=False)
    _fake_ai(monkeypatch,
             {"contact": "", "kind": "other", "media": "none", "keywords": ["wifi"], "what": "wifi paroli"},
             {"answer": "555", "ids": [1]})
    ev = _Ev()
    run(control_bot._run_find(ev, "wifi parolini qidir"))
    text, _ = ev.edits[-1]
    assert "barcha chatlardan" in text and "555" in text
    assert client.calls[0][0] is None  # global qidiruv
    assert fake_storage.d_stats["ai_find"] == 2


def test_search_chat_reports_progress(monkeypatch):
    monkeypatch.setattr(finder, "PROGRESS_SECONDS", 0)
    client = FakeClient([], {1: [_msg(f"xabar {i}") for i in range(5)]})
    seen = []

    async def progress(n):
        seen.append(n)
    _, scanned = run(finder.search_chat(client, 1, "phone", progress=progress))
    assert scanned == 5 and seen == [1, 2, 3, 4, 5]
