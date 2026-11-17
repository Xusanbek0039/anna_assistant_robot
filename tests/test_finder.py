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

    async def iter_messages(self, chat_id, limit=None):
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

    hits, scanned = run(finder.search_chat(client, 1, "username", only_them=True))
    assert scanned == 4 and [h.value for h in hits] == ["@new_user"]
    hits, _ = run(finder.search_chat(client, 1, "username"))
    assert [h.value for h in hits] == ["@new_user", "@my_own"]

    text = finder.format_hits("Ali Valiyev", hits, "username", 4, False)
    assert "@new_user" in text and "Ali Valiyev" in text
    assert "topilmadi" in finder.format_hits("Ali", [], "phone", 10, True)
