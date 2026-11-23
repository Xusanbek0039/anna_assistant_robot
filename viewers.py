"""👀 Kim qiziqdi: storis'ingizni ko'rganlar va profilingizdagi havola orqali botni ochganlar (botda /korganlar).

❗️ Telegram profilingizni yoki profil rasmingizni KIM ochganini hech kimga bermaydi — na botga, na userbotga,
na Premium'ga. Bu ma'lumot Telegram serveridan umuman chiqmaydi, «profilingizni kim ko'rdi» deydigan botlar
aldaydi. Shunga eng yaqin, haqiqatan ishlaydigan ikki yo'l:

  📖 Storis ko'ruvchilari — Telegram har bir faol storis uchun kim ko'rganini (va reaksiyasini) beradi. Dastur
     storislaringizni har VIEWERS_CHECK_MINUTES da tekshiradi va YANGI ko'ruvchi haqida botga xabar beradi:
     ism, familiya, ID, username. Storis ko'ruvchilarga hech narsa bildirilmaydi.
  🔗 Profildagi havola — bio'ga bot havolasini (yoki @bot) qo'yasiz. Kim bosib botni ochsa — botga kim
     ekani keladi. Buni odamning o'zi bosadi, shuning uchun faqat qiziqib bosganlar tushadi.

Istisno: xabardagi «🚫 Xabar kelmasin» tugmasi — o'sha odam haqida boshqa xabar kelmaydi (ro'yxat: /korganlar).
"""
import asyncio
import html
import logging
import time

from telethon import errors
from telethon.tl.functions.stories import GetPeerStoriesRequest, GetStoryViewsListRequest
from telethon.tl.types import InputPeerSelf, ReactionCustomEmoji, ReactionEmoji, StoryItem, StoryView

import config
import runtime
import storage

log = logging.getLogger("viewers")

PAGE = 100            # GetStoryViewsList bitta so'rovda
MAX_PAGES = 20        # bitta storis uchun ko'pi bilan 2000 ko'ruvchi
GROUP_OVER = 5        # bitta storisga bir tekshiruvda shundan ko'p yangi ko'ruvchi — bitta umumiy xabar
GROUP_BUTTONS = 20    # umumiy xabardagi «🚫» tugmalari soni

_alert = None                 # async (html, tugmalar) -> None  (control_bot.watch_alert)
_counts: dict[int, int] = {}  # storis id -> oxirgi tekshiruvdagi ko'rishlar soni (o'zgarmasa ro'yxat so'ralmaydi)
_wake = asyncio.Event()
last_check = 0.0
last_problem = ""


def setup(alert) -> None:
    global _alert
    _alert = alert


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _when(ts: float | None) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo(config.TIMEZONE)).strftime("%d.%m %H:%M") if ts else ""


def person(user_id: int, u=None) -> dict:
    return {"user_id": user_id,
            "first": (getattr(u, "first_name", None) or "").strip(),
            "last": (getattr(u, "last_name", None) or "").strip(),
            "username": getattr(u, "username", None) or "",
            "deleted": bool(getattr(u, "deleted", False))}


def full_name(p: dict) -> str:
    if p.get("deleted"):
        return "O'chirilgan akkaunt"
    return " ".join(x for x in (p.get("first"), p.get("last")) if x) or "Nomaʼlum"


def person_html(p: dict) -> str:
    lines = [f"👤 <b>{_esc(p.get('first') or ('O‘chirilgan akkaunt' if p.get('deleted') else 'Nomaʼlum'))}</b>"]
    if p.get("last"):
        lines.append(f"👪 Familiya: <b>{_esc(p['last'])}</b>")
    lines.append(f"🆔 ID: <code>{p['user_id']}</code>")
    lines.append(f"🔗 Username: @{_esc(p['username'])}" if p.get("username") else "🔗 Username: yo'q")
    return "\n".join(lines)


def reaction_text(r) -> str:
    if isinstance(r, ReactionEmoji):
        return r.emoticon
    if isinstance(r, ReactionCustomEmoji):
        return "⭐️ (maxsus emoji)"
    return "⭐️" if r is not None else ""


def _buttons(uid: int) -> list:
    return [[("💬 Lichkaga o'tish", f"wch:dm:{uid}"), ("🚫 Xabar kelmasin", f"vw:ign:{uid}")]]


async def _send(text: str, buttons: list | None = None) -> None:
    if _alert:
        try:
            await _alert(text, buttons)
        except Exception:
            log.warning("Ko'ruvchi haqida xabar botga yuborilmadi")


