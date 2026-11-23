"""⏳ Jonli «yuklanmoqda» ko'rsatkichi: Braille spinner, spoiler ostida qaynaydigan bar, tezlik."""
import asyncio

import pytest
from telethon.tl.types import MessageEntitySpoiler

import control_bot
from conftest import run


class FakeMsg:
    def __init__(self, fail=False, fail_entities=False):
        self.edits: list[tuple[str, dict]] = []
        self.fail, self.fail_entities = fail, fail_entities

    async def edit(self, text, **kw):
        if self.fail:
            raise RuntimeError("Telegram cheklovi")
        if self.fail_entities and kw.get("formatting_entities"):
            raise RuntimeError("ENTITY_BOUNDS_INVALID")
        self.edits.append((text, kw))
        return self

    def spoiler_of(self, index=0) -> str | None:
        """Oxirgi xabardagi spoiler ostidagi matn (UTF-16 offset bo'yicha kesiladi)."""
        text, kw = self.edits[index]
        entities = kw.get("formatting_entities") or []
        if not entities:
            return None
        e = entities[0]
        raw = text.encode("utf-16-le")
        return raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")


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


# ---------- progress bar ----------
@pytest.mark.parametrize("done,full,empty", [
    (0.0, 0, 12),
    (0.5, 6, 6),
    (1.0, 12, 0),
    (1.5, 12, 0),      # chegaradan oshsa ham buzilmaydi
    (-0.2, 0, 12),
])
def test_progress_parts(done, full, empty):
    f, e = control_bot.progress_parts(done)
    assert f == control_bot.BAR_FULL * full and e == control_bot.BAR_EMPTY * empty
    assert len(f) + len(e) == control_bot.BAR_LEN


@pytest.mark.parametrize("size,text", [
    (512, "512 B"), (1024, "1 KB"), (1024 * 700, "700 KB"),
    (1024 * 1024 * 4.25, "4.2 MB"), (0, "0 B"),
])
def test_human_size(size, text):
    assert control_bot.human_size(size) == text


@pytest.mark.parametrize("left,text", [
    (0.2, "~1 s"), (3, "~3 s"), (59, "~59 s"), (60, "~1 daq"), (95, "~1 daq 35 s"),
])
def test_human_left(left, text):
    assert control_bot.human_left(left) == text


# ---------- spoiler: asosiy yangilik ----------
def test_empty_part_is_marked_as_spoiler():
    """Bar ning bo'sh qismi spoiler ostida bo'ladi — Telegram uni o'zi qaynatib turadi."""
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("🎬 video yuklanmoqda", 0.5, done=4 * 1024 * 1024, total=8 * 1024 * 1024)
    run(bar.render())
    text, kw = msg.edits[0]
    entities = kw["formatting_entities"]
    assert len(entities) == 1 and isinstance(entities[0], MessageEntitySpoiler)
    # Offset UTF-16 da hisoblanadi: matnda emoji (2 birlik) bor, shuning uchun bu muhim
    assert msg.spoiler_of() == control_bot.BAR_EMPTY * 6
    assert control_bot.BAR_FULL * 6 in text and "50%" in text


def test_spoiler_offset_correct_with_many_emoji():
    """Matnda ko'p emoji bo'lsa ham spoiler aynan bo'sh qismga tushadi (UTF-16 regressiyasi)."""
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("🎬🖼🎤📤 1/3 video — 🔥 bir martalik", 0.25, done=1000, total=4000)
    run(bar.render())
    assert msg.spoiler_of() == control_bot.BAR_EMPTY * 9


def test_no_spoiler_when_bar_is_full():
    """100% da bo'sh qism yo'q — length=0 entity'ni Telegram rad etadi."""
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("tugadi", 1.0, done=100, total=100)
    run(bar.render())
    assert msg.edits[0][1]["formatting_entities"] is None
    assert control_bot.BAR_EMPTY not in msg.edits[0][0]


def test_whole_bar_is_spoiler_at_zero():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("boshlandi", 0.0)
    run(bar.render())
    assert msg.spoiler_of() == control_bot.BAR_EMPTY * control_bot.BAR_LEN


def test_falls_back_to_plain_text_if_spoiler_rejected():
    """Telegram spoilerni qabul qilmasa — ko'rsatkich oddiy matn bo'lib davom etadi."""
    msg = FakeMsg(fail_entities=True)
    bar = control_bot.Progress(msg)
    bar.set("video", 0.5)
    run(bar.render())
    assert len(msg.edits) == 1 and not msg.edits[0][1].get("formatting_entities")
    bar.set("video", 0.6)
    run(bar.render())
    assert bar._plain and len(msg.edits) == 2        # endi urinib ham ko'rmaydi
    assert msg.edits[1][1].get("formatting_entities") is None


