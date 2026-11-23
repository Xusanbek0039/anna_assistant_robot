"""⏳ Jonli «yuklanmoqda» ko'rsatkichi: qum soati aylanadi, progress bar to'ladi, Telegram holati ko'rinadi."""
import asyncio

import pytest

import control_bot
from conftest import run


class FakeMsg:
    def __init__(self, fail=False):
        self.edits: list[tuple[str, dict]] = []
        self.fail = fail

    async def edit(self, text, **kw):
        if self.fail:
            raise RuntimeError("Telegram cheklovi")
        self.edits.append((text, kw))
        return self


class FakeClient:
    """client.action(...) kontekst menejerini soxta qiladi."""

    def __init__(self, fail=False):
        self.actions: list[tuple[int, str]] = []
        self.entered = self.exited = 0
        self.fail = fail

    def action(self, chat, what):
        self.actions.append((chat, what))
        client = self

        class Act:
            async def __aenter__(self):
                if client.fail:
                    raise RuntimeError("action qo'llanmadi")
                client.entered += 1

            async def __aexit__(self, *exc):
                client.exited += 1

        return Act()


@pytest.mark.parametrize("done,bar", [
    (0.0, "▱▱▱▱▱▱▱▱▱▱▱▱"),
    (0.5, "▰▰▰▰▰▰▱▱▱▱▱▱"),
    (1.0, "▰▰▰▰▰▰▰▰▰▰▰▰"),
    (1.5, "▰▰▰▰▰▰▰▰▰▰▰▰"),      # chegaradan oshsa ham buzilmaydi
    (-0.2, "▱▱▱▱▱▱▱▱▱▱▱▱"),
])
def test_progress_bar(done, bar):
    assert control_bot.progress_bar(done) == bar
    assert len(control_bot.progress_bar(done)) == control_bot.BAR_LEN


def test_spinner_alternates_and_shows_percent():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("Yuklanmoqda — 1.0 MB / 4.0 MB", 0.25)
    run(bar.render())
    run(bar.render())
    assert len(msg.edits) == 2
    first, second = msg.edits[0][0], msg.edits[1][0]
    assert first.startswith("⏳") and second.startswith("⌛")      # qum soati aylanadi
    assert "▰▰▰▱▱▱▱▱▱▱▱▱  25%" in first and "1.0 MB / 4.0 MB" in first


def test_no_percent_when_size_unknown():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("Ma'lumot olinmoqda...")
    run(bar.render())
    text = msg.edits[0][0]
    assert "%" not in text and "▰" not in text and "Ma'lumot olinmoqda" in text


def test_identical_text_not_re_sent():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("bir xil", 0.5)
    run(bar.render())
    run(bar.render())        # matn o'zgardi (spinner), demak yuboriladi
    assert len(msg.edits) == 2
    bar._tick = 0            # spinner ham bir xil bo'lsa — qayta yuborilmaydi
    bar._shown = msg.edits[0][0]
    run(bar.render())
    assert len(msg.edits) == 2


def test_edit_failure_is_ignored():
    bar = control_bot.Progress(FakeMsg(fail=True))
    bar.set("matn", 0.3)
    run(bar.render())        # xato chiqmasligi kerak


def test_context_manager_sets_and_clears_telegram_action():
    client = FakeClient()
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg, client, 42, "document") as bar:
            bar.set("ishlayapti", 0.1)
            await bar.render()
        return True

    assert run(scenario())
    assert client.actions == [(42, "document")] and client.entered == 1 and client.exited == 1
    assert msg.edits and "ishlayapti" in msg.edits[0][0]


def test_works_without_client():
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg) as bar:
            bar.set("faqat xabar")
            await bar.render()

    run(scenario())
    assert "faqat xabar" in msg.edits[0][0]


def test_failing_action_does_not_break_progress():
    client = FakeClient(fail=True)
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg, client, 1) as bar:
            bar.set("davom etadi")
            await bar.render()

    run(scenario())
    assert client.entered == 0 and "davom etadi" in msg.edits[0][0]


def test_background_loop_updates_without_calls(monkeypatch):
    """Hech kim render chaqirmasa ham ko'rsatkich o'zi jonlanib turadi."""
    monkeypatch.setattr(control_bot, "PROGRESS_EVERY", 0.01)
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg) as bar:
            bar.set("kutilmoqda")
            await asyncio.sleep(0.05)

    run(scenario())
    assert len(msg.edits) >= 2
    assert msg.edits[0][0] != msg.edits[1][0]      # spinner almashdi


def test_finish_replaces_message_with_result():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    run(bar.finish("✅ Yuborildi (3 ta fayl)"))
    text, kw = msg.edits[-1]
    assert text.startswith("✅") and kw["parse_mode"] == "html" and kw["link_preview"] is False


def test_finish_falls_back_to_new_message():
    sent = []

    class Ev:
        async def respond(self, text, **kw):
            sent.append(text)

    bar = control_bot.Progress(FakeMsg(fail=True))
    run(bar.finish("✅ Natija", Ev()))
    assert sent == ["✅ Natija"]


def test_first_update_is_immediate(monkeypatch):
    """Tez tugagan ishda ham ko'rsatkich ko'rinadi (oldin birinchi yangilanish kechikib, umuman
    ko'rinmay qolardi — foydalanuvchi faqat qotib qolgan «⏳ ...» matnini ko'rardi)."""
    monkeypatch.setattr(control_bot, "PROGRESS_EVERY", 5)      # keyingisi hech qachon yetib kelmaydi
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg, text="boshlandi") as bar:
            bar.set("🎬 video yuklanmoqda — 2.0 MB / 8.0 MB", 0.25)
            await asyncio.sleep(0.02)                          # ish 20 ms da tugadi

    run(scenario())
    assert msg.edits, "ko'rsatkich umuman yangilanmadi"
    assert "25%" in msg.edits[0][0] and "video yuklanmoqda" in msg.edits[0][0]


def test_initial_text_not_re_sent(monkeypatch):
    """Xabar allaqachon shu matn bilan yuborilgan — behuda tahrirlanmaydi."""
    monkeypatch.setattr(control_bot, "PROGRESS_EVERY", 5)
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg, text="Saqlanmoqda...") as bar:
            await asyncio.sleep(0.02)
            assert bar.text == "Saqlanmoqda..."

    run(scenario())
    assert msg.edits == []