# ---------- 📖 storis ko'ruvchilari ----------
async def _story_viewers(client, story_id: int) -> list[dict]:
    out, offset = [], ""
    for _ in range(MAX_PAGES):
        res = await client(GetStoryViewsListRequest(peer=InputPeerSelf(), id=story_id, offset=offset, limit=PAGE))
        users = {u.id: u for u in res.users}
        for v in res.views:
            if isinstance(v, StoryView):   # qayta joylash/uzatishlar emas — faqat ko'rishlar
                p = person(v.user_id, users.get(v.user_id))
                p["date"] = v.date.timestamp() if v.date else time.time()
                p["reaction"] = reaction_text(v.reaction)
                out.append(p)
        if not res.next_offset or not res.views:
            break
        offset = res.next_offset
    return out


async def _report(story: StoryItem, new: list[dict]) -> None:
    posted = story.date.timestamp() if story.date else None
    head = f"👀 <b>Storis'ingizni ko'rdi</b> <i>(storis {_when(posted)} da qo'yilgan)</i>"
    if len(new) <= GROUP_OVER:
        for p in new:
            extra = f"\n❤️ Reaksiya: {_esc(p['reaction'])}" if p.get("reaction") else ""
            await _send(f"{head}\n\n{person_html(p)}{extra}\n🕒 Ko'rdi: {_when(p.get('date'))}",
                        _buttons(p["user_id"]))
        return
    lines = [f"👀 <b>Storis'ingizni {len(new)} kishi ko'rdi</b> <i>(storis {_when(posted)} da qo'yilgan)</i>", ""]
    for i, p in enumerate(new[:60], 1):
        uname = f" @{_esc(p['username'])}" if p.get("username") else ""
        react = f" {_esc(p['reaction'])}" if p.get("reaction") else ""
        lines.append(f"{i}. <b>{_esc(full_name(p))}</b>{uname} · <code>{p['user_id']}</code>{react}")
    if len(new) > 60:
        lines.append(f"… yana {len(new) - 60} kishi")
    lines.append("\n🚫 tugmasi — o'sha odam haqida boshqa xabar kelmaydi")
    btns = [(f"🚫 {full_name(p)[:18]}", f"vw:ign:{p['user_id']}") for p in new[:GROUP_BUTTONS]]
    await _send("\n".join(lines), [btns[i:i + 2] for i in range(0, len(btns), 2)])


async def check_stories(client) -> int:
    """Faol storislarni tekshiradi; yangi ko'ruvchilar haqida xabar beradi. Yangi ko'ruvchilar soni."""
    global last_check, last_problem
    res = await client(GetPeerStoriesRequest(peer=InputPeerSelf()))
    stories = [s for s in (res.stories.stories or []) if isinstance(s, StoryItem)]
    total = 0
    for s in stories:
        count = getattr(s.views, "views_count", 0) or 0
        if not count or _counts.get(s.id) == count:
            continue
        viewers = await _story_viewers(client, s.id)
        new = await storage.story_views_new(s.id, viewers)
        _counts[s.id] = count
        new = [p for p in new if not await storage.viewer_ignored(p["user_id"])]
        if new:
            new.sort(key=lambda p: p.get("date") or 0)
            await _report(s, new)
            total += len(new)
    last_check, last_problem = time.time(), ""
    return total


async def check_now(client) -> str:
    try:
        n = await check_stories(client)
    except Exception as e:
        log.warning("Storis ko'ruvchilari tekshirilmadi (%s)", type(e).__name__)
        return f"❌ Tekshirilmadi: {type(e).__name__}"
    return f"✅ Tekshirildi — yangi ko'ruvchi: {n}" if n else "✅ Tekshirildi — yangi ko'ruvchi yo'q"


async def run(client) -> None:
    global last_problem
    await asyncio.sleep(30)
    while True:
        _wake.clear()
        if runtime.get("story_viewers"):
            try:
                await check_stories(client)
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as e:
                last_problem = f"Telegram cheklovi — {e.seconds} soniya kutilmoqda"
                await asyncio.sleep(e.seconds)
            except Exception as e:
                last_problem = type(e).__name__
                log.exception("Storis ko'ruvchilari tekshirilmadi")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=config.VIEWERS_CHECK_MINUTES * 60)
        except asyncio.TimeoutError:
            pass


# ---------- 🔗 profildagi havola: botni kim ochdi ----------
def start_payload(text: str) -> str | None:
    """/start bo'lsa — havoladagi qo'shimcha (bo'lmasa ""); /start bo'lmasa None."""
    parts = (text or "").strip().split(maxsplit=1)
    if not parts or parts[0].split("@")[0] != "/start":
        return None
    return parts[1] if len(parts) > 1 else ""


