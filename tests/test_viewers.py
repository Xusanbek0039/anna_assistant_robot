"""👀 Kim qiziqdi: storis ko'ruvchilari va profildagi havola orqali botni ochganlar."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.functions.stories import GetPeerStoriesRequest, GetStoryViewsListRequest
from telethon.tl.types import (MessageMediaEmpty, ReactionEmoji, StoryItem, StoryView, StoryViewPublicForward,
                               StoryViews, User)

import runtime
import storage
import viewers
from conftest import run

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)


def story(sid, views):
    return StoryItem(id=sid, date=NOW, expire_date=NOW + timedelta(days=1), media=MessageMediaEmpty(),
                     views=StoryViews(views_count=views))


def user(uid, first="Ali", last="Valiyev", username="alijon"):
    return User(id=uid, first_name=first, last_name=last, username=username)


class FakeClient:
    def __init__(self):
        self.stories: list = []
        self.views: dict[int, list] = {}      # story id -> [(user, reaction)]
        self.calls: list = []

    async def __call__(self, req):
        self.calls.append(type(req).__name__)
        if isinstance(req, GetPeerStoriesRequest):
            return SimpleNamespace(stories=SimpleNamespace(stories=list(self.stories)))
        if isinstance(req, GetStoryViewsListRequest):
            rows = self.views.get(req.id, [])
            start = int(req.offset or 0)
            page = rows[start:start + req.limit]
            nxt = str(start + req.limit) if start + req.limit < len(rows) else None
            views = [StoryView(user_id=u.id, date=NOW, reaction=r) for u, r in page]
            views.append(StoryViewPublicForward(message=None))   # uzatishlar — ko'rish emas
            return SimpleNamespace(views=views, users=[u for u, _ in page], next_offset=nxt)
        raise AssertionError(req)


@pytest.fixture
def vw(monkeypatch):
    seen: set = set()
    ignored: dict[int, str] = {}

    async def story_views_new(story_id, rows):
        new = [r for r in rows if (story_id, r["user_id"]) not in seen]
        seen.update((story_id, r["user_id"]) for r in rows)
        return new

    async def viewer_ignored(uid):
        return uid in ignored

    async def viewer_ignore_list():
        return [{"user_id": k, "label": v} for k, v in ignored.items()]

    monkeypatch.setattr(storage, "story_views_new", story_views_new)
    monkeypatch.setattr(storage, "viewer_ignored", viewer_ignored)
    monkeypatch.setattr(storage, "viewer_ignore_list", viewer_ignore_list)
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    monkeypatch.setattr(runtime, "_listeners", [])
    monkeypatch.setattr(storage, "set_runtime", lambda **kw: asyncio.sleep(0))
    monkeypatch.setattr(viewers, "_counts", {})
    sent = []

    async def alert(text, buttons=None):
        sent.append((text, buttons))

    viewers.setup(alert)
    return SimpleNamespace(sent=sent, ignored=ignored, client=FakeClient())


def test_story_viewer_alert_has_name_surname_id_username(vw):
    c = vw.client
    c.stories = [story(7, 1)]
    c.views[7] = [(user(42), ReactionEmoji(emoticon="🔥"))]
    assert run(viewers.check_stories(c)) == 1
    text, buttons = vw.sent[0]
    assert "Storis'ingizni ko'rdi" in text and "<b>Ali</b>" in text and "Valiyev" in text
    assert "<code>42</code>" in text and "@alijon" in text and "🔥" in text
    datas = [d for row in buttons for _, d in row]
    assert datas == ["wch:dm:42", "vw:ign:42"]


def test_story_only_new_viewers_and_skip_unchanged(vw):
    c = vw.client
    c.stories = [story(7, 1)]
    c.views[7] = [(user(42), None)]
    run(viewers.check_stories(c))
    n_calls = c.calls.count("GetStoryViewsListRequest")
    run(viewers.check_stories(c))                           # ko'rishlar soni o'zgarmadi — ro'yxat so'ralmaydi
    assert c.calls.count("GetStoryViewsListRequest") == n_calls and len(vw.sent) == 1
    c.stories = [story(7, 2)]
    c.views[7].insert(0, (user(43, "Vali", "", None), None))
    run(viewers.check_stories(c))
    assert len(vw.sent) == 2 and "Vali" in vw.sent[1][0] and "Username: yo'q" in vw.sent[1][0]


def test_story_ignored_user_not_reported(vw):
    vw.ignored[42] = "Ali"
    c = vw.client
    c.stories = [story(7, 2)]
    c.views[7] = [(user(42), None), (user(43, "Vali"), None)]
    assert run(viewers.check_stories(c)) == 1
    assert "Vali" in vw.sent[0][0] and "<code>42</code>" not in vw.sent[0][0]


def test_story_many_viewers_grouped_and_paged(vw):
    c = vw.client
    c.stories = [story(7, 130)]
    c.views[7] = [(user(1000 + i, f"Odam{i}"), None) for i in range(130)]
    assert run(viewers.check_stories(c)) == 130
    assert len(vw.sent) == 1                                 # bitta umumiy xabar
    text, buttons = vw.sent[0]
    assert "130 kishi" in text and "… yana 70 kishi" in text
    assert sum(len(r) for r in buttons) == viewers.GROUP_BUTTONS
    assert c.calls.count("GetStoryViewsListRequest") == 2   # 100 tadan sahifalab


def test_story_toggle_off_in_run_loop(vw, monkeypatch):
    monkeypatch.setitem(runtime._values, "story_viewers", False)
    assert "yoqildi" in run(viewers.toggle("story_viewers")) and runtime.get("story_viewers") is True


def test_start_payload():
    assert viewers.start_payload("/start profil") == "profil"
    assert viewers.start_payload("/start") == ""
    assert viewers.start_payload("/start@AnnaBot") == ""
    assert viewers.start_payload("salom") is None


def test_bot_visitor_from_profile_link(vw):
    run(viewers.bot_visitor(user(42), "/start profil"))
    text, buttons = vw.sent[0]
    assert "havola orqali" in text and "<code>42</code>" in text and "Valiyev" in text and "@alijon" in text
    run(viewers.bot_visitor(user(43, "Vali"), "salom, Husan aka bormi?"))
    assert "yozdi" in vw.sent[1][0] and "Husan aka bormi" in vw.sent[1][0]


def test_bot_visitor_ignored_bots_and_toggle(vw, monkeypatch):
    vw.ignored[42] = "Ali"
    run(viewers.bot_visitor(user(42), "/start"))
    bot = user(50)
    bot.bot = True
    run(viewers.bot_visitor(bot, "/start"))
    monkeypatch.setitem(runtime._values, "bot_visitors", False)
    run(viewers.bot_visitor(user(51), "/start"))
    assert vw.sent == []


def test_panel_explains_and_shows_link(vw):
    text, rows = run(viewers.panel("AnnaBot"))
    assert "kim ochganini hech kimga bermaydi" in text
    assert "https://t.me/AnnaBot?start=profil" in text and "{prefix}" in text
    datas = [d for row in rows for _, d in row]
    assert {"vw:set:story_viewers", "vw:set:bot_visitors", "vw:check", "vw:list"} <= set(datas)


def test_ignore_view(vw):
    text, rows = run(viewers.ignore_view())
    assert "bo'sh" in text
    vw.ignored[42] = "Ali (@alijon)"
    text, rows = run(viewers.ignore_view())
    assert rows[0] == [("↩️ Ali (@alijon)", "vw:unign:42")]
