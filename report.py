"""Kunlik hisobot: har kuni REPORT_TIME da nazorat botiga bugungi xulosa."""
import asyncio
import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import storage

log = logging.getLogger("report")

MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
          "sentabr", "oktabr", "noyabr", "dekabr"]


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def build_report(stats: dict, pending: list[dict], day: datetime) -> str:
    """Hisobot matni (HTML). Sof funksiya — test qilish oson."""
    people = len(stats.get("chats") or [])
    lines = [f"📊 <b>Bugungi hisobot</b> — {day.day}-{MONTHS[day.month - 1]}", ""]
    if not people:
        lines.append("Bugun hech kim yozmadi. 🌙")
    else:
        lines.append(f"👥 Yozganlar: <b>{people}</b> kishi ({stats.get('messages', 0)} ta xabar)")
        extra = []
        if stats.get("urgent"):
            extra.append(f"🔴 muhim: {stats['urgent']}")
        if stats.get("calls"):
            extra.append(f"📞 chaqirishgan: {stats['calls']}")
        if stats.get("spam"):
            extra.append(f"🚫 spam: {stats['spam']}")
        if extra:
            lines.append(" | ".join(extra))

    ai_req = stats.get("ai_requests", 0)
    if ai_req:
        parts = [f"{stats.get('ai_chat', 0)} javob"]
        if stats.get("ai_voice"):
            parts.append(f"{stats['ai_voice']} ovoz")
        if stats.get("ai_image"):
            parts.append(f"{stats['ai_image']} rasm")
        if stats.get("ai_tts"):
            parts.append(f"{stats['ai_tts']} ovozli javob")
        limit = f" / {config.AI_DAILY_LIMIT}" if config.AI_DAILY_LIMIT else ""
        lines.append(f"🤖 AI: {ai_req}{limit} so'rov ({', '.join(parts)}), "
                     f"~{_fmt_tokens(stats.get('ai_tokens', 0))} token")

    if pending:
        lines += ["", f"📬 <b>Javob kutayotganlar: {len(pending)}</b>"]
        for r in pending[:7]:
            mark = "🔴 " if r.get("urgent") else "• "
            about = r.get("summary") or r.get("last_text") or ""
            lines.append(f"{mark}<b>{html.escape(r.get('name') or 'Nomaʼlum')}</b>: "
                         f"{html.escape(about[:120])}")
        if len(pending) > 7:
            lines.append(f"… va yana {len(pending) - 7} ta (/inbox)")
    elif people:
        lines += ["", "✅ Hammasiga javob berilgan."]
    return "\n".join(lines)


async def make_report() -> str:
    tz = ZoneInfo(config.TIMEZONE)
    stats = await storage.get_stats()
    pending = [r for r in await storage.pending_inbox() if r.get("summary") != "🚫 spam"]
    return build_report(stats, pending, datetime.now(tz))


def _parse_time(text: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in text.split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    log.warning("REPORT_TIME noto'g'ri (%r) — 21:00 ishlatiladi", text)
    return 21, 0


def seconds_until(now: datetime, hh: int, mm: int) -> float:
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run(send) -> None:
    """send(text) — egasiga HTML xabar yuboradigan coroutine. Hech qachon istisno chiqarmaydi."""
    tz = ZoneInfo(config.TIMEZONE)
    hh, mm = _parse_time(config.REPORT_TIME)
    log.info("Kunlik hisobot har kuni %02d:%02d da yuboriladi", hh, mm)
    while True:
        await asyncio.sleep(seconds_until(datetime.now(tz), hh, mm))
        today = storage.today_key()
        try:
            st = await storage.get_runtime()
            if st.get("last_report") == today:  # qayta ishga tushganda ikki marta yubormaslik
                continue
            await send(await make_report())
            await storage.set_runtime(last_report=today)
            log.info("Kunlik hisobot yuborildi")
        except Exception as e:
            log.warning("Kunlik hisobot yuborilmadi (%s)", type(e).__name__)
        await asyncio.sleep(60)
