"""AI chat xulosasi va va'dalar kuzatuvi.

- summarize(chat_id, days) — arxivdagi yozishmadan xulosa, mavzular, va'dalar, ochiq savollar.
- Xulosada topilgan SIZ bergan va'dalar ro'yxatga yoziladi (takrorlanmaydi).
- Har kuni kechqurun bugun yozishilgan chatlardan yangi va'dalar topiladi, ertalab esa muddati kelganlari eslatiladi.
"""
import logging
import time
from datetime import timedelta

import ai
import archive
import config
import runtime
import schedule
import storage
import usage

log = logging.getLogger("promises")


class NoAI(Exception):
    """AI ishlatib bo'lmaydi (kalit yo'q yoki kunlik limit tugagan)."""


def today() -> str:
    return schedule.now().date().isoformat()


def tomorrow() -> str:
    return (schedule.now().date() + timedelta(days=1)).isoformat()


async def summarize(chat_id: int, days: int | None) -> tuple[dict, dict, int]:
    """(xulosa, chat ma'lumoti, xabarlar soni). Xabar bo'lmasa — xulosa {} bo'ladi."""
    lines, info, n = await archive.chat_lines(chat_id, days)
    if not lines:
        return {}, info, 0
    ok, reason = await usage.owner_ok()
    if not ok:
        raise NoAI(reason)
    result = await ai.summarize_chat(lines, info.get("title") or "", today())
    await usage.record_owner("summary", result.get("tokens", 0))
    return result, info, n


async def save_promises(chat_id: int, title: str, items: list[dict], source: str) -> list[dict]:
    """Faqat egasining («men») va'dalari yoziladi. Yangi qo'shilganlarini qaytaradi."""
    added = []
    for p in items:
        if p.get("who") != "men":
            continue
        pid = await storage.promise_add(chat_id, title, p["text"], p.get("due"), source)
        if pid:
            added.append({**p, "id": pid, "chat_id": chat_id, "title": title})
    return added


async def scan_recent(hours: int = 24) -> list[dict]:
    """Oxirgi `hours` soatda yozishilgan chatlardan yangi va'dalarni topadi. AI limiti tugasa — to'xtaydi."""
    since = time.time() - hours * 3600
    chats = await storage.archive_active_chats(since, config.PROMISE_SCAN_CHATS)
    found: list[dict] = []
    for c in chats:
        try:
            result, info, _ = await summarize(c["chat_id"], max(1, hours // 24))
        except NoAI as e:
            log.info("Va'dalar tekshiruvi to'xtatildi: %s", e)
            break
        except Exception as e:
            log.warning("Chat %s tekshirilmadi (%s)", c["chat_id"], type(e).__name__)
            continue
        if result:
            found += await save_promises(c["chat_id"], info.get("title") or "", result["promises"], "scan")
    return found


async def due_now() -> list[dict]:
    """Muddati bugun yoki o'tib ketgan, bajarilmagan va'dalar."""
    return await storage.promise_list(due_until=today())


async def run_scan(notify) -> None:
    """Har kuni PROMISE_SCAN_TIME da: bugungi yozishmalardan yangi va'dalar. notify(rows, sarlavha)."""
    async def job():
        rows = await scan_recent()
        if rows:
            await notify(rows, "📌 <b>Bugungi yozishmalardan yangi va'dalaringiz</b>")

    await schedule.daily("Va'dalar tekshiruvi", config.PROMISE_SCAN_TIME, "last_promise_scan", job,
                         enabled=lambda: runtime.get("promise_scan"))


async def run_remind(notify) -> None:
    """Har kuni PROMISE_REMIND_TIME da muddati kelgan va'dalarni eslatadi."""
    async def job():
        rows = await due_now()
        if rows:
            await notify(rows, "⏰ <b>Bugun bajarilishi kerak bo'lgan va'dalar</b>")

    # Eslatma doim ishlaydi: xulosadan qo'lda qo'shilgan va'dalar ham bor (avtomatik tekshiruv o'chiq bo'lsa ham)
    await schedule.daily("Va'dalar eslatmasi", config.PROMISE_REMIND_TIME, "last_promise_remind", job)
