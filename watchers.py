"""🔔 Eslatmalar: egasi aytgan hodisa haqiqatan ro'y berganda nazorat boti orqali xabar beradi.

Botga oddiy yozing yoki ovozli ayting:
  «+998 95 446 68 86 raqami tg ga qo'shilsa / akkaunt ochsa / yashirindan chiqarsa xabar ber»
  «@ali online bo'lsa xabar ber», «Vali onlayn bo'lsa ayt», «Ali har safar online bo'lganda xabar ber»

Turlari:
  phone  — raqam Telegram'da topilsa: yangi akkaunt ochsa yoki «Raqamim orqali kim topa oladi» maxfiyligini
           ochsa. Har WATCH_PHONE_MINUTES da contacts.resolvePhone bilan tekshiriladi — raqam kontaktlaringizga
           QO'SHILMAYDI va u odamga hech narsa ko'rinmaydi. Topilgach — bir marta xabar, eslatma yopiladi.
  online — odam online bo'lsa. Real vaqtda (Telegram yuboradigan status yangilanishi) va zaxira sifatida har
           WATCH_ONLINE_SECONDS da users.getUsers. Standart — bir marta; «har safar» deyilsa — har gal
           (orada kamida WATCH_ONLINE_COOLDOWN_MINUTES). Odam «So'nggi faollik»ni sizdan yashirgan bo'lsa,
           Telegram online holatini ko'rsatmaydi — bu haqda eslatma qo'yilayotganda ogohlantiriladi.
Eslatmalar MongoDB'da saqlanadi — dastur qayta ishga tushganda ham davom etadi.
"""
import asyncio
import html
import itertools
import logging
import re
import time
from dataclasses import dataclass

from telethon import errors, events
from telethon.tl.functions.contacts import ResolvePhoneRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.tl.types import InputUser, User, UserStatusOffline, UserStatusOnline

import config
import storage

log = logging.getLogger("watchers")

PHONE_PAUSE = 3.0   # bir nechta raqam tekshirilganda orasida (Telegram cheklovi bo'lmasligi uchun)
BATCH = 100         # users.getUsers bitta so'rovda

# ---------- egasining gapini tushunish ----------
_A = "['ʻ’`‘]?"     # o'/o‘/o’/o — apostrofsiz yozilsa ham
NOTIFY_RE = re.compile(
    rf"\b[xh]abar(?:dor)?\s*(?:ber|qil|yubor|jo{_A}nat)\w*|\bbildir\w*|\bogohlantir\w*|\beslat\w*"
    r"|\bayt(?:ib|gin|ing|asan|sang|chi)?\b|\bkuzat\w*|\bnotify\b|\btell\s+me\b|\blet\s+me\s+know\b"
    r"|\bсообщи\w*|\bскажи\w*|\bуведоми\w*|\bнапиши\s+мне",
    re.IGNORECASE)
ONLINE_RE = re.compile(r"\bonl[aei]y?n\w*|\bонлайн\w*|\b(?:internet|tarmoq|set)ga\s+kir\w*|\bв\s+сети\b",
                       re.IGNORECASE)
_OCH = r"och(?:sa|ilsa|ganda|gan|di|ib|ishi|masmi)\b"   # «ochsa», lekin «ochirsa» (o'chirsa) emas
SIGNUP_RE = re.compile(
    rf"\b(?:tg|telegram\w*|телеграм\w*)\s*(?:ga|da|га|да)?\s*(?:qo{_A}sh\w*|kir\w*|o{_A}t\w*|{_OCH}|paydo|ul[ai]n\w*"
    rf"|ro{_A}yxat\w*|topil\w*)"
    rf"|\ba[ck]+[ao]u?nt\w*\s+(?:{_OCH}|yarat\w*)|\bаккаунт\w*\s+(?:откро|созда)\w*"
    rf"|\byashirin\w*\s+chiq\w*|\byashir\w*\s+(?:olib|o{_A}chir)\w*|\bro{_A}yxatdan\s+o{_A}t\w*"
    rf"|\bpaydo\s+bo{_A}l\w*|\bзарегистр\w*|\bпоявит\w*|\bjoins?\b|\bsigns?\s*up\b",
    re.IGNORECASE)
REPEAT_RE = re.compile(r"\bhar\s+(?:bir\s+)?(?:safar|gal|marta|doim|kir\w*|onl\w*)|\bdoim\w*|\bhamisha\b"
                       r"|\bкаждый\s+раз|\bвсегда\b|\bevery\s*time\b|\balways\b", re.IGNORECASE)
