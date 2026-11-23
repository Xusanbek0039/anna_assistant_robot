"""🎨 HTML widget ko'rsatkichi: Telegram xabar ichida CSS animatsiya, holat chat_data orqali."""
import asyncio
import json
import zlib

import pytest
from telethon.tl.functions.messages import EditMessageWidgetDataRequest, SendMediaRequest
from telethon.tl.types import InputMediaWidget

import progress
from conftest import run


class FakeMsg:
    def __init__(self, mid=11, fail_delete=False):
        self.id, self.fail_delete = mid, fail_delete
        self.deleted = False
        self.edits: list = []

    async def delete(self):
        if self.fail_delete:
            raise RuntimeError("o'chirib bo'lmadi")
        self.deleted = True

    async def edit(self, text, **kw):
        self.edits.append((text, kw))


class FakeClient:
    """SendMediaRequest / EditMessageWidgetDataRequest ni yozib oladi."""

    def __init__(self, send_fails=False, edit_fails=False, no_message=False):
        self.sends: list[InputMediaWidget] = []
        self.edits: list[EditMessageWidgetDataRequest] = []
        self.send_fails, self.edit_fails, self.no_message = send_fails, edit_fails, no_message
        self.msg = FakeMsg()
        self.actions: list = []

    async def get_input_entity(self, chat):
        return f"peer:{chat}"

    async def __call__(self, req):
        if isinstance(req, SendMediaRequest):
            if self.send_fails:
                raise RuntimeError("WIDGET_INVALID")
            self.sends.append(req.media)
            return "updates"
        if isinstance(req, EditMessageWidgetDataRequest):
            if self.edit_fails:
                raise RuntimeError("FLOOD_WAIT")
            self.edits.append(req)
            return "ok"
        raise AssertionError(f"kutilmagan so'rov: {type(req).__name__}")

    def _get_response_message(self, req, result, peer):
        return None if self.no_message else self.msg

    def action(self, chat, what):
        self.actions.append((chat, what))

        class Act:
            async def __aenter__(self_):
                return None

            async def __aexit__(self_, *exc):
                return None

        return Act()


class FakeEvent:
    def __init__(self):
        self.sent: list = []

    async def respond(self, text, **kw):
        self.sent.append((text, kw))
        return FakeMsg(mid=99)


@pytest.fixture(autouse=True)
def fresh():
    progress.reset_widgets()
    yield
    progress.reset_widgets()


def last_data(client) -> dict:
    return json.loads(client.edits[-1].chat_data)


# ---------- Telegram cheklovlari (regressiya) ----------
def test_widget_html_fits_telegram_limits():
    raw = progress.WIDGET_HTML.encode("utf-8")
    assert len(raw) <= 32768, "HTML 32 KB dan oshdi"
    assert len(zlib.compress(raw, 9)) <= 4096, "zlib bilan 4 KB dan oshdi"
    assert b"\x00" not in raw, "NUL belgi bo'lmasligi kerak"


def test_widget_html_avoids_unsupported_features():
    """Widget ichida ishlamaydigan narsalar ishlatilmasligi kerak (hujjat bo'yicha)."""
    html = progress.WIDGET_HTML
    for bad in ("vh", "overflow:auto", "overflow: auto", "position:sticky", "clip-path",
                "@font-face", "@import", ":has(", "<iframe", "<video", "<audio", "<input",
                "fetch(", "XMLHttpRequest", "inset:"):
        assert bad not in html, f"qo'llab-quvvatlanmaydi: {bad}"
    assert "@keyframes" in html and "transition:" in html      # animatsiya bor
    assert "TelegramWidget" in html and "chatDataChanged" in html


@pytest.mark.parametrize("text,fraction,size,extra", [
    ("🎬 video yuklanmoqda", 0.52, "6.2 / 12.0 MB", "4.2 MB/s · ~2 s"),
    ("3/10 🖼 rasm yuklanmoqda — juda uzun nom " * 3, 1.0, "999.9 MB / 999.9 MB",
     "123.4 MB/s · ~59 daq 59 s"),
    ("qisqa", None, "", ""),
])
def test_chat_data_fits_limit(text, fraction, size, extra):
    data = progress.widget_data(text, fraction, size, extra)
    assert len(data.encode()) <= progress.CHAT_DATA_MAX
    assert json.loads(data)["t"]                               # matn baribir bo'ladi


def test_chat_data_shape():
    data = json.loads(progress.widget_data("🎬 video", 0.523, "6.2 MB", "4 MB/s"))
    assert data == {"t": "🎬 video", "p": 52, "z": "6.2 MB", "e": "4 MB/s"}
    no_pct = json.loads(progress.widget_data("boshlandi", None))
    assert no_pct == {"t": "boshlandi"} and "p" not in no_pct   # hajm noma'lum — foiz yo'q


@pytest.mark.parametrize("fraction,pct", [(0.0, 0), (0.014, 1), (0.999, 100), (1.0, 100),
                                          (1.4, 100), (-0.3, 0)])
def test_percent_always_in_range(fraction, pct):
    assert json.loads(progress.widget_data("x", fraction))["p"] == pct


# ---------- widget yuborish ----------
def test_widget_is_sent_and_used():
    client, event = FakeClient(), FakeEvent()
    bar = run(progress.open_progress(client, 7, event, "Instagram'dan olinmoqda..."))
    assert bar.widget is True and progress.widgets_state() is True
    assert event.sent == []                                    # matnli xabar yuborilmadi
    media = client.sends[0]
    assert isinstance(media, InputMediaWidget)
    assert media.html == progress.WIDGET_HTML
    assert (media.width, media.height) == (0, 0)               # avtomatik o'lcham
    assert json.loads(media.chat_data)["t"] == "Instagram'dan olinmoqda..."


