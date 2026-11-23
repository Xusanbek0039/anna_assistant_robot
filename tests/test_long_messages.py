"""Uzun xabarlar: Telegram 4096 belgi cheklovi (MessageTooLongError) sabab /help ishlamay qolgan edi."""
import pytest
from telethon import errors

import control_bot
from conftest import run

LIMIT = control_bot.TG_LIMIT


def test_tg_len_counts_utf16():
    assert control_bot.tg_len("abc") == 3
    assert control_bot.tg_len("🔥") == 2            # emoji — 2 birlik (Telegram shunday hisoblaydi)
    assert control_bot.tg_len("o'zbek") == 6


def test_help_is_long_but_fits_in_parts():
    """HELP matni Telegram limitidan uzun — bo'laklab yuborilishi kerak, aks holda /help javob bermaydi."""
    assert control_bot.tg_len(control_bot.HELP) > LIMIT
    parts = control_bot.split_message(control_bot.HELP)
    assert len(parts) > 1
    for p in parts:
        assert control_bot.tg_len(p) <= LIMIT
        assert control_bot._tags_balanced(p), p[:120]
    joined = "\n\n".join(parts)
    assert joined.replace("\n", "") == control_bot.HELP.replace("\n", "")   # hech narsa yo'qolmadi
    for cmd in ("/auto", "/ig", "/korganlar", "/eslatma", "/fin", "/arxiv"):
        assert cmd in joined


def test_short_text_not_split():
    assert control_bot.split_message("salom") == ["salom"]
    assert control_bot.split_message("") == [""]


def test_split_sections_keep_tags_balanced():
    text = "\n\n".join(f"<b>Bo'lim {i}</b>\n" + "x" * 1000 for i in range(6))
    parts = control_bot.split_message(text)
    assert len(parts) > 1
    for p in parts:
        assert control_bot.tg_len(p) <= LIMIT and control_bot._tags_balanced(p)


def test_split_single_huge_block_by_lines():
    parts = control_bot.split_message("qator\n" * 2000)
    assert len(parts) > 1
    for p in parts:
        assert control_bot.tg_len(p) <= LIMIT
    assert sum(p.count("qator") for p in parts) == 2000


def test_split_huge_single_line_with_emoji():
    parts = control_bot.split_message("🔥" * 5000)
    assert len(parts) > 1
    for p in parts:
        assert control_bot.tg_len(p) <= LIMIT          # emoji 2 birlik — yarmicha sig'adi
    assert sum(p.count("🔥") for p in parts) == 5000


def test_unbalanced_chunk_merged_with_next():
    parts = control_bot.split_message("<b>ochildi\n\nyopildi</b>", limit=12)
    assert all(control_bot._tags_balanced(p) for p in parts)


def test_tags_balanced_helper():
    assert control_bot._tags_balanced("<b>a</b> <i>b</i>")
    assert not control_bot._tags_balanced("<b>a")
    assert not control_bot._tags_balanced("a</b>")
    assert control_bot._tags_balanced('<a href="x">link</a>')


def test_send_long_buttons_only_on_last_part():
    sent = []

    async def send(text, **kw):
        sent.append((text, kw))

    run(control_bot.send_long(send, "a\n\n" + "x" * 5000, parse_mode="html", buttons=["KB"]))
    assert len(sent) > 1
    assert all(kw.get("buttons") is None for _, kw in sent[:-1])
    assert sent[-1][1]["buttons"] == ["KB"]
    assert all(kw["parse_mode"] == "html" for _, kw in sent)


def test_send_long_plain_fallback_on_entity_error():
    calls = []

    class BadEntity(errors.RPCError):
        def __init__(self):
            pass

        def __str__(self):
            return "ENTITY_BOUNDS_INVALID"

    async def send(text, **kw):
        calls.append((text, kw))
        if len(calls) == 1:
            raise BadEntity()

    run(control_bot.send_long(send, "<b>salom</b>", parse_mode="html"))
    assert len(calls) == 2 and calls[1][0] == "salom" and calls[1][1]["parse_mode"] is None


def test_send_long_reraises_other_rpc_errors():
    class Other(errors.RPCError):
        def __init__(self):
            pass

        def __str__(self):
            return "CHAT_WRITE_FORBIDDEN"

    async def send(text, **kw):
        raise Other()

    with pytest.raises(errors.RPCError):
        run(control_bot.send_long(send, "salom"))


def test_send_html_splits_long_report(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, chat, text, **kw):
            sent.append(text)

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    run(control_bot.send_html("<b>Hisobot</b>\n\n" + "qator\n" * 2000))
    assert len(sent) > 1 and all(control_bot.tg_len(t) <= LIMIT for t in sent)


def test_alert_splits_long_text(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, chat, text, **kw):
            sent.append(text)

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    run(control_bot.alert("⚠️ " + "sabab " * 1200))
    assert len(sent) > 1 and all(control_bot.tg_len(t) <= LIMIT for t in sent)


def test_instagram_unsent_alert_splits(monkeypatch):
    """«Qaytarib olingan xabarlar» ro'yxati uzun bo'lsa ham yuboriladi va tugmalar oxirgi bo'lakda."""
    import instagram
    sent = []

    class Msg:
        id = 7

    class Bot:
        async def send_message(self, chat, text, **kw):
            sent.append((text, kw))
            return Msg()

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    monkeypatch.setattr(control_bot, "_ig_notif_map", {})
    docs = [{"sender": "Ali Valiyev", "username": "ali", "date": 1_700_000_000, "text": "x" * 250}
            for _ in range(12)]
    th = instagram.Thread("t1", "Ali Valiyev", False, False)
    run(control_bot.ig_notify("t1", instagram.unsent_text(th, docs)))
    assert all(control_bot.tg_len(t) <= LIMIT for t, _ in sent)
    assert all(kw.get("buttons") is None for _, kw in sent[:-1])
    assert sent[-1][1]["buttons"] and control_bot._ig_notif_map == {7: "t1"}


def test_story_viewers_alert_splits(monkeypatch):
    """60 kishilik storis ro'yxati ham sig'adi (tugmalar oxirgi bo'lakda)."""
    sent = []

    class Msg:
        id = 9

    class Bot:
        async def send_message(self, chat, text, **kw):
            sent.append((text, kw))
            return Msg()

    monkeypatch.setattr(control_bot.ctx, "bot", Bot(), raising=False)
    monkeypatch.setattr(control_bot.ctx, "owner_id", 1, raising=False)
    monkeypatch.setattr(control_bot, "_notif_map", {})
    long_list = "👀 <b>Ro'yxat</b>\n\n" + "\n".join(
        f"{i}. <b>Juda Uzun Ism Familiya {i}</b> @username{i} · <code>12345678{i}</code>" for i in range(60))
    run(control_bot.watch_alert(long_list, [[("💬 Lichka", "wch:dm:42")]]))
    assert len(sent) >= 1 and all(control_bot.tg_len(t) <= LIMIT for t, _ in sent)
    assert sent[-1][1].get("buttons") and control_bot._notif_map == {9: 42}
