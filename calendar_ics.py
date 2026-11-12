"""Google Calendar (maxfiy iCal havolasi) orqali "hozir bandmi, qachongacha" ni aniqlash.

Uchrashuv nomi, joyi va ishtirokchilari hech qachon tashqariga chiqmaydi —
faqat "band / qachongacha" ishlatiladi.
"""
import asyncio
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("calendar")

CACHE_SECONDS = 300
LOOKAHEAD = timedelta(hours=24)


@dataclass
class Busy:
    until: datetime  # hozirgi band vaqt qachon tugaydi (ketma-ket uchrashuvlar birlashtiriladi)


_cache: tuple[float, list[tuple[datetime, datetime]]] | None = None
_warned = False


def _fetch_sync() -> bytes:
    req = urllib.request.Request(config.CALENDAR_ICS_URL, headers={"User-Agent": "anna-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _parse(raw: bytes, now: datetime) -> list[tuple[datetime, datetime]]:
    import icalendar
    import recurring_ical_events

    tz = ZoneInfo(config.TIMEZONE)
    cal = icalendar.Calendar.from_ical(raw)
    spans = []
    for ev in recurring_ical_events.of(cal).between(now - timedelta(hours=12), now + LOOKAHEAD):
        start, end = ev.get("DTSTART"), ev.get("DTEND")
        if start is None:
            continue
        start = start.dt
        # Butun kunlik tadbirlar (tug'ilgan kun, bayram) bandlik hisoblanmaydi
        if isinstance(start, date) and not isinstance(start, datetime):
            continue
        if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":  # "Bo'sh" deb belgilangan
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        end = end.dt if end is not None else start + timedelta(hours=1)
        start = start if start.tzinfo else start.replace(tzinfo=tz)
        end = end if end.tzinfo else end.replace(tzinfo=tz)
        spans.append((start.astimezone(tz), end.astimezone(tz)))
    spans.sort()
    return spans


async def _spans(now: datetime) -> list[tuple[datetime, datetime]] | None:
    global _cache, _warned
    if _cache and time.time() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        raw = await asyncio.to_thread(_fetch_sync)
        spans = await asyncio.to_thread(_parse, raw, now)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if not _warned:
            log.warning("Kalendar o'qilmadi (%s) — kalendarsiz davom etaman", type(e).__name__)
            _warned = True
        return _cache[1] if _cache else None
    except Exception as e:  # buzilgan ICS
        log.warning("Kalendar formati tushunilmadi (%s)", type(e).__name__)
        return _cache[1] if _cache else None
    if _warned:
        log.info("Kalendar yana o'qilyapti")
    _warned = False
    _cache = (time.time(), spans)
    return spans


async def current_busy(now: datetime) -> Busy | None:
    """Hozir kalendarda band bo'lsa — qachongacha (ketma-ket uchrashuvlar birlashtiriladi)."""
    if not config.CALENDAR_ICS_URL:
        return None
    spans = await _spans(now)
    if not spans:
        return None
    until = None
    for start, end in spans:
        if until is None:
            if start <= now < end:
                until = end
        elif start <= until + timedelta(minutes=5):  # oraliq 5 daqiqadan kam — davomi deb hisoblanadi
            until = max(until, end)
    return Busy(until) if until else None