PHONE_HINT = re.compile(r"\b(?:raqam\w*|nomer\w*|номер\w*|telefon\w*|телефон\w*|phone\w*|tel)\b", re.IGNORECASE)
NUMBER_RE = re.compile(r"(?<![\w@/])\+?\d[\d \-().]{5,22}\d(?!\w)")
# «... bo'lsa», «qo'shilsa», «online bo'lganda», «qachon» — eslatma shart bilan aytiladi
COND_RE = re.compile(r"\w(?:sa|ganda|ganida|guncha)\b|\bqachon\b|\bесли\b|\bкогда\b|\bif\b|\bwhen\b|\bonce\b",
                     re.IGNORECASE)
_SEARCH_RE = re.compile(r"\b(?:top|topib|topgin|topchi|qidir\w*|izla\w*|find|search|найди\w*|поищи)\b",
                        re.IGNORECASE)
# ismdan ajratiladigan fe'llar («bo'lsa», «kirsa»...) — «Bolta», «Kirill» kabi ismlar saqlanib qolsin
_VERB_FORM = re.compile(r"^(?:bo'?l(?:sa|ganda|ganida|ishi|sin|ib|di|adi|ar)|kir(?:sa|ganda|ishi|di|adi)"
                        r"|chiq(?:sa|arsa|ganda|di)|qo'?shil\w*|och(?:sa|ilsa|di)|topil\w*)$")
