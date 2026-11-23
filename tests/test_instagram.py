"""📸 Instagram: bir martalik media, avtojavob, arxiv va qaytarib olingan xabarlar (tarmoqsiz, instagrapi'siz)."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import availability
import ai
import config
import instagram
import runtime
import storage
import usage
from conftest import run

STARTED = 1_700_000_000.0
NEW = 1_700_000_100_000_000      # mikrosoniya — dastur ishga tushgandan keyin
OLD = 1_600_000_000_000_000      # ishga tushishdan oldin


def text_item(iid, uid, text, ts=NEW, **kw):
    return {"item_id": iid, "user_id": uid, "timestamp": ts, "item_type": "text", "text": text, **kw}


def raven(iid, uid="2", ts=NEW, video=False, seen=False, mode="once"):
    media = {"media_type": 2 if video else 1, "id": "m"}
    if not seen:
        media["image_versions2"] = {"candidates": [{"url": "https://cdn/small.jpg", "width": 320, "height": 480},
                                                   {"url": "https://cdn/big.jpg", "width": 1080, "height": 1920}]}
        if video:
            media["video_versions"] = [{"url": "https://cdn/low.mp4", "width": 480, "height": 854},
                                       {"url": "https://cdn/hd.mp4", "width": 720, "height": 1280}]
            media["video_duration"] = 7.5
    return {"item_id": iid, "user_id": uid, "timestamp": ts, "item_type": "raven_media",
            "visual_media": {"media": media, "view_mode": mode, "seen_count": 0}}


def thread(tid, items, group=False, title=""):
    return {"thread_id": tid, "thread_title": title, "is_group": group, "items": list(reversed(items)),
            "users": [{"pk": 2, "username": "ali.v", "full_name": "Ali Valiyev"}]}


class FakeCl:
    """instagrapi.Client o'rniga: inbox — Direct'dagi chatlar (xom JSON)."""

    def __init__(self, inbox=None, pending=None, error=None):
        self.inbox, self.pending, self.error = inbox or [], pending or [], error
        self.sent, self.calls = [], []
        self.user_id = "1"

    def private_request(self, endpoint, data=None, params=None, **kw):
        self.calls.append(endpoint)
        if self.error:
            raise self.error
        threads = self.inbox if endpoint == "direct_v2/inbox/" else self.pending
        return {"inbox": {"threads": threads}}

    def direct_send(self, text, thread_ids=()):
        self.sent.append((text, list(thread_ids)))

    def get_settings(self):
        return {"uuids": {"phone_id": "p"}, "cookies": {"sessionid": "secret"}, "authorization_data": {"x": 1}}


class FakeIgStorage:
    def __init__(self):
        self.session = None
        self.saved: dict[str, dict] = {}
        self.archive: dict[tuple, dict] = {}
        self.chats: dict[str, dict] = {}
        self.media: dict[tuple, tuple[bytes, str]] = {}

    async def ig_get_session(self):
        return self.session

    async def ig_set_session(self, **fields):
        self.session = {**(self.session or {}), **fields}

    async def ig_delete_session(self):
        self.session = None

    async def ig_saved_has(self, item_id):
        return item_id in self.saved

    async def ig_saved_add(self, item_id, doc):
        if item_id in self.saved:
            return False
        self.saved[item_id] = doc
        return True

    async def ig_saved_count(self):
        return sum(1 for d in self.saved.values() if d["status"] == "saved")

    async def ig_archive_add_many(self, docs):
        n = 0
        for d in docs:
            key = (d["thread_id"], d["msg_id"])
            if key not in self.archive:
                self.archive[key] = dict(d)
                n += 1
        return n

    async def ig_archive_window(self, tid, since):
        return [dict(d) for (t, _), d in self.archive.items()
                if t == tid and d["date"] >= since and not d.get("deleted")]

    async def ig_archive_mark_deleted(self, tid, ids, ts):
        for i in ids:
            self.archive[(tid, i)].update(deleted=True, deleted_at=ts)

    async def ig_ai_ids(self, tid):
        return {d["msg_id"] for (t, _), d in self.archive.items() if t == tid and d.get("ai")}

    async def ig_archive_export(self, tid, limit):
        return sorted((dict(d) for (t, _), d in self.archive.items() if t == tid), key=lambda d: d["date"])

    async def ig_chat_touch(self, tid, title, usernames, group, last, added):
        c = self.chats.setdefault(tid, {"count": 0, "last": 0})
        c.update(title=title, usernames=usernames, group=group, last=max(c["last"], last))
        c["count"] += added
        if added:
            c.update(changed=time.time(), drive_dirty=True)

    async def ig_chats(self, page, per):
        rows = sorted(({"thread_id": k, **v} for k, v in self.chats.items()), key=lambda r: -r["last"])
        return rows[page * per:page * per + per + 1]

    async def ig_chat_get(self, tid):
        return {"thread_id": tid, **self.chats[tid]} if tid in self.chats else None

    async def ig_chat_set(self, tid, **fields):
        self.chats.setdefault(tid, {"count": 0, "last": 0}).update(fields)

    async def ig_chat_count(self, tid):
        return sum(1 for (t, _) in self.archive if t == tid)

    async def ig_archive_page(self, tid, skip, limit):
        rows = sorted((dict(d) for (t, _), d in self.archive.items() if t == tid),
                      key=lambda d: -d["date"])
        return rows[skip:skip + limit + 1]

    async def ig_drive_pending(self, limit=50):
        rows = [{"thread_id": k, **v} for k, v in self.chats.items() if v.get("drive_dirty")]
        return sorted(rows, key=lambda r: r.get("changed") or 0)[:limit]

    async def ig_drive_done(self, tid, changed, file_id, link):
        c = self.chats[tid]
        c.update(drive_file_id=file_id, drive_link=link, drive_synced=time.time())
        if c.get("changed") == changed:
            c["drive_dirty"] = False

    async def ig_drive_mark_all(self, only_missing=False):
        n = 0
        for c in self.chats.values():
            if c.get("count") and not (only_missing and c.get("drive_file_id")):
                c["drive_dirty"] = True
                c.setdefault("changed", time.time())
                n += 1
        return n

    async def ig_backfill_pending(self, limit=50):
        rows = [{"thread_id": k, **v} for k, v in self.chats.items() if "backfilled" not in v]
        return sorted(rows, key=lambda r: -(r.get("last") or 0))[:limit]

    # --- media fayllari ---
    async def ig_media_pending(self, limit=10):
        rows = [dict(d) for d in self.archive.values() if d.get("media_state") in ("pending", "retry")]
        return sorted(rows, key=lambda d: -d["date"])[:limit]

    async def ig_media_save(self, tid, mid, data, mime):
        self.media[(tid, mid)] = (bytes(data), mime)

    async def ig_media_get(self, tid, msg_ids):
        return {mid: blob for (t, mid), blob in self.media.items() if t == tid and mid in msg_ids}

    async def ig_media_set(self, tid, mid, **fields):
        if (tid, mid) in self.archive:
            self.archive[(tid, mid)].update(fields)

    async def ig_media_counts(self):
        out: dict = {}
        for d in self.archive.values():
            if d.get("media_state"):
                out[d["media_state"]] = out.get(d["media_state"], 0) + 1
        return out

    async def ig_media_bytes(self):
        return sum(len(b) for b, _ in self.media.values())

    async def ig_stats(self):
        return {"chats": len(self.chats), "messages": len(self.archive),
                "unsent": sum(1 for d in self.archive.values() if d.get("deleted")),
                "pending": sum(1 for c in self.chats.values() if c.get("drive_dirty")),
                "left": sum(1 for c in self.chats.values() if "backfilled" not in c),
                "media": len(self.media),
                "media_left": sum(1 for d in self.archive.values()
                                  if d.get("media_state") in ("pending", "retry"))}

    async def get_mode(self):
        return "on"


@pytest.fixture
def ig(monkeypatch):
    """Toza holat: soxta baza, soxta bot, yuklab olish tarmoqsiz."""
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
    for name in ("_known", "_missing", "_muted_until", "_alerted"):
        monkeypatch.setattr(instagram, name, {})
    monkeypatch.setattr(instagram, "_ai_sent", instagram.defaultdict(lambda: instagram.deque(maxlen=50)))
    for name in ("_check_lock", "_api_lock", "_login_lock"):
        monkeypatch.setattr(instagram, name, asyncio.Lock())
    monkeypatch.setattr(instagram, "_wake", asyncio.Event())

    out = SimpleNamespace(fs=fs, media=[], alerts=[], notes=[], downloads=[])

    async def send_media(data, filename, caption, video, as_file):
        out.media.append(SimpleNamespace(data=data, filename=filename, caption=caption, video=video, as_file=as_file))
        return True

    async def alert(text):
        out.alerts.append(text)

    async def notify(tid, text):
        out.notes.append((tid, text))

    async def download(url, limit=0, on_progress=None):
        out.downloads.append(url)
        return b"DATA:" + url.encode(), "video/mp4" if url.endswith(".mp4") else "image/jpeg"

    instagram.setup(alert, send_media, notify)
    monkeypatch.setattr(instagram, "_download", download)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(availability, "mark_owner_active", noop)
    monkeypatch.setattr(usage, "record", noop)

    async def usage_ok(key):
        return True, None

    monkeypatch.setattr(usage, "check", usage_ok)
    return out


def connect(cl):
    instagram.st.client, instagram.st.user_id, instagram.st.username = cl, "1", "husan"


# ---------- Instagram javobini o'qish ----------
def test_parse_raven_photo_picks_best_quality():
    th = instagram.parse_thread(thread("t1", [raven("a")]), "1")
    it = th.items[0]
    assert it.ephemeral and it.kind == "rasm" and it.view_mode == "once"
    assert it.url == "https://cdn/big.jpg" and (it.width, it.height) == (1080, 1920)
    assert th.person("2") == ("ali.v", "Ali Valiyev") and th.title == "Ali Valiyev"


def test_parse_raven_video_and_seen_media():
    video = instagram.parse_item(raven("v", video=True))
    assert video.kind == "video" and video.url == "https://cdn/hd.mp4" and video.duration == 7.5
    seen = instagram.parse_item(raven("s", seen=True, video=True))
    assert seen.ephemeral and seen.url == "" and seen.kind == "video"   # ochilgan — Instagram manzil bermaydi
    permanent = instagram.parse_item(raven("p", mode="permanent"))
    assert not permanent.ephemeral


def test_parse_items_order_out_and_time():
    th = instagram.parse_thread(thread("t", [text_item("1", "2", "salom", ts=NEW),
                                             text_item("2", "1", "alik", ts=NEW + 5_000_000)]), "1")
    assert [i.item_id for i in th.items] == ["1", "2"]          # eskidan yangiga
    assert th.items[0].ts == pytest.approx(NEW / 1e6) and not th.items[0].out and th.items[1].out
    assert instagram.parse_item({"item_id": "x", "item_type": "like", "user_id": "2"}).kind == "like"
    link = instagram.parse_item({"item_id": "l", "item_type": "link", "link": {"text": "https://a.uz"}})
    assert instagram.content_of(link) == "https://a.uz"
    assert instagram.content_of(instagram.parse_item(raven("r"))) == "[🔥 bir martalik rasm]"


def test_error_kind_and_explain_by_name():
    LoginRequired = type("LoginRequired", (Exception,), {})
    ChallengeRequired = type("ChallengeRequired", (LoginRequired,), {})
    Wait = type("PleaseWaitFewMinutes", (Exception,), {})
    assert instagram.error_kind(LoginRequired()) == "auth"
    assert instagram.error_kind(ChallengeRequired()) == "auth"
    assert instagram.error_kind(Wait()) == "throttle"
    assert instagram.error_kind(ValueError("x")) == "other"
    assert "Bu men edim" in instagram.explain(ChallengeRequired())
    assert "parol" in instagram.explain(type("BadPassword", (Exception,), {})())


def test_clean_sessionid():
    assert instagram.clean_sessionid(' sessionid=123%3AabcDEF1234567; path=/ ') == "123%3AabcDEF1234567"
    assert instagram.clean_sessionid('"123:abc"') == "123:abc"


# ---------- 🔥 bir martalik media ----------
def test_view_once_saved_once_and_not_marked_seen(ig):
    cl = FakeCl([thread("t1", [raven("a"), raven("v", ts=NEW + 1_000_000, video=True),
                                raven("mine", uid="1", ts=NEW + 2_000_000)])])
    connect(cl)
    res = run(instagram.check_once())
    assert res["saved"] == 2
    assert [m.filename for m in ig.media] == ["instagram_a.jpg", "instagram_v.mp4"]
    assert ig.media[1].video == {"duration": 7.5, "w": 720, "h": 1280} and not ig.media[0].as_file
    assert "Ali Valiyev" in ig.media[0].caption and "@ali.v" in ig.media[0].caption
    assert "bir marta" in ig.media[0].caption
    # Faqat o'qish so'rovlari — mediani «ochildi» qiladigan so'rov yo'q
    assert set(cl.calls) <= {"direct_v2/inbox/", "direct_v2/pending_inbox/"}
    run(instagram.check_once())
    assert len(ig.media) == 2 and ig.fs.saved["a"]["status"] == "saved"   # qayta yuborilmaydi
    assert run(storage.ig_saved_count()) == 2


def test_already_opened_view_once_reported_only_if_new(ig):
    connect(FakeCl([thread("t1", [raven("old", ts=OLD, seen=True), raven("new", seen=True)])]))
    run(instagram.check_once())
    assert ig.media == [] and len(ig.alerts) == 1 and "allaqachon ochilgan" in ig.alerts[0]
    run(instagram.check_once())
    assert len(ig.alerts) == 1


def test_download_failure_retried_next_check(ig, monkeypatch):
    connect(FakeCl([thread("t1", [raven("a")])]))
    calls = []

    async def flaky(url, limit=0, on_progress=None):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("cdn")
        return b"x", "image/webp"

    monkeypatch.setattr(instagram, "_download", flaky)
    run(instagram.check_once())
    assert ig.media == [] and "a" not in ig.fs.saved
    run(instagram.check_once())
    assert len(ig.media) == 1 and ig.media[0].filename.endswith(".webp") and ig.media[0].as_file


def test_save_toggle_off(ig, monkeypatch):
    monkeypatch.setitem(runtime._values, "ig_save", False)
    connect(FakeCl([thread("t1", [raven("a")])]))
    run(instagram.check_once())
    assert ig.media == []


# ---------- 🗂 arxiv va qaytarib olingan xabarlar ----------
def test_archive_and_unsent_detection(ig):
    items = [text_item("1", "2", "pul qachon?", ts=NEW), text_item("2", "2", "kechirasiz", ts=NEW + 1_000_000),
             text_item("3", "1", "ertaga", ts=NEW + 2_000_000)]
    cl = FakeCl([thread("t1", items)])
    connect(cl)
    run(instagram.check_once())
    assert len(ig.fs.archive) == 3 and ig.fs.chats["t1"]["count"] == 3
    # suhbatdosh 2-xabarini, siz 3-xabaringizni qaytarib oldingiz
    cl.inbox = [thread("t1", items[:1])]
    run(instagram.check_once())
    assert ig.notes == []                                   # bir marta ko'rinmadi — hali ishonch yo'q
    run(instagram.check_once())
    assert len(ig.notes) == 1
    tid, text = ig.notes[0]
    assert tid == "t1" and "qaytarib olindi" in text and "kechirasiz" in text and "ertaga" not in text
    assert ig.fs.archive[("t1", "2")]["deleted"] and ig.fs.archive[("t1", "3")]["deleted"]
    run(instagram.check_once())
    assert len(ig.notes) == 1                               # qayta ogohlantirilmaydi


def test_message_returning_is_not_unsent(ig):
    items = [text_item("1", "2", "a", ts=NEW), text_item("2", "2", "b", ts=NEW + 1_000_000)]
    cl = FakeCl([thread("t1", items)])
    connect(cl)
    run(instagram.check_once())
    cl.inbox = [thread("t1", items[:1])]
    run(instagram.check_once())
    cl.inbox = [thread("t1", items)]                        # Instagram bir marta to'liq bermadi
    run(instagram.check_once())
    cl.inbox = [thread("t1", items[:1])]
    run(instagram.check_once())
    assert ig.notes == []


def test_older_messages_leaving_window_are_not_unsent(ig):
    items = [text_item(str(i), "2", f"m{i}", ts=NEW + i * 1_000_000) for i in range(5)]
    cl = FakeCl([thread("t1", items)])
    connect(cl)
    run(instagram.check_once())
    cl.inbox = [thread("t1", items[2:] + [text_item("9", "2", "yangi", ts=NEW + 9_000_000)])]
    run(instagram.check_once())
    run(instagram.check_once())
    assert ig.notes == [] and not any(d.get("deleted") for d in ig.fs.archive.values())


def test_export_html_marks_deleted(ig):
    items = [text_item("1", "2", "salom <b>", ts=NEW), text_item("2", "2", "o'chdi", ts=NEW + 1_000_000)]
    cl = FakeCl([thread("t1", items)])
    connect(cl)
    run(instagram.check_once())
    cl.inbox = [thread("t1", items[:1])]
    run(instagram.check_once())
    run(instagram.check_once())
    data, name, n = run(instagram.export_html("t1"))
    page = data.decode()
    assert n == 2 and name == "instagram_Ali_Valiyev.html"
    assert "📸 Ali Valiyev" in page and "salom &lt;b&gt;" in page
    assert page.count("class='msg del'") == 1                 # qaytarib olingan xabar qizil
    text, rows = run(instagram.chats_view(0))
    assert rows[0][0] == ("Ali Valiyev · 2", "ig:view:t1:0")


# ---------- 🤖 avtojavob va 📥 xabarlar botga ----------
@pytest.fixture
def ai_on(ig, monkeypatch):
    monkeypatch.setitem(runtime._values, "ig_autoreply", True)
    calls = []

    async def status():
        return availability.Status(True, True, "doim", "Husan aka hozir band.")

    async def generate(history, context):
        calls.append((history, context))
        return {"reply": "Assalomu alaykum! Husan aka band, yetkazaman.", "summary": "Ali pul so'rayapti",
                "urgent": False, "call_owner": False, "spam": False, "reply_to": 0, "tokens": 10}

    monkeypatch.setattr(availability, "get_status", status)
    monkeypatch.setattr(ai, "generate", generate)
    ig.ai_calls = calls
    return ig


def test_autoreply_answers_new_messages_only(ai_on):
    cl = FakeCl([thread("123", [text_item("old", "2", "eski", ts=OLD), text_item("n1", "2", "salom", ts=NEW),
                                text_item("n2", "2", "qarzni qachon berasiz?", ts=NEW + 1)])])
    connect(cl)
    res = run(instagram.check_once())
    assert res["replied"] == 1
    assert cl.sent == [("Assalomu alaykum! Husan aka band, yetkazaman.", [123])]
    history, context = ai_on.ai_calls[0]
    assert [h["content"] for h in history][-2:] == ["salom", "qarzni qachon berasiz?"]
    assert "Instagram" in context
    tid, note = ai_on.notes[0]
    assert tid == "123" and "Anna javobi" in note and "qarzni qachon" in note and "Ali pul" in note
    # Javob Instagram'da paydo bo'ldi — bu egasi yozgani emas, AI jim bo'lmaydi
    cl.inbox[0]["items"].insert(0, text_item("ai1", "1", "Assalomu alaykum! Husan aka band, yetkazaman.",
                                             ts=NEW + 2))
    run(instagram.check_once())
    assert len(cl.sent) == 1 and instagram._muted_until.get("123", 0) == 0
    assert ai_on.fs.archive[("123", "ai1")]["ai"] is True


def test_owner_writing_mutes_ai(ai_on):
    cl = FakeCl([thread("123", [text_item("n1", "2", "salom", ts=NEW), text_item("me", "1", "hozir", ts=NEW + 1)])])
    connect(cl)
    run(instagram.check_once())
    assert cl.sent == [] and instagram._muted_until["123"] > time.time()


def test_groups_and_requests_not_autoreplied(ai_on):
    cl = FakeCl([thread("g", [text_item("n1", "2", "salom")], group=True, title="Do'stlar")],
                pending=[thread("p", [text_item("n2", "2", "reklama")])])
    connect(cl)
    run(instagram.check_once())
    assert cl.sent == []
    notes = dict(ai_on.notes)
    assert "g" not in notes and "so'rov" in notes["p"]       # so'rovlar botga keladi, guruh — yo'q


def test_forward_without_autoreply(ig, monkeypatch):
    monkeypatch.setitem(runtime._values, "ig_forward", True)
    cl = FakeCl([thread("123", [text_item("n1", "2", "salom")])])
    connect(cl)
    run(instagram.check_once())
    assert cl.sent == [] and "salom" in ig.notes[0][1] and "Anna javobi" not in ig.notes[0][1]


def test_nothing_forwarded_when_all_off(ig):
    connect(FakeCl([thread("123", [text_item("n1", "2", "salom")])]))
    run(instagram.check_once())
    assert ig.notes == []


def test_owner_reply_from_bot(ig):
    cl = FakeCl()
    connect(cl)
    text = run(instagram.send_text("555", "ok, ertaga"))
    assert cl.sent == [("ok, ertaga", [555])] and "yuborildi" in text
    assert instagram._muted_until["555"] > time.time()
    instagram.st.client = None
    assert "ulanmagan" in run(instagram.send_text("555", "x"))


# ---------- xatolar ----------
def test_session_expired_disconnects_and_alerts(ig):
    connect(FakeCl(error=type("LoginRequired", (Exception,), {})()))
    ig.fs.session = {"settings": "{}", "active": True}
    try:
        run(instagram.check_once())
    except Exception as e:
        run(instagram._on_error(e))
    assert instagram.st.client is None and ig.fs.session["active"] is False
    assert "sessiyasi tugadi" in ig.alerts[0]


def test_throttle_pauses(ig):
    run(instagram._on_error(type("PleaseWaitFewMinutes", (Exception,), {})()))
    assert instagram.st.paused_until > time.time() and "cheklov" in ig.alerts[0]


def test_check_now_reports(ig):
    assert run(instagram.check_now()) == "Instagram ulanmagan"
    connect(FakeCl([thread("t1", [raven("a")])]))
    assert run(instagram.check_now()).startswith("✅ 1 ta chat")


# ---------- login: kod botdan keladi ----------
def test_code_exchange_between_thread_and_bot(ig):
    prompts = []

    async def say(text):
        prompts.append(text)

    async def scenario():
        ask = instagram._asker(asyncio.get_running_loop(), say)
        result = {}
        t = threading.Thread(target=lambda: result.update(code=ask("kod?")))
        t.start()
        for _ in range(100):
            if instagram.waiting_code():
                break
            await asyncio.sleep(0.01)
        assert not instagram.submit_code("salom")             # kodga o'xshamaydi
        assert instagram.submit_code("123 456")
        await asyncio.to_thread(t.join, 5)
        await asyncio.sleep(0)
        return result

    assert run(scenario())["code"] == "123456"
    assert prompts == ["kod?"] and not instagram.waiting_code()


def test_login_with_two_factor(ig, monkeypatch):
    TwoFactorRequired = type("TwoFactorRequired", (Exception,), {})

    class LoginCl(FakeCl):
        username = "husan"
        logins: list = []

        def __init__(self):
            super().__init__()
            self.last_json = {}

        def login(self, username, password, verification_code=""):
            self.logins.append((username, password, verification_code))
            if not verification_code:
                self.last_json = {"two_factor_info": {"two_factor_identifier": "id", "totp_two_factor_on": True}}
                raise TwoFactorRequired()
            return True

    created = []

    def new_client(settings=None):
        created.append(settings)
        return LoginCl()

    monkeypatch.setattr(instagram, "lib_problem", lambda: None)
    monkeypatch.setattr(instagram, "_new_client", new_client)
    ig.fs.session = {"settings": '{"uuids": {"a": 1}, "cookies": {"sessionid": "old"}}', "active": False}

    prompts = []

    async def say(text):   # assert bu yerda emas: yiqilsa login oqimi kodni 5 daqiqa kutib qoladi
        prompts.append(text)
        instagram.submit_code("654321")

    result = run(instagram.login("husan", "parol", say))
    assert len(prompts) == 1 and "autentifikator" in prompts[0]
    assert result.startswith("✅ Instagram ulandi: @husan")
    assert LoginCl.logins == [("husan", "parol", ""), ("husan", "parol", "654321")]
    assert created == [{"uuids": {"a": 1}}]                  # eski qurilma saqlanadi, eski cookie — yo'q
    assert instagram.connected() and ig.fs.session["active"] is True
    assert "parol" not in ig.fs.session["settings"]           # parol hech qayerga yozilmaydi


def test_login_failure_explained(ig, monkeypatch):
    class Bad(FakeCl):
        def login(self, *a, **k):
            raise type("BadPassword", (Exception,), {})()

    monkeypatch.setattr(instagram, "lib_problem", lambda: None)
    monkeypatch.setattr(instagram, "_new_client", lambda settings=None: Bad())

    async def say(text):
        pass

    assert "login yoki parol noto'g'ri" in run(instagram.login("husan", "x", say))
    assert not instagram.connected()


def test_login_without_library(ig, monkeypatch):
    monkeypatch.setattr(instagram, "lib_problem", lambda: "instagrapi o'rnatilmagan")
    assert "o'rnatilmagan" in run(instagram.login("a", "b", None))
    assert "o'rnatilmagan" in run(instagram.login_session("123%3Aabcdefghijkl"))


def test_bad_sessionid_rejected(ig, monkeypatch):
    monkeypatch.setattr(instagram, "lib_problem", lambda: None)
    assert "o'xshamaydi" in run(instagram.login_session("salom"))


# ---------- panel ----------
def test_panel_and_toggles(ig):
    text, rows = run(instagram.panel())
    assert "ulanmagan" in text and rows[-1] == [("🔑 Ulash", "ig:help"), ("🌐 Ulanish testi", "ig:net")]
    data = [d for row in rows for _, d in row]
    assert all(len(d.encode()) <= 64 for d in data)
    assert "ig:set:ig_autoreply" in data
    assert "yoqildi" in run(instagram.toggle("ig_autoreply")) and runtime.get("ig_autoreply") is True
    connect(FakeCl())
    text, rows = run(instagram.panel())
    assert "@husan" in text and rows[-1][0] == ("🚪 Chiqish", "ig:logout")
    assert "✅ 🤖 Avtojavob" in [t for row in rows for t, _ in row]


def test_logout_clears_session(ig):
    connect(FakeCl())
    ig.fs.session = {"settings": "{}"}
    run(instagram.logout())
    assert not instagram.connected() and ig.fs.session is None


def test_ig_button_data_fits_telegram_limit():
    tid = "340282366841710301244276017723107508377"   # Instagram thread id — 39 xona
    import control_bot
    for row in control_bot._ig_buttons(tid):
        for b in row:
            assert len(getattr(b, "data", None) or b.type.data) <= 64


def test_send_ig_media_video_attributes(monkeypatch):
    import control_bot
    from telethon.tl.types import DocumentAttributeVideo
    sent = []

    class Bot:
        async def send_file(self, chat, f, **kw):
            sent.append((f.name, kw))

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    assert run(control_bot.send_ig_media(b"x", "a.mp4", "cap", {"duration": 7.5, "w": 720, "h": 1280}))
    name, kw = sent[0]
    attr = kw["attributes"][0]
    assert isinstance(attr, DocumentAttributeVideo) and (attr.w, attr.h, attr.duration) == (720, 1280, 7.5)
    assert kw["supports_streaming"] and kw["parse_mode"] == "html"


# ---------- «ilova eski» xatosi va kutubxona versiyasi ----------
def test_needs_upgrade_explained_with_steps():
    Upgrade = type("ClientNeedsUpgradeError", (Exception,), {})
    text = instagram.explain(Upgrade("Your version of Instagram is out of date."))
    assert "Ha, men edim" in text and "/igsession" in text
    assert instagram.explain(RuntimeError("needs_upgrade")) == instagram.NEEDS_UPGRADE_HELP


def test_login_needs_upgrade_message(ig, monkeypatch):
    class Old(FakeCl):
        def login(self, *a, **k):
            raise type("ClientNeedsUpgradeError", (Exception,), {})("Your version of Instagram is out of date")

    monkeypatch.setattr(instagram, "lib_problem", lambda: None)
    monkeypatch.setattr(instagram, "_new_client", lambda settings=None: Old())
    result = run(instagram.login("husan", "x", None))
    assert result.startswith("❌") and "/igsession" in result


@pytest.mark.parametrize("version,ok", [(None, False), ("2.1.5", False), ("3.2.18", False),
                                        ("3.2.19", True), ("3.10.0", True), ("4.0", True)])
def test_lib_version_check(monkeypatch, version, ok):
    monkeypatch.setattr(instagram, "lib_version", lambda: version)
    problem = instagram.lib_problem()
    assert (problem is None) is ok
    if version and not ok:
        assert version in problem and "pip install -U" in problem


def test_saved_app_version_not_reused():
    saved = {"uuids": {"uuid": "u"}, "user_agent": "Instagram 269.0.0.18.75 Android (...)",
             "device_settings": {"app_version": "269.0.0.18.75", "version_code": "314665256",
                                 "bloks_versioning_id": "old", "model": "Pixel 8 Pro", "android_version": 34},
             "cookies": {"sessionid": "s"}}
    s = instagram._with_current_app(saved)
    assert s["device_settings"] == {"model": "Pixel 8 Pro", "android_version": 34}   # qurilma qoladi
    assert "user_agent" not in s and s["uuids"] == {"uuid": "u"} and s["cookies"] == {"sessionid": "s"}
    assert saved["device_settings"]["app_version"] == "269.0.0.18.75"              # asl lug'at o'zgarmaydi
    assert "user_agent" not in instagram._device_only(saved)


# ---------- proksi va ulanish testi ----------
@pytest.mark.parametrize("raw,url", [
    ("", ""),
    ("http://u:p@1.2.3.4:8000", "http://u:p@1.2.3.4:8000"),
    ("socks5://u:p@host.io:1080", "socks5://u:p@host.io:1080"),
    ("1.2.3.4:8000:user1:pa55", "http://user1:pa55@1.2.3.4:8000"),      # provayder ko'rinishi
    ("user1:pa55@gate.pr.io:7000", "http://user1:pa55@gate.pr.io:7000"),
    ("1.2.3.4:8000", "http://1.2.3.4:8000"),
    (' "http://u:p@h:1" ', "http://u:p@h:1"),
])
def test_normalize_proxy(raw, url):
    assert instagram.normalize_proxy(raw) == url


def test_proxy_password_never_shown(monkeypatch):
    monkeypatch.setattr(config, "IG_PROXY", "1.2.3.4:8000:user1:Secr3tPass")
    monkeypatch.setattr(instagram, "PROXY", "http://user1:Secr3tPass@1.2.3.4:8000")
    ProxyError = type("ProxyError", (Exception,), {})
    text = instagram.explain(ProxyError("Cannot connect to proxy http://user1:Secr3tPass@1.2.3.4:8000 refused"))
    assert "Secr3tPass" not in text and "proksiga ulanib bo'lmadi" in text and "Ulanish testi" in text
    assert instagram.mask_proxy(instagram.PROXY) == "http://***@1.2.3.4:8000"
    Conn = type("ClientConnectionError", (Exception,), {})
    assert "407" in instagram.explain(Conn("407 Proxy Authentication Required"))


def test_connection_error_without_proxy(monkeypatch):
    monkeypatch.setattr(instagram, "PROXY", "")
    Timeout = type("ReadTimeout", (Exception,), {})
    text = instagram.explain(Timeout("Read timed out"))
    assert "vaqt tugadi" in text and "IG_PROXY bo'sh" in text


def _fake_probe(results):
    def probe(url, proxy):
        return results[(url == instagram.IP_PROBE_URL, bool(proxy))]
    return probe


def test_net_check_broken_proxy(monkeypatch):
    monkeypatch.setattr(config, "IG_PROXY", "5.6.7.8:8000:user1:Secr3tPass")
    monkeypatch.setattr(instagram, "PROXY", instagram.normalize_proxy(config.IG_PROXY))
    monkeypatch.setattr(instagram, "_probe", _fake_probe({
        (True, False): (True, "35.1.1.1", 0.2), (False, False): (True, "HTTP 200", 0.3),
        (True, True): (False, "ProxyError: refused", 0.0), (False, True): (False, "ProxyError: refused", 0.0)}))
    text = run(instagram.net_check())
    assert "35.1.1.1" in text and "Secr3tPass" not in text and "http://***@5.6.7.8:8000" in text
    assert "avtomatik to'g'rilandi" in text and "Proksi ishlamayapti" in text


def test_net_check_proxy_ok_and_not_used(monkeypatch):
    monkeypatch.setattr(config, "IG_PROXY", "http://u:p@h:1")
    monkeypatch.setattr(instagram, "PROXY", "http://u:p@h:1")
    ok = {(True, False): (True, "35.1.1.1", 0.2), (False, False): (True, "HTTP 200", 0.3),
          (True, True): (True, "91.2.2.2", 0.5), (False, True): (True, "HTTP 200", 0.6)}
    monkeypatch.setattr(instagram, "_probe", _fake_probe(ok))
    assert "Proksi ishlayapti" in run(instagram.net_check())
    ok[(True, True)] = (True, "35.1.1.1", 0.5)                 # chiquvchi IP o'zgarmadi
    assert "proksi ishlamayapti" in run(instagram.net_check())


def test_net_check_no_proxy(monkeypatch):
    monkeypatch.setattr(instagram, "PROXY", "")
    monkeypatch.setattr(instagram, "_probe", _fake_probe({
        (True, False): (True, "35.1.1.1", 0.2), (False, False): (False, "ConnectTimeout: x", 0.0)}))
    text = run(instagram.net_check())
    assert "IG_PROXY: bo'sh" in text and "residential proksi kerak" in text


@pytest.mark.parametrize("raw,bad", [
    ("http://login:parol@host:port", "misol matni"),           # yo'riqnomadagi misolning o'zi
    ("http://ali:Parol123@1.2.3.4:8000", "misol matni"),
    ("http://u:p@gate.pr.io", "noto'g'ri formatda"),           # port yo'q
    ("ftp://u:p@gate.pr.io:21", "noto'g'ri formatda"),
    ("http://u1:Xy7@gate.pr.io:7000", None),
    ("socks5://u1:Xy7@9.9.9.9:1080", None),
    ("", None),
])
def test_proxy_problem(monkeypatch, raw, bad):
    monkeypatch.setattr(instagram, "PROXY", instagram.normalize_proxy(raw))
    problem = instagram.proxy_problem()
    assert (problem is None) if bad is None else (bad in problem)


def test_placeholder_proxy_blocks_login_and_test(ig, monkeypatch):
    monkeypatch.setattr(config, "IG_PROXY", "http://login:parol@host:port")
    monkeypatch.setattr(instagram, "PROXY", "http://login:parol@host:port")
    monkeypatch.setattr(instagram, "lib_problem", lambda: None)
    called = []
    monkeypatch.setattr(instagram, "_new_client", lambda settings=None: called.append(1))
    assert "Instagram login/paroli EMAS" in run(instagram.login("husan", "x", None))
    assert "misol matni" in run(instagram.login_session("123%3Aabcdefghijkl"))
    assert called == []                                        # Instagram'ga umuman urinilmadi
    monkeypatch.setattr(instagram, "_probe", lambda url, proxy: (True, "35.1.1.1", 0.1))
    text = run(instagram.net_check())
    assert "misol matni" in text and "Proksi IP" not in text
    assert "misol matni" in run(instagram.panel())[0]


# ---------- 🧦 proksini botdan o'chirish ----------
def test_proxy_toggle_from_bot(ig, monkeypatch):
    monkeypatch.setattr(config, "IG_PROXY", "http://u1:Xy7@gate.pr.io:7000")
    monkeypatch.setattr(instagram, "PROXY", "http://u1:Xy7@gate.pr.io:7000")
    text, rows = run(instagram.panel())
    assert "http://***@gate.pr.io:7000" in text and "Xy7" not in text
    assert [("✅ 🧦 Proksi (bosib o'chirish/yoqish)", "ig:set:ig_proxy")] in rows
    Proxy = type("ClientConnectionError", (Exception,), {})
    assert "🧦 Proksi" in instagram.explain(Proxy("ProxyError curl private transport failed"))

    rebuilt = []
    monkeypatch.setattr(instagram, "_new_client", lambda settings=None: rebuilt.append(settings) or FakeCl())
    connect(FakeCl())
    assert "o'chirildi" in run(instagram.toggle("ig_proxy"))
    assert instagram.active_proxy() == "" and runtime.get("ig_proxy") is False
    assert len(rebuilt) == 1 and instagram.connected()             # sessiya saqlangan holda qayta yasaldi
    text, rows = run(instagram.panel())
    assert "o'chiq" in text and ("▫️ 🧦 Proksi (bosib o'chirish/yoqish)", "ig:set:ig_proxy") in rows[-2]
    assert "botdan o'chirilgan" in instagram.explain(Proxy("timeout"))

    monkeypatch.setattr(instagram, "_probe", lambda url, proxy: (True, "35.1.1.1" if proxy is None else "X", 0.1))
    assert "botdan o'chirilgan" in run(instagram.net_check())
    assert "yoqildi" in run(instagram.toggle("ig_proxy")) and instagram.active_proxy()


def test_proxy_toggle_without_proxy(ig, monkeypatch):
    monkeypatch.setattr(instagram, "PROXY", "")
    assert "o'chiradigan proksi yo'q" in run(instagram.toggle("ig_proxy"))
    assert all(d != "ig:set:ig_proxy" for row in run(instagram.panel())[1] for _, d in row)


def test_disabled_placeholder_proxy_does_not_block_login(ig, monkeypatch):
    monkeypatch.setattr(instagram, "PROXY", "http://login:parol@host:port")
    monkeypatch.setitem(runtime._values, "ig_proxy", False)
    assert instagram.proxy_problem() is None                         # o'chirilgan — xalaqit bermaydi


# ---------- parol bilan login: «ilova eski» xatosida boshqa profillar sinaladi ----------
def test_needs_upgrade_detector():
    Up = type("ClientNeedsUpgradeError", (Exception,), {})
    assert instagram.needs_upgrade(Up())
    assert instagram.needs_upgrade(RuntimeError("needs_upgrade"))
    assert instagram.needs_upgrade(RuntimeError("Your version of Instagram is out of date."))
    assert not instagram.needs_upgrade(RuntimeError("bad password"))


class UpgradeCl(FakeCl):
    """Faqat berilgan ilova profili bilan login qiladi, qolganlariga «ilova eski» deydi."""
    accept = ""
    attempts: list = []

    def __init__(self):
        super().__init__()
        self.app = "default"
        self.last_json = {}
        self.username = "husan"

    def set_app(self, version):
        self.app = version

    def set_user_agent(self, user_agent=None):
        pass

    def login(self, username, password, verification_code=""):
        UpgradeCl.attempts.append(self.app)
        if self.app != UpgradeCl.accept:
            raise type("ClientNeedsUpgradeError", (Exception,), {})("out of date")
        return True


@pytest.fixture
def upgrade(monkeypatch):
    UpgradeCl.attempts = []
    monkeypatch.setattr(instagram, "lib_problem", lambda: None)
    monkeypatch.setattr(instagram, "_new_client", lambda settings=None: UpgradeCl())
    monkeypatch.setattr(instagram, "LOGIN_PAUSE", 0)
    monkeypatch.setattr(instagram, "app_versions", lambda: ["449.0.0.52.84", "448.0.0.0.20", "446.0.0.49.77"])
    return UpgradeCl


def test_login_retries_older_app_profiles(ig, upgrade):
    upgrade.accept = "446.0.0.49.77"          # faqat uchinchi profil o'tadi
    notes = []

    async def say(text):
        notes.append(text)

    assert run(instagram.login("husan", "parol", say)).startswith("✅")
    assert upgrade.attempts == ["default", "448.0.0.0.20", "446.0.0.49.77"]
    assert len(notes) == 2 and "2/3" in notes[0] and "3/3" in notes[1]
    assert instagram.connected()


def test_login_first_profile_wins_without_retries(ig, upgrade):
    upgrade.accept = "default"

    async def say(text):
        raise AssertionError("ortiqcha xabar")

    assert run(instagram.login("husan", "parol", say)).startswith("✅")
    assert upgrade.attempts == ["default"]


def test_login_all_profiles_rejected(ig, upgrade):
    upgrade.accept = "yo'q"

    async def say(text):
        pass

    result = run(instagram.login("husan", "parol", say))
    assert len(upgrade.attempts) == 3 and "/igsession" in result and not instagram.connected()


def test_login_other_errors_not_retried(ig, upgrade, monkeypatch):
    """Noto'g'ri parol yoki tasdiqlash so'ralsa — qayta urinilmaydi (akkaunt bloklanmasligi uchun)."""
    class Bad(UpgradeCl):
        def login(self, username, password, verification_code=""):
            UpgradeCl.attempts.append(self.app)
            raise type("BadPassword", (Exception,), {})()

    monkeypatch.setattr(instagram, "_new_client", lambda settings=None: Bad())

    async def say(text):
        pass

    assert "parol noto'g'ri" in run(instagram.login("husan", "x", say))
    assert len(UpgradeCl.attempts) == 1


def test_app_versions_from_library(monkeypatch):
    import sys
    from types import SimpleNamespace
    fake = SimpleNamespace(DEFAULT_APP_VERSION="449.0", APP_SETTINGS={"449.0": {}, "448.0": {}, "446.0": {},
                                                                     "428.0": {}, "385.0": {}, "364.0": {}})
    monkeypatch.setitem(sys.modules, "instagrapi", SimpleNamespace(config=fake))
    monkeypatch.setitem(sys.modules, "instagrapi.config", fake)
    versions = instagram.app_versions()
    assert versions[0] == "449.0" and len(versions) == instagram.LOGIN_ATTEMPTS
    assert len(set(versions)) == len(versions)


def test_app_versions_without_library(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_ig(name, *a, **kw):
        if name == "instagrapi":
            raise ImportError("yo'q")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_ig)
    assert instagram.app_versions() == [""]


def test_lib_problem_points_to_main_requirements(monkeypatch):
    monkeypatch.setattr(instagram, "lib_version", lambda: None)
    problem = instagram.lib_problem()
    assert "requirements.txt" in problem and "requirements-instagram" not in problem
