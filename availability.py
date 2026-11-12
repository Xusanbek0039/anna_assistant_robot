"""Egasi hozir bandmi? — kotiba javob berishi kerakligini va sababini aniqlaydi.

Rejimlar (nazorat botida /on, /off, /auto):
  on   — doim javob beradi
  off  — hech qachon javob bermaydi (faqat xabardor qiladi)
  auto — faqat band bo'lganda:
         1) kalendarda hozir uchrashuv bo'lsa              -> band ("15:00 gacha")
         2) BUSY_HOURS jadvalidagi vaqt bo'lsa              -> band ("ish vaqti")
         3) egasi oxirgi OWNER_ACTIVE_MINUTES da yozgan bo'lsa -> band EMAS (o'zi javob beradi)
         4) aks holda                                       -> band (telefon yonida emas)
"""
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import calendar_ics
import config
import storage

log = logging.getLogger("availability")

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
        "du": 0, "se": 1, "ch": 2, "pa": 3, "ju": 4, "sh": 5, "ya": 6}

_last_active_mem = 0.0


@dataclass
class Status:
    reply: bool          # kotiba javob bersinmi
    notify: bool         # egasiga xabar yuborilsinmi
    reason: str          # log va /status uchun
    ai_context: str      # AI'ga: egasi hozir qanday holatda (tafsilotlarsiz)


def _parse_rule(rule: str) -> tuple[set[int], int, int]:
    """'Mon-Fri 09:00-18:00' yoki 'Du-Ju 09:00-18:00' -> (kunlar, boshlanish_daqiqa, tugash_daqiqa)."""
    m = re.fullmatch(r"\s*([A-Za-z]+)(?:-([A-Za-z]+))?\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", rule)
    if not m:
        raise ValueError(rule)
    d1, d2 = m.group(1).lower()[:3], (m.group(2) or m.group(1)).lower()[:3]
    d1 = DAYS.get(d1, DAYS.get(d1[:2]))
    d2 = DAYS.get(d2, DAYS.get(d2[:2]))
    if d1 is None or d2 is None:
        raise ValueError(rule)
    days = {(d1 + i) % 7 for i in range(((d2 - d1) % 7) + 1)}
    start = int(m.group(3)) * 60 + int(m.group(4))
    end = int(m.group(5)) * 60 + int(m.group(6))
    return days, start, end


def parse_busy_hours(text: str) -> list[tuple[set[int], int, int]]:
    rules = []
    for part in filter(None, (p.strip() for p in text.split(";"))):
        try:
            rules.append(_parse_rule(part))
        except ValueError:
            log.warning("BUSY_HOURS qoidasi tushunilmadi: %r (namuna: 'Mon-Fri 09:00-18:00')", part)
    return rules


BUSY_RULES = parse_busy_hours(config.BUSY_HOURS)


def _in_busy_hours(now: datetime) -> str | None:
    minute = now.hour * 60 + now.minute
    for days, start, end in BUSY_RULES:
        if start <= end:
            if now.weekday() in days and start <= minute < end:
                return f"{end // 60:02d}:{end % 60:02d}"
        else:  # tunni kesib o'tadigan oraliq, masalan 22:00-07:00
            if (now.weekday() in days and minute >= start) or \
               ((now.weekday() - 1) % 7 in days and minute < end):
                return f"{end // 60:02d}:{end % 60:02d}"
    return None


async def mark_owner_active() -> None:
    """Egasi istalgan chatga o'zi yozganda chaqiriladi."""
    global _last_active_mem
    _last_active_mem = time.time()
    try:
        await storage.set_owner_active(_last_active_mem)
    except Exception:
        pass  # xotirada saqlangani yetarli


async def _owner_active_ago() -> float:
    last = _last_active_mem
    if not last:
        try:
            last = await storage.get_owner_active()
        except Exception:
            last = 0.0
    return time.time() - last if last else float("inf")


async def get_status() -> Status:
    mode = await storage.get_mode()
    name = config.OWNER_CALL_NAME
    if mode == "off":
        return Status(False, True, "avtojavob o'chirilgan (/off)", "")
    if mode == "on":
        busy = await calendar_ics.current_busy(datetime.now(ZoneInfo(config.TIMEZONE)))
        ctx = f"{name} {busy.until:%H:%M} gacha band." if busy else f"{name} hozir band."
        return Status(True, True, "doim yoqilgan (/on)", ctx)

    # --- auto ---
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    busy = await calendar_ics.current_busy(now)
    if busy:
        return Status(True, True, f"kalendarda band ({busy.until:%H:%M} gacha)",
                      f"{name} {busy.until:%H:%M} gacha band (uchrashuvda). "
                      "Uchrashuv tafsilotlarini bilmaysan va so'rashsa ham aytma.")
    until = _in_busy_hours(now)
    if until:
        return Status(True, True, f"ish vaqti ({until} gacha)", f"{name} ish vaqtida, {until} gacha band.")
    ago = await _owner_active_ago()
    if ago < config.OWNER_ACTIVE_MINUTES * 60:
        return Status(False, False, f"egasi faol ({int(ago // 60)} daqiqa oldin yozgan)", "")
    return Status(True, True, "egasi telefon yonida emas", f"{name} hozir telefon yonida emas.")