# ---------- spinner ----------
def test_spinner_cycles_through_all_frames():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    seen = []
    for i in range(len(control_bot.SPIN) + 2):
        bar.set(f"qadam {i}")                        # matn o'zgaradi, demak har kadr yuboriladi
        run(bar.render())
        seen.append(msg.edits[-1][0][0])
    assert seen[:len(control_bot.SPIN)] == list(control_bot.SPIN)
    assert seen[len(control_bot.SPIN)] == control_bot.SPIN[0]      # aylanib qaytadi


def test_spinner_advances_even_when_text_unchanged():
    """Matn o'zgarmasa xabar yangilanmaydi, lekin keyingi kadrda spinner boshqa bo'ladi."""
    msg = FakeMsg()
    bar = control_bot.Progress(msg, text="kutilmoqda")
    run(bar.render())                                 # boshlang'ich matn — yuborilmaydi
    assert msg.edits == []
    run(bar.render())
    assert msg.edits and msg.edits[0][0].startswith(control_bot.SPIN[1])


# ---------- tezlik va qolgan vaqt ----------
def test_speed_and_eta_shown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control_bot.time, "monotonic", lambda: clock[0])
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    mb = 1024 * 1024
    bar.set("video", 0.1, done=1 * mb, total=10 * mb)
    clock[0] += 1.0
    bar.set("video", 0.3, done=3 * mb, total=10 * mb)
    run(bar.render())
    line = msg.edits[0][0]
    assert "3.0 MB / 10.0 MB" in line and "2.0 MB/s" in line
    assert "~3 s" in line                             # 7 MB qoldi / 2 MB/s


def test_no_eta_when_total_unknown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control_bot.time, "monotonic", lambda: clock[0])
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("fayl", None, done=1024 * 1024)
    clock[0] += 1.0
    bar.set("fayl", None, done=3 * 1024 * 1024)
    run(bar.render())
    text = msg.edits[0][0]
    assert "%" not in text and control_bot.BAR_FULL not in text   # hajm noma'lum — bar yo'q


def test_speed_is_smoothed(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(control_bot.time, "monotonic", lambda: clock[0])
    bar = control_bot.Progress(FakeMsg())
    bar.set("x", 0.1, done=1_000_000, total=10_000_000)
    clock[0] += 1.0
    bar.set("x", 0.2, done=2_000_000, total=10_000_000)
    first = bar.speed
    clock[0] += 1.0
    bar.set("x", 0.9, done=9_000_000, total=10_000_000)   # keskin sakrash
    assert first < bar.speed < 7_000_000                  # o'rtacha olinadi, sakramaydi


def test_huge_eta_hidden(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(control_bot.time, "monotonic", lambda: clock[0])
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("x", 0.0, done=1024, total=10 * 1024 * 1024 * 1024)
    clock[0] += 1.0
    bar.set("x", 0.0, done=2048, total=10 * 1024 * 1024 * 1024)
    run(bar.render())
    assert "~" not in msg.edits[0][0]                     # 10 GB / 1 KB/s — ma'nosiz, ko'rsatilmaydi


# ---------- umumiy xatti-harakat ----------
def test_no_bar_when_size_unknown():
    msg = FakeMsg()
    bar = control_bot.Progress(msg)
    bar.set("Ma'lumot olinmoqda...")
    run(bar.render())
    text = msg.edits[0][0]
    assert "%" not in text and control_bot.BAR_FULL not in text and "Ma'lumot" in text


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


def test_first_update_is_immediate(monkeypatch):
    """Tez tugagan ishda ham ko'rsatkich ko'rinadi (oldin birinchi yangilanish kechikardi)."""
    monkeypatch.setattr(control_bot, "PROGRESS_EVERY", 5)      # keyingisi yetib kelmaydi
    msg = FakeMsg()

    async def scenario():
        async with control_bot.Progress(msg, text="boshlandi") as bar:
            bar.set("🎬 video yuklanmoqda", 0.25, done=2 * 1024 * 1024, total=8 * 1024 * 1024)
            await asyncio.sleep(0.02)                          # ish 20 ms da tugadi

    run(scenario())
    assert msg.edits, "ko'rsatkich umuman yangilanmadi"
    assert "25%" in msg.edits[0][0] and "video yuklanmoqda" in msg.edits[0][0]
    assert msg.spoiler_of() == control_bot.BAR_EMPTY * 9


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
