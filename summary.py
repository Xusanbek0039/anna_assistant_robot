"""🧠 AI chat xulosasi (/xulosa): arxivdagi yozishmadan qisqa xulosa, mavzular va ochiq qolgan masalalar."""
import ai
import archive
import schedule
import usage


class NoAI(Exception):
    """AI ishlatib bo'lmaydi (kalit yo'q yoki kunlik limit tugagan)."""


def today() -> str:
    return schedule.now().date().isoformat()


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