ID_RE = re.compile(r"\bid\s*[:=]?\s*(\d{5,15})\b", re.IGNORECASE)
USERNAME_RE = re.compile(r"(?<![\w@.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
TME_RE = re.compile(r"\b(?:t|telegram)\.me/([A-Za-z][A-Za-z0-9_]{3,31})\b", re.IGNORECASE)

# ism ajratishda tashlab yuboriladigan so'zlar
_STOP = {
    "menga", "men", "meni", "mening", "shu", "shuni", "bu", "buni", "u", "uni", "o'sha", "osha", "user", "useri",
    "userni", "odam", "odamni", "foydalanuvchi", "kontakt", "agar", "qachon", "qachonki", "har", "safar", "gal",
    "bir", "marta", "doim", "iltimos", "pls", "tg", "tgda", "tgga", "telegram", "telegramda", "telegramga", "da",
    "ga", "va", "yoki", "ham", "ber", "bering", "bergin", "berasan", "beringlar", "qil", "qiling", "darhol",
    "tezda", "zudlik", "bilan", "lozim", "kerak", "xabar", "habar", "eslatma", "holda", "holatda", "shunday",
    "shunda", "ekan", "yana", "keyingi", "anna", "akkaunt", "akkaunti", "akkauntga", "if", "when", "me", "is",
    "goes", "comes", "tell", "the", "please", "мне", "если", "когда", "будет",
}


@dataclass
class Request:
    kind: str                 # phone — raqam Telegram'da paydo bo'lishi; online — odam online bo'lishi
    repeat: bool = False      # «har safar»
    phone: str | None = None  # 998954466886 (faqat raqamlar, mamlakat kodi bilan); None — yo'q yoki noto'g'ri
    number: str = ""          # matndagi raqamning o'zi (xato bo'lsa ko'rsatish uchun)
    user_ref: str = ""        # @username yoki ID (raqam bo'lishi ham mumkin — ikkalasi sinab ko'riladi)
    name: str = ""            # ism — chatlardan qidiriladi


def _norm(s: str) -> str:
    return re.sub(r"[ʻ’`‘]", "'", (s or "").lower())


def normalize_phone(raw: str) -> str | None:
    """«+998 (95) 446-68-86», «954466886» -> «998954466886». Noto'g'ri/to'liq bo'lmasa — None."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("00"):
        d = d[2:]
    cc = re.sub(r"\D", "", config.WATCH_COUNTRY_CODE or "")
    if cc == "998" and len(d) == 9:   # O'zbekiston: 90 123 45 67
        d = cc + d
    if d.startswith("998") and len(d) != 12:
        return None
    return d if 10 <= len(d) <= 15 else None


def fmt_phone(d: str) -> str:
    if d.startswith("998") and len(d) == 12:
        return f"+998 {d[3:5]} {d[5:8]} {d[8:10]} {d[10:]}"
    return "+" + d


def name_words(text: str) -> str:
    """Gapdan «online bo'lsa xabar ber» kabi so'zlar olib tashlangan qismi (odamning ismi)."""
    out = []
    for w in re.findall(r"[\w'ʻ’`‘]+", text or "", re.UNICODE):
        n = _norm(w).strip("'")
        if (len(n) < 2 or n in _STOP or _VERB_FORM.match(n)
                or NOTIFY_RE.fullmatch(n) or ONLINE_RE.fullmatch(n) or REPEAT_RE.fullmatch(n)):
            continue
        out.append(w)
    return " ".join(out)


def parse(text: str, strict: bool = True) -> Request | None:
    """Egasining gapi eslatma so'rovimi. Emas bo'lsa — None (qidiruv, buxgalter va h.k. ga o'tadi).
    strict=False — /eslatma buyrug'i uchun: «/eslatma @ali», «/eslatma 954466886» ham tushuniladi."""
    t = text or ""
    online, signup = bool(ONLINE_RE.search(t)), bool(SIGNUP_RE.search(t))
    if strict and (not (online or signup) or not NOTIFY_RE.search(t) or not COND_RE.search(t)
                   or _SEARCH_RE.search(t)):
        return None
    repeat = bool(REPEAT_RE.search(t))
    m_user = USERNAME_RE.search(t) or TME_RE.search(t)
    m_id = ID_RE.search(t)
    m_num = next((m for m in NUMBER_RE.finditer(t) if len(re.sub(r"\D", "", m.group())) >= 7), None)
    number = m_num.group().strip() if m_num else ""
    phone_hint = bool(PHONE_HINT.search(t)) or number.startswith("+")
    if not (online or signup):  # faqat /eslatma buyrug'ida: raqam — Telegram'da paydo bo'lishi, qolgani — online
        signup = bool(m_num and not m_user and not m_id)
    if signup and not m_user and not m_id and (m_num or (phone_hint and not online)):
        return Request("phone", repeat, normalize_phone(number), number)
    req = Request("online", repeat)
    if m_user:
        req.user_ref = "@" + m_user.group(1)
    elif m_id:
        req.user_ref = m_id.group(1)
    elif m_num:
        req.number = number
        if phone_hint:
            req.phone = normalize_phone(number)
        else:
            req.user_ref = re.sub(r"\D", "", number)  # Telegram ID yoki telefon raqami
    else:
        req.name = name_words(t)
    return req


_ABOUT_RE = re.compile(r"\b(?:menga|shu|ushbu|bu|uni|buni|shuni|eslat\w*|kuzat\w*|мне|этот|его)\b", re.IGNORECASE)


def about_this(text: str) -> bool:
    """Bildirishnomaga reply qilingan gap suhbatdoshga emas, botga aytilganmi («shu user online bo'lsa menga
    xabar ber»). Busiz suhbatdoshga yoziladigan «online bo'lsa xabar ber» ham eslatma deb olinardi."""
    return bool(_ABOUT_RE.search(text or ""))


def bad_number_text(number: str) -> str:
    if not number:
        return ("📱 Qaysi raqam? Masalan: «+998 90 123 45 67 raqami Telegram'ga qo'shilsa xabar ber».")
    n = len(re.sub(r"\D", "", number))
    return (f"❌ «{html.escape(number)}» — raqam noto'g'ri yoki to'liq emas ({n} ta raqam). O'zbekiston raqami "
            "operator kodi bilan 9 xonali bo'ladi: <code>95 446 68 86</code> yoki <code>+998 95 446 68 86</code>. "
            "Boshqa davlat raqamini + bilan to'liq yozing.")


# ---------- Telegram ----------
def status_kind(status, now: float | None = None) -> str:
    """online | offline | hidden («yaqinda», «shu hafta»... — odam vaqtni yashirgan)."""
    if isinstance(status, UserStatusOnline):
        exp = status.expires.timestamp() if status.expires else None
        return "offline" if exp and exp < (now or time.time()) else "online"
    if isinstance(status, UserStatusOffline):
        return "offline"
    return "hidden"


def _name(u: User) -> str:
    return " ".join(p for p in (u.first_name, u.last_name) if p) or "Nomaʼlum"


def _username(u: User) -> str:
    return u.username or next((x.username for x in (getattr(u, "usernames", None) or []) if x.active), "") or ""


def _who(name: str, username: str, uid: int) -> str:
    out = f"<b>{html.escape(name)}</b>"
    if username:
        out += f" (@{html.escape(username)})"
    return out + f"\n🆔 <code>{uid}</code>"


async def resolve_phone(client, phone: str) -> User | None:
    """Raqam egasi (maxfiylik ruxsat bersa). Raqam kontaktlarga qo'shilmaydi."""
    try:
        res = await client(ResolvePhoneRequest(phone=phone))
    except errors.PhoneNotOccupiedError:
        return None
    return next((u for u in res.users if isinstance(u, User) and not u.deleted), None)


async def resolve_user(client, ref) -> User | None:
    """@username, ID yoki telefon raqami -> User (topilmasa None)."""
    if isinstance(ref, User):
        return ref
    ref = str(ref).strip()
    if ref.isdigit():
        try:
            ent = await client.get_entity(int(ref))
            if isinstance(ent, User):
                return ent
        except (ValueError, TypeError, errors.RPCError):
            pass
        phone = normalize_phone(ref)
        if phone:
            try:
                return await resolve_phone(client, phone)
            except errors.RPCError:
                return None
        return None
    try:
        ent = await client.get_entity(ref)
    except (ValueError, TypeError, errors.RPCError):
        return None
    return ent if isinstance(ent, User) else None


# ---------- holat ----------
_alert = None                                  # async (text, buttons) -> None  (control_bot.watch_alert)
_online: dict[int, list[dict]] = {}            # user_id -> shu odam uchun faol online eslatmalar
_lock = asyncio.Lock()
_wake = asyncio.Event()
_phone_pause_until = 0.0                       # FloodWait bo'lsa raqam tekshiruvi shu vaqtgacha to'xtaydi
_pending: dict[str, tuple[bool, list]] = {}    # ism bo'yicha bir nechta odam topilsa — tanlov kutilmoqda
_seq = itertools.count(1)


async def _send(text: str, buttons: list | None = None) -> None:
    if _alert:
        try:
            await _alert(text, buttons)
        except Exception:
            log.exception("Eslatma xabari yuborilmadi")


async def _refresh() -> list[dict]:
    rows = await storage.watch_list()
    by_user: dict[int, list[dict]] = {}
    for r in rows:
        if r["kind"] == "online":
            by_user.setdefault(r["user_id"], []).append(r)
    _online.clear()
    _online.update(by_user)
    return rows


def _user_buttons(uid: int, online_too: bool = False) -> list:
    rows = [[("💬 Lichkaga o'tish", f"wch:dm:{uid}")]]
    if online_too:
        rows.append([("🟢 Online bo'lsa ham xabar ber", f"wch:on:{uid}")])
    return rows


LIST_BUTTON = [("🔔 Eslatmalar", "wch:list")]


# ---------- eslatma qo'yish ----------
async def add_phone(client, phone: str) -> tuple[str, list]:
    pretty = fmt_phone(phone)
    rows = await storage.watch_list()
    if any(r["kind"] == "phone" and r.get("phone") == phone for r in rows):
        return f"🔔 <code>{pretty}</code> raqami allaqachon kuzatilyapti.", [LIST_BUTTON]
    note = ""
    try:
        user = await resolve_phone(client, phone)
    except errors.PhoneNumberInvalidError:
        return f"❌ Telegram <code>{pretty}</code> raqamini noto'g'ri deb hisobladi — tekshirib qayta yozing.", []
    except errors.FloodWaitError as e:
        user, note = None, f"\n⏳ Telegram hozir {e.seconds} soniya kutishni so'radi — keyin tekshiraman."
    except Exception as e:
        log.warning("Raqam tekshirilmadi (%s)", type(e).__name__)
        user, note = None, "\n⚠️ Hozir tekshirib bo'lmadi — keyinroq o'zim qayta urinaman."
    if user is not None:
        return (f"📱 <code>{pretty}</code> raqami <b>allaqachon Telegram'da</b>:\n👤 "
                + _who(_name(user), _username(user), user.id)), _user_buttons(user.id, online_too=True)
    wid = await storage.watch_add({"kind": "phone", "phone": phone, "label": pretty,
                                   "last_check": 0 if note else time.time()})
    _wake.set()
    return (f"🔔 Eslatma qo'yildi: 📱 <code>{pretty}</code>\n"
            "Hozir bu raqam Telegram'da topilmadi (akkaunt yo'q yoki raqami bo'yicha qidirishni yopgan). "
            f"Har {config.WATCH_PHONE_MINUTES} daqiqada tekshiraman — Telegram'ga qo'shilsa, akkaunt ochsa yoki "
            "yashirishni olib tashlasa, darhol xabar beraman.\n"
            "ℹ️ Raqam kontaktlaringizga qo'shilmaydi, u odamga hech narsa ko'rinmaydi." + note,
            [[("🗑 Bekor qilish", f"wch:del:{wid}")], LIST_BUTTON])


HIDDEN_NOTE = ("\n⚠️ Bu odam «So'nggi faollik»ni sizdan yashirgan — Telegram uning online bo'lganini "
               "ko'rsatmasligi mumkin, unda xabar bera olmayman. (O'zingiz ham «So'nggi faollik»ni yashirgan "
               "bo'lsangiz, boshqalarniki ham ko'rinmaydi.)")


async def add_online(client, user: User, repeat: bool = False) -> tuple[str, list]:
    if not isinstance(user, User) or user.bot or user.is_self:
        return "❌ Faqat odamning (bot yoki o'zingiz emas) online holatini kuzatish mumkin.", []
    name, username = _name(user), _username(user)
    rows = await storage.watch_list()
    existing = next((r for r in rows if r["kind"] == "online" and r["user_id"] == user.id), None)
    how = (f"har safar online bo'lganda (orada kamida {config.WATCH_ONLINE_COOLDOWN_MINUTES} daqiqa)"
           if repeat else "online bo'lganda (bir marta)")
    if existing:
        if bool(existing.get("repeat")) != repeat:
            await storage.watch_update(existing["id"], repeat=repeat)
            await _refresh()
            return f"🔔 {_who(name, username, user.id)}\nEslatma yangilandi: {how} xabar beraman.", [LIST_BUTTON]
        return f"🔔 {_who(name, username, user.id)}\nbu odam allaqachon kuzatilyapti.", [LIST_BUTTON]
    st = status_kind(user.status)
    wid = await storage.watch_add({"kind": "online", "user_id": user.id, "access_hash": user.access_hash or 0,
                                   "label": name, "username": username, "repeat": repeat,
                                   "last_status": st, "last_fired": 0, "fired": 0})
    await _refresh()
    _wake.set()
    text = f"🔔 Eslatma qo'yildi: {_who(name, username, user.id)}\n{how.capitalize()} xabar beraman."
    if st == "online":
        text += "\n🟢 U hozir online — keyingi safar online bo'lganda xabar beraman."
    elif st == "hidden":
        text += HIDDEN_NOTE
    return text, [[("🗑 Bekor qilish", f"wch:del:{wid}")], LIST_BUTTON]


async def handle(client, req: Request) -> tuple[str, list]:
    """Egasining so'rovi bo'yicha eslatma qo'yadi. (javob matni, tugmalar)."""
    if not config.WATCH_ENABLED:
        return "🔕 Eslatmalar o'chirilgan (.env: WATCH_ENABLED=false). Yoqib, dasturni qayta ishga tushiring.", []
    if req.kind == "phone":
        if not req.phone:
            return bad_number_text(req.number), []
        return await add_phone(client, req.phone)
    if req.user_ref:
        user = await resolve_user(client, req.user_ref)
        if user is None:
            return (f"❌ <code>{html.escape(req.user_ref)}</code> topilmadi. @username, Telegram ID yoki "
                    "telefon raqamini yozing (raqam bo'lsa: «... raqami online bo'lsa»).", [])
        return await add_online(client, user, req.repeat)
    if req.number:  # «95 446 68 86 raqami online bo'lsa»
        if not req.phone:
            return bad_number_text(req.number), []
        try:
            user = await resolve_phone(client, req.phone)
        except Exception as e:
            log.warning("Raqam tekshirilmadi (%s)", type(e).__name__)
            user = None
        if user is None:
            return (f"📱 <code>{fmt_phone(req.phone)}</code> raqami Telegram'da topilmadi (akkaunt yo'q yoki "
                    "raqami bo'yicha qidirishni yopgan), shuning uchun online holatini bilib bo'lmaydi.",
                    [[("📱 Telegram'da paydo bo'lsa xabar ber", f"wch:ph:{req.phone}")]])
        return await add_online(client, user, req.repeat)
    if not req.name:
        return ("Kimni kuzatay? @username, Telegram ID, telefon raqami yoki chatdagi ismini yozing. Masalan: "
                "«@ali online bo'lsa xabar ber».", [])
    import finder
    try:
        cands = await finder.find_chats(client, req.name)
    except Exception as e:
        log.warning("Chatlar ro'yxati olinmadi (%s)", type(e).__name__)
        return f"❌ Chatlar ro'yxatini olib bo'lmadi: {type(e).__name__}", []
    if not cands:
        return (f"🤷 «{html.escape(req.name)}» nomli chat topilmadi. @username yoki Telegram ID yozing.", [])
    best = cands[0]
    if len(cands) == 1 or best.score >= cands[1].score + 1:
        user = await resolve_user(client, best.id)
        if user is None:
            return f"❌ «{html.escape(best.title)}» — bu odam emas (guruh yoki kanal).", []
        return await add_online(client, user, req.repeat)
    key = str(next(_seq))
    _pending[key] = (req.repeat, cands[:6])
    while len(_pending) > 20:
        _pending.pop(next(iter(_pending)))
    return "Kimni kuzatay?", [[((c.title[:40] + (f" (@{c.username})" if c.username else "")), f"wch:pick:{key}:{i}")]
                              for i, c in enumerate(cands[:6])]


async def pick(client, key: str, idx: int) -> tuple[str, list] | None:
    pending = _pending.get(key)
    if not pending or not 0 <= idx < len(pending[1]):
        return None
    repeat, cands = pending
    user = await resolve_user(client, cands[idx].id)
    if user is None:
        return f"❌ «{html.escape(cands[idx].title)}» — bu odam emas (guruh yoki kanal).", []
    return await add_online(client, user, repeat)


# ---------- ro'yxat ----------
def _ago(ts: float) -> str:
    m = int((time.time() - ts) // 60)
    return "hozirgina" if m < 1 else f"{m} daqiqa oldin" if m < 60 else f"{m // 60} soat oldin"


_STATUS_NAMES = {"online": "🟢 online", "offline": "⚪️ offline", "hidden": "🙈 yashirgan"}


async def list_view() -> tuple[str, list]:
    rows = await storage.watch_list()
    if not rows:
        return ("🔔 <b>Eslatmalar</b>\n\nHozir eslatma yo'q. Qo'yish uchun botga yozing yoki ayting:\n"
                "• «+998 90 123 45 67 raqami Telegram'ga qo'shilsa xabar ber»\n"
                "• «@username online bo'lsa xabar ber»\n"
                "• «Ali har safar online bo'lganda xabar ber»", [])
    lines, buttons = ["🔔 <b>Eslatmalar</b>\n"], []
    for i, r in enumerate(rows, 1):
        if r["kind"] == "phone":
            seen = f", tekshirildi: {_ago(r['last_check'])}" if r.get("last_check") else ""
            lines.append(f"{i}. 📱 <code>{html.escape(r['label'])}</code> — Telegram'da paydo bo'lsa{seen}")
        else:
            who = html.escape(r.get("label") or str(r["user_id"]))
            if r.get("username"):
                who += f" (@{html.escape(r['username'])})"
            how = "har safar" if r.get("repeat") else "bir marta"
            st = _STATUS_NAMES.get(r.get("last_status"), "")
            lines.append(f"{i}. 🟢 <b>{who}</b> — online bo'lsa ({how})" + (f", hozir: {st}" if st else ""))
        buttons.append((f"🗑 {i}", f"wch:del:{r['id']}"))
    lines.append("\n🗑 — eslatmani o'chirish")
    return "\n".join(lines), [buttons[i:i + 4] for i in range(0, len(buttons), 4)]


async def remove(watch_id: str) -> bool:
    ok = await storage.watch_delete(watch_id)
    if ok:
        await _refresh()
    return ok


# ---------- tekshirish ----------
async def on_status(user_id: int, status, user: User | None = None) -> None:
    """Odamning online holati o'zgardi (real vaqtda yoki davriy tekshiruvda)."""
    kind = status_kind(status)
    fire: list[dict] = []
    async with _lock:
        for r in list(_online.get(user_id, [])):
            fields: dict = {}
            if user is not None and (r.get("label") != _name(user) or r.get("username") != _username(user)):
                fields.update(label=_name(user), username=_username(user))
            if r.get("last_status") != kind:
                fields["last_status"] = kind
                now = time.time()
                if kind == "online" and (not r.get("repeat") or
                                         now - r.get("last_fired", 0) >= config.WATCH_ONLINE_COOLDOWN_MINUTES * 60):
                    fields.update(last_fired=now, fired=r.get("fired", 0) + 1)
                    if not r.get("repeat"):
                        fields["active"] = False
                        _online[user_id].remove(r)
                    fire.append(r)
            if fields:
                r.update(fields)
                await storage.watch_update(r["id"], **fields)
    for r in fire:
        who = _who(r.get("label") or str(user_id), r.get("username") or "", user_id)
        tail = ("" if r.get("repeat") else "\n(Eslatma bajarildi va yopildi.)")
        buttons = _user_buttons(user_id)
        if r.get("repeat"):
            buttons.append([("🛑 Kuzatuvni to'xtatish", f"wch:del:{r['id']}")])
        await _send(f"🔔 <b>Eslatma!</b> 🟢 {who}\n<b>hozir online!</b>{tail}", buttons)


async def check_phone(client, r: dict) -> None:
    global _phone_pause_until
    pretty = fmt_phone(r["phone"])
    try:
        user = await resolve_phone(client, r["phone"])
    except errors.FloodWaitError as e:
        _phone_pause_until = time.time() + e.seconds + 5
        log.info("Raqam tekshiruvi: Telegram %d soniya kutishni so'radi", e.seconds)
        return
    except errors.PhoneNumberInvalidError:
        await storage.watch_update(r["id"], active=False, error="invalid")
        await _send(f"❌ Eslatma yopildi: Telegram <code>{pretty}</code> raqamini noto'g'ri deb hisobladi.")
        return
    except Exception as e:
        log.info("Raqam tekshirilmadi (%s)", type(e).__name__)
        await storage.watch_update(r["id"], last_check=time.time())
        return
    if user is None:
        await storage.watch_update(r["id"], last_check=time.time())
        return
    await storage.watch_update(r["id"], last_check=time.time(), active=False, found_user_id=user.id,
                               found_at=time.time())
    await _send(f"🔔 <b>Eslatma!</b> 📱 <code>{pretty}</code> raqami <b>Telegram'da paydo bo'ldi</b> "
                "(akkaunt ochdi yoki raqamini yashirishni olib tashladi):\n👤 "
                + _who(_name(user), _username(user), user.id), _user_buttons(user.id, online_too=True))


async def check_once(client) -> None:
    rows = await _refresh()
    online = {r["user_id"]: r for r in rows if r["kind"] == "online"}
    inputs = [InputUser(uid, r.get("access_hash") or 0) for uid, r in online.items()]
    for i in range(0, len(inputs), BATCH):
        try:
            users = await client(GetUsersRequest(inputs[i:i + BATCH]))
        except errors.FloodWaitError as e:
            log.info("Online tekshiruvi: Telegram %d soniya kutishni so'radi", e.seconds)
            await asyncio.sleep(min(e.seconds + 1, 600))
            continue
        except Exception as e:
            log.warning("Online holatlar olinmadi (%s)", type(e).__name__)
            continue
        for u in users:
            if isinstance(u, User):
                await on_status(u.id, u.status, u)
    due = time.time() - config.WATCH_PHONE_MINUTES * 60
    for r in rows:
        if r["kind"] != "phone" or r.get("last_check", 0) > due:
            continue
        if time.time() < _phone_pause_until:
            break
        await check_phone(client, r)
        await asyncio.sleep(PHONE_PAUSE)


def setup(client, alert) -> None:
    global _alert
    _alert = alert

    @client.on(events.UserUpdate)
    async def _on_user_update(event):
        # status=None — bu «yozmoqda...» kabi hodisa; faqat kuzatilayotgan odamlar
        if event.status is None or event.user_id not in _online:
            return
        try:
            await on_status(event.user_id, event.status)
        except Exception as e:
            log.warning("Online holat qayta ishlanmadi (%s)", type(e).__name__)


async def run(client) -> None:
    """Fon vazifasi: online holat (zaxira) va raqamlarni davriy tekshiradi."""
    await asyncio.sleep(15)
    while True:
        try:
            await check_once(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Eslatmalar tekshirilmadi")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=config.WATCH_ONLINE_SECONDS)
        except asyncio.TimeoutError:
            pass