async def bot_visitor(user, text: str) -> None:
    """Begona odam botni ochdi yoki yozdi — egasiga kim ekanini yuboradi (istisnodagilardan tashqari)."""
    if not runtime.get("bot_visitors") or user is None or getattr(user, "bot", False):
        return
    if await storage.viewer_ignored(user.id):
        return
    p = person(user.id, user)
    payload = start_payload(text)
    if payload:
        head = "🔗 <b>Profilingizdagi havola orqali botingizni ochdi</b>"
    elif payload == "":
        head = "🤖 <b>Botingizni ochdi</b> (bio'dagi @bot yoki qidiruv orqali)"
    else:
        head = "🤖 <b>Botingizga yozdi</b>"
    body = f"{head}\n\n{person_html(p)}"
    if payload is None and text:
        body += f"\n\n💬 <i>{_esc(text[:500])}</i>"
    await _send(body, _buttons(user.id))


# ---------- nazorat boti uchun ko'rinishlar ----------
def profile_link(bot_username: str | None) -> str:
    return f"https://t.me/{bot_username}?start=profil" if bot_username else ""


async def panel(bot_username: str | None) -> tuple[str, list]:
    story_on, visit_on = runtime.get("story_viewers"), runtime.get("bot_visitors")
    try:
        n_ign = len(await storage.viewer_ignore_list())
    except Exception:
        n_ign = 0
    link = profile_link(bot_username)
    lines = [
        "👀 <b>Kim qiziqdi</b>", "",
        "❗️ Telegram profilingizni yoki profil rasmingizni <b>kim ochganini hech kimga bermaydi</b> — bu "
        "ma'lumot Telegram serveridan umuman chiqmaydi («profilingizni kim ko'rdi» deydigan botlar aldaydi). "
        "Shunga eng yaqin, haqiqatan ishlaydigan ikki yo'l:", "",
        f"📖 <b>Storis ko'ruvchilari</b> {'✅' if story_on else '⏸'}\n"
        f"Storis qo'ysangiz — uni kim ko'rganini (ism, familiya, ID, username, reaksiya) shu yerga yuboraman. "
        f"Har {config.VIEWERS_CHECK_MINUTES} daqiqada tekshiriladi, ko'ruvchiga hech narsa bildirilmaydi.", "",
        f"🔗 <b>Profildagi havola</b> {'✅' if visit_on else '⏸'}\n"
        "Kim botingizni ochsa yoki yozsa — kimligini yuboraman. Profilingizga (bio'ga) havola qo'ying:",
        f"<code>{_esc(link)}</code>" if link else "(bot username hali noma'lum)",
        f"yoki shunchaki <code>@{_esc(bot_username)}</code>" if bot_username else None,
        "Bio avtomatik yangilansa — .env dagi BIO_TEMPLATE ga qo'shing, masalan:\n"
        f"<code>{{prefix}} | 🕒 {{time}} | @{_esc(bot_username or 'bot')}</code>", "",
        f"🚫 Istisnolar: {n_ign} ta",
    ]
    if last_check:
        lines.append(f"🕒 Storis oxirgi tekshiruvi: {_when(last_check)}")
    if last_problem:
        lines.append(f"⚠️ {_esc(last_problem)}")
    rows = [[(f"{'✅' if story_on else '▫️'} 📖 Storis", "vw:set:story_viewers"),
             (f"{'✅' if visit_on else '▫️'} 🔗 Havola", "vw:set:bot_visitors")],
            [("🔄 Hozir tekshirish", "vw:check"), ("🚫 Istisnolar", "vw:list")]]
    return "\n".join(x for x in lines if x is not None), rows


async def toggle(key: str) -> str:
    names = {"story_viewers": "📖 Storis ko'ruvchilari", "bot_visitors": "🔗 Havola orqali kelganlar"}
    if key not in names:
        return "Noma'lum sozlama"
    value = not runtime.get(key)
    await runtime.update(**{key: value})
    if value:
        _wake.set()
    return f"{names[key]}: {'yoqildi ✅' if value else 'o‘chirildi'}"


async def ignore_view() -> tuple[str, list]:
    rows = await storage.viewer_ignore_list()
    back = [("⬅️ Kim qiziqdi", "vw:panel")]
    if not rows:
        return "🚫 Istisnolar ro'yxati bo'sh. Xabardagi «🚫 Xabar kelmasin» tugmasi bilan qo'shiladi.", [back]
    text = "🚫 <b>Istisnolar</b> — bu odamlar haqida xabar kelmaydi. Olib tashlash uchun bosing:"
    buttons = [[(f"↩️ {r['label'][:36]}", f"vw:unign:{r['user_id']}")] for r in rows[:40]]
    return text, buttons + [back]