def test_state_is_sent_through_chat_data():
    client, event = FakeClient(), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    mb = 1024 * 1024
    bar.set("🎬 video yuklanmoqda", 0.5, done=6 * mb, total=12 * mb)
    run(bar.render())
    assert len(client.edits) == 1
    req = client.edits[0]
    assert req.id == client.msg.id and req.peer == "peer:7"
    data = json.loads(req.chat_data)
    assert data["p"] == 50 and data["t"] == "🎬 video yuklanmoqda"
    assert data["z"] == "6.0 MB / 12.0 MB"


def test_unchanged_state_not_re_sent():
    client, event = FakeClient(), FakeEvent()
    bar = run(progress.open_progress(client, 7, event, "Yuklanmoqda..."))
    run(bar.render())                      # boshlang'ich holat allaqachon yuborilgan
    assert client.edits == []
    bar.set("Yuklanmoqda...", 0.4)
    run(bar.render())
    run(bar.render())                      # o'zgarmadi
    assert len(client.edits) == 1


def test_widget_mode_ignores_spinner():
    """Widget rejimida matn qurilmaydi — animatsiyani CSS qiladi, spinner kerak emas."""
    client, event = FakeClient(), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    bar.set("video", 0.3)
    run(bar.render())
    run(bar.render())
    assert len(client.edits) == 1          # spinner o'zgarmaydi -> qayta yubormaydi
    assert progress.SPIN[0] not in client.edits[0].chat_data


def test_edit_failure_is_ignored():
    client, event = FakeClient(edit_fails=True), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    bar.set("video", 0.3)
    run(bar.render())                      # xato chiqmasligi kerak
    assert client.edits == []


# ---------- zaxira: matnli rejim ----------
def test_falls_back_to_text_when_widget_rejected():
    client, event = FakeClient(send_fails=True), FakeEvent()
    bar = run(progress.open_progress(client, 7, event, "Saqlanmoqda..."))
    assert bar.widget is False and progress.widgets_state() is False
    assert len(event.sent) == 1 and "Saqlanmoqda..." in event.sent[0][0]
    assert event.sent[0][0].startswith(progress.SPIN[0])


def test_widget_not_retried_after_rejection():
    """Bir marta rad etilgach boshqa urinilmaydi — har safar xato kutib o'tirmaslik uchun."""
    client, event = FakeClient(send_fails=True), FakeEvent()
    run(progress.open_progress(client, 7, event))
    ok_client = FakeClient()
    bar = run(progress.open_progress(ok_client, 7, event))
    assert bar.widget is False and ok_client.sends == []


def test_falls_back_when_message_not_returned():
    client, event = FakeClient(no_message=True), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    assert bar.widget is False and len(event.sent) == 1


def test_text_mode_used_without_client():
    event = FakeEvent()
    bar = run(progress.open_progress(None, None, event, "Yuklanmoqda..."))
    assert bar.widget is False and len(event.sent) == 1


# ---------- yakun ----------
def test_finish_deletes_widget_and_sends_result():
    client, event = FakeClient(), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    run(bar.finish("✅ Yuborildi (3 ta fayl)", event))
    assert client.msg.deleted is True
    assert event.sent[-1][0] == "✅ Yuborildi (3 ta fayl)"
    assert event.sent[-1][1]["parse_mode"] == "html"


def test_finish_sends_result_even_if_delete_fails():
    client, event = FakeClient(), FakeEvent()
    client.msg = FakeMsg(fail_delete=True)
    bar = run(progress.open_progress(client, 7, event))
    run(bar.finish("✅ Natija", event))
    assert event.sent[-1][0] == "✅ Natija"


def test_finish_uses_send_long_for_long_result():
    client, event = FakeClient(), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    calls = []

    async def send_long(send, text, **kw):
        calls.append(text)

    run(bar.finish("✅ " + "x" * 5000, event, send_long))
    assert len(calls) == 1 and calls[0].startswith("✅")


def test_text_mode_finish_edits_message():
    client, event = FakeClient(send_fails=True), FakeEvent()
    bar = run(progress.open_progress(client, 7, event))
    run(bar.finish("✅ Tayyor", event))
    assert bar.msg.edits[-1][0] == "✅ Tayyor"       # matn rejimida xabar tahrirlanadi
    assert not bar.msg.deleted


# ---------- umumiy ----------
def test_widget_loop_is_slower_than_text(monkeypatch):
    """Widget rejimida kamroq yangilanadi — CSS o'zi animatsiya qiladi."""
    assert progress.WIDGET_EVERY > progress.PROGRESS_EVERY
    client, event = FakeClient(), FakeEvent()
    waits = []
    real = asyncio.sleep

    async def fake(sec, *a, **kw):
        waits.append(sec)
        if len(waits) >= 2:
            raise asyncio.CancelledError
        return await real(0)

    monkeypatch.setattr(progress.asyncio, "sleep", fake)
    bar = run(progress.open_progress(client, 7, event))

    async def scenario():
        async with bar:
            await real(0.01)

    try:
        run(scenario())
    except asyncio.CancelledError:
        pass
    assert waits and waits[0] == progress.WIDGET_EVERY


def test_telegram_action_still_shown():
    client, event = FakeClient(), FakeEvent()

    async def scenario():
        bar = await progress.open_progress(client, 7, event, action="document")
        async with bar:
            pass

    run(scenario())
    assert client.actions == [(7, "document")]
