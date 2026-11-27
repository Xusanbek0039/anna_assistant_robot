"""Haftalik chat hisoboti: kim bilan ko'p yozishdingiz, kim xabar o'chirdi/tahrirladi, kim javob kutyapti."""
import html
import logging
import time

import config
import runtime
import schedule
import storage

log = logging.getLogger("weekly")

WEEK = 7 * 86400


def _name(chats: dict, chat_id: int) -> str:
    c = chats.get(chat_id) or {}
    name = c.get("title") or str(chat_id)
    return html.escape(name[:40]) + (f" (@{html.escape(c['username'])})" if c.get("username") else "")


def _ago(ts: float, now: float) -> str:
    hours = int((now - ts) // 3600)
    return f"{hours // 24} kun" if hours >= 48 else f"{hours} soat"


def build(stats: dict, media: dict | None = None, now: float | None = None) -> tuple[str, list[int]]:
    """(HTML matn, javob kutayotgan chat id lari — tugmalar uchun)."""
    now = now or time.time()
    chats = stats.get("chats") or {}
    lines = ["📊 <b>Haftalik chat hisoboti</b> (oxirgi 7 kun)", ""]

    active = stats.get("active") or []
    total_in = sum(r["inc"] for r in active)
    total_out = sum(r["out"] for r in active)
    lines.append(f"💬 Jami: 📥 {total_in} ta kelgan, 📤 {total_out} ta yuborilgan, {len(active)} ta chat")
    if active:
        lines += ["", "<b>Eng faol suhbatlar</b>"]
        for i, r in enumerate(active[:5], 1):
            lines.append(f"{i}. {_name(chats, r['_id'])} — 📥 {r['inc']} / 📤 {r['out']}")

    if stats.get("deleted"):
        lines += ["", "🗑 <b>Xabar o'chirganlar</b>"]
        lines += [f"• {_name(chats, r['_id'])} — {r['n']} ta" for r in stats["deleted"][:5]]
    if stats.get("edited"):
        lines += ["", "✏️ <b>Xabar tahrirlaganlar</b>"]
        lines += [f"• {_name(chats, r['_id'])} — {r['n']} ta" for r in stats["edited"][:5]]

    unanswered = stats.get("unanswered") or []
    if unanswered:
        lines += ["", "⏳ <b>Javob kutayotganlar</b>"]
        for r in unanswered[:5]:
            body = " ".join((r.get("text") or "").split()) or f"[{r.get('media') or 'xabar'}]"
            lines.append(f"• {_name(chats, r['_id'])} — {_ago(r['date'], now)}dan beri: "
                         f"<i>{html.escape(body[:80])}</i>")
    if media:
        waiting = media.get("pending", 0) + media.get("history", 0)
        lines += ["", f"☁️ Drive'dagi media: {media.get('done', 0)} ta yuklangan, {waiting} ta navbatda"]
    return "\n".join(lines), [r["_id"] for r in unanswered[:5]]


async def make() -> tuple[str, list[int]]:
    stats = await storage.archive_week_stats(time.time() - WEEK)
    media = None
    try:
        import gdrive
        if gdrive.enabled():
            media = await storage.archive_media_counts()
    except Exception:
        pass
    return build(stats, media)


async def run(send, slots=lambda: [1]) -> None:
    """send(text, chat_ids) — egasiga hisobot (javob berish tugmalari bilan). Har bir akkauntga alohida."""
    async def job():
        for slot in slots():
            with storage.use_slot(slot):
                text, ids = await make()
                if slot != 1:
                    text = f"📱 <b>{slot}-akkaunt</b>\n" + text
                await send(text, ids)

    await schedule.daily("Haftalik chat hisoboti", config.WEEKLY_REPORT_TIME, "last_weekly", job,
                         weekday=config.WEEKLY_REPORT_DAY, enabled=lambda: runtime.get("weekly_report"))
