"""OpenAI xarajat nazorati: kunlik umumiy va har bir suhbatdosh uchun limit.

Limit tugasa kotiba AI'siz ishlaydi (tayyor shablon matn) — spam yuborgan odam
hisobingizni tugatib qo'ya olmaydi. Egasiga kuniga bir marta ogohlantirish yuboriladi.
"""
import logging

import config
import storage

log = logging.getLogger("usage")

_alerted: set[str] = set()  # "kun:turi" — bir xil ogohlantirishni qayta yubormaslik uchun


async def check(chat_id: int) -> tuple[bool, str | None]:
    """AI so'rovi qilish mumkinmi. (True, None) yoki (False, sabab)."""
    try:
        st = await storage.get_stats()
    except Exception as e:
        log.warning("Statistika o'qilmadi (%s) — limit tekshirilmadi", type(e).__name__)
        return True, None
    total = st.get("ai_requests", 0)
    per_chat = (st.get("chat_ai") or {}).get(str(chat_id), 0)
    if config.AI_DAILY_LIMIT and total >= config.AI_DAILY_LIMIT:
        return False, f"kunlik umumiy AI limiti tugadi ({total}/{config.AI_DAILY_LIMIT})"
    if config.AI_CHAT_DAILY_LIMIT and per_chat >= config.AI_CHAT_DAILY_LIMIT:
        return False, f"bu suhbatdosh uchun kunlik limit tugadi ({per_chat}/{config.AI_CHAT_DAILY_LIMIT})"
    return True, None


async def record(chat_id: int, kind: str, tokens: int = 0) -> None:
    """kind: chat | voice | image | tts"""
    try:
        await storage.stat_inc(**{"ai_requests": 1, f"ai_{kind}": 1, "ai_tokens": tokens,
                                  f"chat_ai.{chat_id}": 1})
    except Exception as e:
        log.warning("AI sarfi yozilmadi (%s)", type(e).__name__)


def should_alert(reason: str, chat_id: int) -> bool:
    """Bir kunda: umumiy limit haqida bir marta, har bir suhbatdosh limiti haqida bir marta."""
    key = f"{storage.today_key()}:{'umumiy' if 'umumiy' in reason else chat_id}"
    if key in _alerted:
        return False
    _alerted.add(key)
    return True


async def owner_ok() -> tuple[bool, str | None]:
    """Egasining o'z so'rovlari (xulosa, va'dalar) uchun — faqat kunlik umumiy limit tekshiriladi."""
    if not config.OPENAI_API_KEY:
        return False, "OPENAI_API_KEY yo'q"
    try:
        st = await storage.get_stats()
    except Exception:
        return True, None
    total = st.get("ai_requests", 0)
    if config.AI_DAILY_LIMIT and total >= config.AI_DAILY_LIMIT:
        return False, f"kunlik umumiy AI limiti tugadi ({total}/{config.AI_DAILY_LIMIT})"
    return True, None


async def record_owner(kind: str, tokens: int = 0) -> None:
    """Egasining AI so'rovi: umumiy hisobga yoziladi, suhbatdoshlarning limitiga ta'sir qilmaydi."""
    try:
        await storage.stat_inc(**{"ai_requests": 1, f"ai_{kind}": 1, "ai_tokens": tokens})
    except Exception as e:
        log.warning("AI sarfi yozilmadi (%s)", type(e).__name__)
