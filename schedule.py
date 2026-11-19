"""Har kuni (yoki haftaning bir kunida) belgilangan vaqtda bajariladigan vazifalar uchun umumiy sikl."""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import report
import storage

log = logging.getLogger("schedule")


def now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


async def daily(name: str, time_text: str, state_key: str, job, *, weekday: int | None = None,
                enabled=lambda: True) -> None:
    """Har kuni `time_text` (HH:MM, Toshkent vaqti) da `job()` ni bajaradi. weekday (1=Du ... 7=Ya) berilsa —
    faqat o'sha kuni. `state_key` — bir kunda ikki marta bajarilmasligi uchun (qayta ishga tushganda ham).
    Xato siklni to'xtatmaydi."""
    hh, mm = report._parse_time(time_text)
    log.info("%s: har %s %02d:%02d da", name, f"haftaning {weekday}-kuni" if weekday else "kuni", hh, mm)
    while True:
        await asyncio.sleep(report.seconds_until(now(), hh, mm))
        today = now().date()
        if (weekday and today.isoweekday() != weekday) or not enabled():
            await asyncio.sleep(60)
            continue
        key = today.isoformat()
        try:
            st = await storage.get_runtime()
            if st.get(state_key) != key:
                await job()
                await storage.set_runtime(**{state_key: key})
                log.info("%s bajarildi", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s bajarilmadi", name)
        await asyncio.sleep(60)
