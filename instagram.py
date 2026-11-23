"""📸 Instagram: akkauntingiz nazorat boti orqali ulanadi va boshqariladi (botda /ig yoki «📸 Instagram»).

Imkoniyatlar (har birini /ig panelidan yoqib-o'chirsa bo'ladi):
  🔥 Bir marta ko'riladigan rasm/video — kimdir Direct'da o'chib ketadigan media yuborsa, dastur uni OCHMASDAN
     yuklab oladi va botga oddiy (saqlasa bo'ladigan) holda yuboradi. Instagram'da media «ochilmagan» bo'lib
     qoladi — yuboruvchiga hech narsa bildirilmaydi.
  🤖 Avtojavob — kotiba (Anna) Instagram Direct'dagi shaxsiy yozishmalarga ham javob beradi. Telegram rejimiga
     (/auto, /on, /off) bo'ysunadi; siz Instagram'da o'zingiz yozsangiz — o'sha chatda vaqtincha jim turadi.
  🗂 Chat arxivi — yozishmalar MongoDB'ga yoziladi; kimdir xabarini qaytarib olsa (unsend) — botga xabar keladi,
     yozishmani HTML fayl qilib olasiz.
  📥 Xabarlar botga — Direct'ga kelgan yangi xabarlar botga keladi; bildirishnomaga reply qilib javob yozasiz.

Qanday ishlaydi: Instagram'ning rasmiy API'si shaxsiy Direct'ni bermaydi — telefon ilovasi o'rnida ishlaydigan
instagrapi kutubxonasi ishlatiladi va dastur har IG_POLL_SECONDS da Direct'ni tekshiradi.
Muhim:
  • Bir martalik media faqat siz uni Instagram'da OCHGUNINGIZCHA olinadi — ochilgach Instagram uni bermaydi.
    Bildirishnoma kelsa, avval botni tekshiring (dastur ishlab turgan bo'lishi kerak).
  • Uchidan-uchiga shifrlangan (end-to-end) chatlarni Instagram serveri ham o'qiy olmaydi — ular ko'rinmaydi.
  • Bu norasmiy yo'l: juda tez-tez tekshirish akkauntga vaqtinchalik cheklov olib kelishi mumkin.
Login: /iglogin <username> <parol> (parolli xabar darhol o'chiriladi, parol hech qayerga yozilmaydi) yoki
/igsession <sessionid> (brauzer cookie'si). Faqat sessiya MongoDB'da saqlanadi — qayta login so'ralmaydi.
"""
import asyncio
import concurrent.futures
import functools
import html
import importlib.metadata
import json
import logging
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import httpx

import ai
import archive
import availability
import config
import runtime
import storage
import usage

log = logging.getLogger("instagram")
for _name in ("instagrapi", "private_request", "public_request"):
    logging.getLogger(_name).setLevel(logging.WARNING)   # har 30 soniyadagi so'rovlar logni to'ldirmasin

THREAD_LIMIT = 20            # Direct'dan nechta chat olinadi
MESSAGE_LIMIT = 20           # har chatdan oxirgi nechta xabar
PENDING_EVERY = 4            # «So'rovlar» (message requests) papkasi har nechta tekshiruvda bir marta
CODE_TIMEOUT = 5 * 60        # Instagram tasdiqlash kodini kutish (soniya)
THROTTLE_PAUSE = 15 * 60     # Instagram «biroz kuting» desa — shuncha tekshirilmaydi
SETTINGS_SAVE_EVERY = 3600   # sessiya (cookie) bazaga shuncha soniyada bir yangilanadi
MISSING_CONFIRM = 2          # xabar ketma-ket shuncha tekshiruvda ko'rinmasa — qaytarib olingan
FAIL_ALERT = 5               # shuncha ketma-ket xatodan keyin egasi ogohlantiriladi
MAX_MEDIA_BYTES = config.MAX_MEDIA_MB * 1024 * 1024
CODE_RE = re.compile(r"\d{4,8}")
USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
_UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"

TOGGLES = {"ig_save": "🔥 Bir martalik media", "ig_autoreply": "🤖 Avtojavob",
           "ig_archive": "🗂 Arxiv", "ig_forward": "📥 Xabarlar botga"}
_TEXT_KINDS = {"", "havola", "like", "amal", "xabar", "qo'ng'iroq"}
_SKIP_UNSENT = {"amal", "xabar", "qo'ng'iroq"}   # vaqtinchalik xizmat xabarlari — «qaytarib olindi» deyilmaydi

# instagrapi xatolari nomi bo'yicha (kutubxona o'rnatilmagan bo'lsa ham modul ishlashi uchun)
_AUTH_ERRORS = {"LoginRequired", "ClientLoginRequired", "ReloginAttemptExceeded", "ChallengeRequired",
                "ChallengeUnknownStep", "ChallengeError", "ChallengeRedirection", "SelectContactPointRecoveryForm",
                "RecaptchaChallengeForm", "SubmitPhoneNumberForm", "AccountSuspended", "TermsAccept",
                "TermsUnblock", "BadPassword", "BadCredentials", "TwoFactorRequired"}
_THROTTLE_ERRORS = {"PleaseWaitFewMinutes", "RateLimitError", "ClientThrottledError", "FeedbackRequired",
                    "SentryBlock"}
_CHALLENGE_ERRORS = {"ChallengeRequired", "ChallengeUnknownStep", "ChallengeError", "ChallengeRedirection",
                     "SelectContactPointRecoveryForm", "RecaptchaChallengeForm", "SubmitPhoneNumberForm"}
_NET_ERRORS = {"ClientConnectionError", "ClientRequestTimeout", "ConnectionError", "ConnectTimeout",
               "ReadTimeout", "ConnectError", "ProxyError", "SSLError"}


class LoginError(Exception):
    """Login jarayonidagi tushunarli xato (matni egasiga ko'rsatiladi)."""


def normalize_proxy(raw: str) -> str:
    """Provayderlar turli ko'rinishda beradi — instagrapi tushunadigan URL'ga keltiradi:
    ip:port:login:parol -> http://login:parol@ip:port;  login:parol@ip:port / ip:port -> http://..."""
    p = (raw or "").strip().strip("\"'")
    if not p or "://" in p:
        return p
    m = re.fullmatch(r"([^:/@\s]+):(\d{2,5}):([^:@\s]+):(\S+)", p)
    if m:
        host, port, user, password = m.groups()
        return f"http://{user}:{password}@{host}:{port}"
    return f"http://{p}"


PROXY = normalize_proxy(config.IG_PROXY)


def mask_proxy(p: str) -> str:
    return re.sub(r"(://)[^@/\s]+@", r"\1***@", p or "")


def _scrub(text: str) -> str:
    """Xato matnidan proksi login/paroli olib tashlanadi."""
    text = text or ""
    for secret in {config.IG_PROXY, PROXY}:
        if secret:
            text = text.replace(secret, mask_proxy(secret))
    return re.sub(r"(://)[^@/\s:]+:[^@/\s]+@", r"\1***@", text)


def _net_explain(e: BaseException) -> str:
    detail = _scrub(" ".join(str(e).split()))[:220]
    low = f"{type(e).__name__} {detail}".lower()
    if "407" in low or "proxy authentication" in low:
        why = "proksi login yoki paroli noto'g'ri (407)"
    elif "proxy" in low or "socks" in low:
        why = "proksiga ulanib bo'lmadi"
    elif "timed out" in low or "timeout" in low:
        why = "Instagram javob bermadi (vaqt tugadi)"
    else:
        why = "Instagram'ga ulanib bo'lmadi"
    if PROXY:
        hint = ("IG_PROXY ishlatilyapti — manzil, port, login va parolni tekshiring yoki IG_PROXY ni vaqtincha "
                "bo'shatib ko'ring.")
    else:
        hint = "IG_PROXY bo'sh — server Instagram'ga to'g'ridan-to'g'ri ulana olmadi."
    return f"{why}. {hint} Tekshirish: /ig → 🌐 Ulanish testi.\n({type(e).__name__}: {detail})"


def _names(e: BaseException) -> set[str]:
    return {c.__name__ for c in type(e).__mro__}


def _is(e: BaseException, name: str) -> bool:
    return name in _names(e)


def error_kind(e: BaseException) -> str:
    """auth — qayta login kerak; throttle — Instagram cheklovi; other — vaqtinchalik xato."""
    names = _names(e)
    if names & _AUTH_ERRORS:
        return "auth"
    if names & _THROTTLE_ERRORS:
        return "throttle"
    return "other"


NEEDS_UPGRADE_HELP = (
    "Instagram «ilova versiyasi eski» dedi. Bu instagrapi kutubxonasining hozirgi ma'lum xatosi (GitHub #2971): "
    "Instagram aslida boshqa narsani so'ragan — odatda yangi joydan (server IP'sidan) kirishni tasdiqlash, "
    "email kod yoki parol noto'g'ri.\n"
    "Nima qilish kerak:\n"
    "1) Telefoningizda Instagram ilovasini oching. «Bu siz edingizmi?» chiqsa — «Ha, men edim» ni bosing va "
    "/iglogin ni qaytadan yuboring.\n"
    "2) Bo'lmasa /igsession usuli bilan ulang — u login bosqichini umuman chetlab o'tadi "
    "(yo'riqnoma: /ig → 🔑 Ulash)."
)


def explain(e: BaseException) -> str:
    names = _names(e)
    if isinstance(e, LoginError):
        return str(e)
    low = str(e).lower()
    if "ClientNeedsUpgradeError" in names or "needs_upgrade" in low or "out of date" in low:
        return NEEDS_UPGRADE_HELP
    if names & {"BadPassword", "BadCredentials"}:
        return "login yoki parol noto'g'ri"
    if "TwoFactorRequired" in names:
        return "ikki bosqichli himoya kodi noto'g'ri yoki eskirgan"
    if names & _CHALLENGE_ERRORS:
        return ("Instagram qo'shimcha tasdiqlash so'rayapti — telefoningizda Instagram ilovasini oching, "
                "«Bu men edim» ni bosing va qayta urinib ko'ring (yoki /igsession usuli)")
    if names & _THROTTLE_ERRORS:
        return "Instagram vaqtincha cheklov qo'ydi — 15-30 daqiqadan keyin urinib ko'ring"
    if names & {"LoginRequired", "ClientLoginRequired", "ReloginAttemptExceeded"}:
        return "sessiya yaroqsiz — qayta kiring"
    if "UserNotFound" in names:
        return "bunday Instagram akkaunt topilmadi"
    if names & {"ProxyAddressIsBlocked", "IPBlocked"}:
        return "bu IP manzil Instagram tomonidan bloklangan (.env: IG_PROXY)"
    if names & _NET_ERRORS or "proxyerror" in low:
        return _net_explain(e)
    msg = _scrub(" ".join(str(e).split()))[:150]
    return type(e).__name__ + (f": {msg}" if msg else "")


MIN_LIB = (3, 2, 19)   # eskiroq versiyalarda login buzilgan (Instagram «ilova eski» deydi)


@functools.lru_cache(maxsize=1)
def lib_version() -> str | None:
    try:
        return importlib.metadata.version("instagrapi")
    except importlib.metadata.PackageNotFoundError:
        return None


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def lib_problem() -> str | None:
    v = lib_version()
    if v is None:
        return "instagrapi kutubxonasi o'rnatilmagan — terminalda: pip install -r requirements.txt"
    if _vtuple(v) < MIN_LIB:
        return (f"instagrapi eski ({v}) — bu versiyada login ishlamaydi. Yangilang: "
                "pip install -U -r requirements.txt")
    return None


def _when(ts: float | None) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo(config.TIMEZONE)).strftime("%d.%m %H:%M") if ts else ""


def _esc(s) -> str:
    return html.escape(str(s or ""))


# ---------- Instagram javobini tushunish (sof funksiyalar — testlanadi) ----------
@dataclass
class Item:
    item_id: str
    sender_id: str
    out: bool                 # o'zingiz yozgansiz
    ts: float                 # soniya
    kind: str                 # "" — matn; rasm, video, ovozli xabar, post, reels, storis, havola, like, ...
    text: str = ""
    ephemeral: bool = False   # o'chib ketadigan (bir marta / qayta ko'rish mumkin bo'lgan) media
    view_mode: str = ""       # once | replayable
    url: str = ""             # ephemeral media hozir yuklab olinadigan bo'lsa — manzili
    width: int = 0
    height: int = 0
    duration: float = 0.0


@dataclass
class Thread:
    thread_id: str
    title: str
    group: bool
    pending: bool                                          # «So'rovlar» papkasida (message request)
    users: dict[str, tuple[str, str]] = field(default_factory=dict)   # pk -> (username, to'liq ism)
    items: list[Item] = field(default_factory=list)                  # eskidan yangiga

    def person(self, uid: str) -> tuple[str, str]:
        return self.users.get(str(uid), ("", ""))


def _ts(value) -> float:
    """Instagram vaqti odatda mikrosoniyada (16 xona)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    if n > 1e14:
        return n / 1e6
    if n > 1e11:
        return n / 1e3
    return n


def _best(cands) -> dict | None:
    """Eng yuqori sifatli variant (eni x bo'yi eng katta)."""
    good = [c for c in cands or [] if isinstance(c, dict) and c.get("url")]
    return max(good, key=lambda c: int(c.get("width") or 0) * int(c.get("height") or 0), default=None)


def _describe(it: dict) -> tuple[str, str]:
    itype = it.get("item_type") or ""
    sub = lambda key: it.get(key) if isinstance(it.get(key), dict) else {}  # noqa: E731
    if itype == "text":
        return "", it.get("text") or ""
    if itype == "link":
        return "havola", sub("link").get("text") or ""
    if itype == "like":
        return "like", "❤️"
    if itype == "media":
        return ("video" if sub("media").get("media_type") == 2 else "rasm"), ""
    if itype in ("raven_media", "visual_media"):
        return "rasm", ""
    if itype == "voice_media":
        return "ovozli xabar", ""
    if itype == "animated_media":
        return "GIF", ""
    if itype in ("clip", "xma_clip"):
        return "reels", ""
    if itype in ("media_share", "xma_media_share", "generic_xma", "xma", "felix_share"):
        return "post", ""
    if itype in ("story_share", "xma_story_share"):
        return "storis", ""
    if itype == "reel_share":
        return "storisga javob", sub("reel_share").get("text") or ""
    if itype == "action_log":
        return "amal", sub("action_log").get("description") or ""
    if itype == "placeholder":
        return "xabar", sub("placeholder").get("message") or ""
    if itype == "video_call_event":
        return "qo'ng'iroq", sub("video_call_event").get("description") or ""
    return itype or "xabar", it.get("text") or ""


def parse_item(it: dict, viewer_id: str = "") -> Item | None:
    item_id = str(it.get("item_id") or "")
    if not item_id:
        return None
    sender = str(it.get("user_id") or "")
    out = bool(it.get("is_sent_by_viewer")) or (bool(viewer_id) and sender == str(viewer_id))
    kind, text = _describe(it)
    item = Item(item_id, sender, out, _ts(it.get("timestamp")), kind, text)
    vm = it.get("visual_media")
    if isinstance(vm, dict) and str(vm.get("view_mode") or "").lower() != "permanent":
        item.ephemeral = True
        item.view_mode = str(vm.get("view_mode") or "once").lower()
        media = vm.get("media") if isinstance(vm.get("media"), dict) else {}
        video = _best(media.get("video_versions"))
        image = _best((media.get("image_versions2") or {}).get("candidates"))
        item.kind = "video" if video or media.get("media_type") == 2 else "rasm"
        best = video or image
        if best:   # ochilgan yoki muddati o'tgan bo'lsa Instagram manzil bermaydi
            if not video:
                item.kind = "rasm"
            item.url = best["url"]
            item.width = int(best.get("width") or media.get("original_width") or 0)
            item.height = int(best.get("height") or media.get("original_height") or 0)
            if video:
                try:
                    item.duration = float(media.get("video_duration") or vm.get("playback_duration_secs") or 0)
                except (TypeError, ValueError):
                    item.duration = 0.0
    return item


def parse_thread(t: dict, viewer_id: str = "") -> Thread:
    users: dict[str, tuple[str, str]] = {}
    for u in t.get("users") or []:
        pk = str(u.get("pk") or u.get("pk_id") or u.get("id") or "")
        if pk:
            users[pk] = (u.get("username") or "", u.get("full_name") or "")
    title = t.get("thread_title") or ", ".join(full or name for name, full in users.values()) \
        or str(t.get("thread_id") or "")
    items = [x for x in (parse_item(i, viewer_id) for i in t.get("items") or [] if isinstance(i, dict)) if x]
    items.sort(key=lambda x: x.ts)
    return Thread(str(t.get("thread_id") or ""), title, bool(t.get("is_group")), bool(t.get("_pending")),
                  users, items)


def content_of(it: Item) -> str:
    """Xabar mazmuni (AI va bildirishnomalar uchun)."""
    if it.ephemeral:
        return f"[🔥 bir martalik {it.kind}]"
    if it.kind == "like":
        return "❤️"
    if it.kind in ("", "havola"):
        return it.text
    return f"[{it.kind}]" + (f" {it.text}" if it.text else "")


def who_html(full: str, username: str) -> str:
    s = f"<b>{_esc(full or username or 'Nomaʼlum')}</b>"
    if username:
        s += f' (<a href="https://instagram.com/{_esc(username)}">@{_esc(username)}</a>)'
    return s


def media_caption(th: Thread, it: Item) -> str:
    uname, full = th.person(it.sender_id)
    mode = "bir marta ko'riladigan" if it.view_mode == "once" else "o'chib ketadigan"
    lines = [f"📸 <b>Instagram</b> — 🔥 {mode} {it.kind}", f"👤 {who_html(full, uname)}"]
    if th.group:
        lines.append(f"👥 {_esc(th.title)}")
    lines.append(f"🕒 {_when(it.ts)}")
    lines.append("✅ Saqlab olindi. Instagram'da hali ochilmagan — yuboruvchiga hech narsa bildirilmadi.")
    return "\n".join(lines)


def doc_of(th: Thread, it: Item, ai_text: bool = False) -> dict:
    """Arxiv hujjati (archive.build_html bilan bir xil ko'rinishda)."""
    uname, full = th.person(it.sender_id)
    return {"thread_id": th.thread_id, "msg_id": it.item_id, "date": it.ts, "out": it.out,
            "sender": "Siz" if it.out else (full or uname or it.sender_id), "sender_id": it.sender_id,
            "username": "" if it.out else uname, "kind": it.kind, "text": it.text,
            "media": None if it.kind in _TEXT_KINDS else it.kind, "view_once": it.ephemeral, "ai": ai_text}


# ---------- holat ----------
class _State:
    client = None            # instagrapi.Client (ulangan bo'lsa)
    username = ""
    user_id = ""
    problem = ""             # oxirgi muammo (panelda ko'rinadi)
    last_check = 0.0
    paused_until = 0.0
    polls = 0
    fail_streak = 0
    saved_at = 0.0
    started = time.time()    # avtojavob va bildirishnomalar faqat shundan keyin kelgan xabarlarga


st = _State()
_alert = None        # async (matn) -> None
_send_media = None   # async (data, filename, caption_html, video: dict | None, as_file: bool) -> bool
_notify = None       # async (thread_id, html) -> None  (tugmalar: javob, AI jim, HTML)
_wake = asyncio.Event()
_check_lock = asyncio.Lock()
_api_lock = asyncio.Lock()       # instagrapi klienti bir vaqtda bitta so'rov yuboradi
_login_lock = asyncio.Lock()
_code: concurrent.futures.Future | None = None   # login paytida kutilayotgan tasdiqlash kodi
_known: dict[str, set[str]] = {}                 # thread_id -> oxirgi tekshiruvda ko'rilgan xabarlar
_missing: dict[tuple[str, str], int] = {}        # arxivda bor, lekin Instagram'da ko'rinmay qolgan xabarlar
_muted_until: dict[str, float] = {}              # siz o'zingiz yozgan chatlar — AI jim
_ai_sent: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))   # AI yuborgan matnlar
_alerted: dict[str, float] = {}


def setup(alert, send_media, notify) -> None:
    global _alert, _send_media, _notify
    _alert, _send_media, _notify = alert, send_media, notify


def wake() -> None:
    _wake.set()


def connected() -> bool:
    return st.client is not None


async def _say(text: str) -> None:
    if _alert:
        try:
            await _alert(text)
        except Exception:
            log.warning("Instagram ogohlantirishi botga yuborilmadi")


async def _say_once(key: str, text: str, every: float = 3600) -> None:
    now = time.time()
    if now - _alerted.get(key, 0) >= every:
        _alerted[key] = now
        await _say(text)


# ---------- login (instagrapi sinxron — alohida oqimda ishlaydi) ----------
def _device_only(settings: dict | None) -> dict | None:
    """Eski sessiyadan faqat «qurilma» ma'lumotlari: Instagram tanish qurilmaga kamroq savol beradi."""
    if not settings:
        return None
    keep = ("uuids", "device_settings", "country", "country_code", "locale", "timezone_offset")
    return {k: settings[k] for k in keep if k in settings} or None


_APP_KEYS = ("app_version", "version_code", "bloks_versioning_id")


def _with_current_app(settings: dict) -> dict:
    """Saqlangan sessiyadagi Instagram ilova versiyasi olib tashlanadi — kutubxona yangilanganda uning joriy
    versiyasi ishlatilsin (aks holda eski versiya qolib, Instagram «ilova eski» deydi). Qurilma va UUID qoladi."""
    s = dict(settings)
    dev = s.get("device_settings")
    if isinstance(dev, dict):
        s["device_settings"] = {k: v for k, v in dev.items() if k not in _APP_KEYS}
    s.pop("user_agent", None)   # qurilma + joriy ilova versiyasidan qayta yasaladi
    return s


def _new_client(settings: dict | None = None):
    from instagrapi import Client
    cl = Client()
    if settings:
        cl.set_settings(_with_current_app(settings))
    if PROXY:
        cl.set_proxy(PROXY)
    cl.delay_range = [1, 3]   # so'rovlar orasida tasodifiy pauza — odamga o'xshab
    return cl


def _challenge_prompt(choice) -> str:
    value = str(getattr(choice, "value", choice))
    where = {"0": "SMS", "1": "email"}.get(value, "SMS yoki email")
    return (f"📩 Instagram tasdiqlash kodini {where} orqali yubordi.\n"
            "Kodni shu yerga yozing (5 daqiqa ichida). Bekor qilish: «bekor»")


def _two_factor_prompt(info: dict) -> str:
    if info.get("totp_two_factor_on"):
        return ("🔐 Ikki bosqichli himoya: autentifikator ilovadagi (Google Authenticator va h.k.) 6 xonali kodni "
                "shu yerga yozing. Bekor qilish: «bekor»")
    phone = info.get("obfuscated_phone_number") or ""
    if phone or info.get("sms_two_factor_on"):
        return (f"🔐 Ikki bosqichli himoya: {('…' + phone + ' raqamiga ') if phone else ''}SMS kod keldi — "
                "kodni shu yerga yozing. Bekor qilish: «bekor»")
    return ("🔐 Ikki bosqichli himoya: SMS'dagi, WhatsApp'dagi yoki autentifikator ilovadagi kodni "
            "(yoki zaxira kodlardan birini) shu yerga yozing. Bekor qilish: «bekor»")


def _no_password_change(_username):
    raise LoginError("Instagram parolni almashtirishni talab qilyapti — ilovada almashtiring va qayta kiring")


def _login_sync(username: str, password: str, ask, old: dict | None):
    cl = _new_client(_device_only(old))
    cl.challenge_code_handler = lambda _u, choice: ask(_challenge_prompt(choice))
    cl.change_password_handler = _no_password_change
    try:
        cl.login(username, password)
    except Exception as e:
        if not _is(e, "TwoFactorRequired"):
            raise
        info = (getattr(cl, "last_json", None) or {}).get("two_factor_info") or {}
        # instagrapi 3.x kodni o'zi yuboradi (yangi Bloks login: SMS, autentifikator ilova, zaxira kod)
        cl.login(username, password, verification_code=ask(_two_factor_prompt(info)))
    return cl


def _session_sync(sessionid: str, old: dict | None):
    cl = _new_client(_device_only(old))
    cl.login_by_sessionid(sessionid)
    return cl


def _current_username(cl) -> str:
    res = cl.private_request("accounts/current_user/", params={"edit": "true"})
    return ((res or {}).get("user") or {}).get("username") or ""


def waiting_code() -> bool:
    f = _code
    return f is not None and not f.done()


def submit_code(text: str) -> bool:
    """Egasi botga yozgan tasdiqlash kodi. Kod kutilmayotgan yoki kodga o'xshamasa — False."""
    code = re.sub(r"[\s\-]", "", text or "")
    f = _code
    if f is None or f.done() or not CODE_RE.fullmatch(code):
        return False
    try:
        f.set_result(code)
    except concurrent.futures.InvalidStateError:
        return False
    return True


def cancel_code() -> bool:
    f = _code
    if f is None or f.done():
        return False
    try:
        f.set_exception(LoginError("login bekor qilindi"))
    except concurrent.futures.InvalidStateError:
        return False
    return True


def _asker(loop, say):
    """Oqim ichidan egasidan kod so'raydi va u botga yozguncha kutadi."""
    def ask(prompt: str) -> str:
        global _code
        fut: concurrent.futures.Future = concurrent.futures.Future()
        _code = fut
        asyncio.run_coroutine_threadsafe(say(prompt), loop)
        try:
            return fut.result(timeout=CODE_TIMEOUT)
        except concurrent.futures.TimeoutError:
            raise LoginError("kod 5 daqiqa ichida yuborilmadi — /iglogin ni qaytadan yuboring") from None
        finally:
            if _code is fut:
                _code = None
    return ask


async def _saved_settings() -> dict | None:
    try:
        doc = await storage.ig_get_session()
        return json.loads(doc["settings"]) if doc and doc.get("settings") else None
    except Exception:
        return None


async def _save_session() -> None:
    cl = st.client
    if cl is None:
        return
    try:
        settings = json.dumps(cl.get_settings(), default=str)
        await storage.ig_set_session(username=st.username, user_id=st.user_id, settings=settings, active=True)
        st.saved_at = time.time()
    except Exception as e:
        log.warning("Instagram sessiyasi bazaga yozilmadi (%s)", type(e).__name__)


async def _connected(cl, typed: str = "") -> None:
    # Email yoki telefon raqam bilan kirilgan bo'lsa — haqiqiy username Instagram'dan olinadi
    if typed:
        name = typed if USERNAME_RE.fullmatch(typed) and not typed.isdigit() else ""
    else:
        name = getattr(cl, "username", "") or ""   # sessionid bilan kirilganda instagrapi o'zi aniqlaydi
    if not name:
        try:
            async with _api_lock:
                name = await asyncio.to_thread(_current_username, cl)
        except Exception:
            name = typed
    st.client, st.username, st.user_id = cl, name, str(getattr(cl, "user_id", "") or "")
    st.problem, st.fail_streak, st.paused_until = "", 0, 0.0
    await _save_session()
    wake()


async def login(username: str, password: str, say) -> str:
    """say(text) — egasiga xabar (kod so'rash). Natija matnini qaytaradi."""
    if problem := lib_problem():
        return f"❌ {problem}"
    if _login_lock.locked():
        return "⏳ Login allaqachon ketyapti — Instagram yuborgan kodni shu yerga yozing."
    async with _login_lock:
        ask = _asker(asyncio.get_running_loop(), say)
        old = await _saved_settings()
        try:
            cl = await asyncio.to_thread(_login_sync, username.strip().lstrip("@"), password, ask, old)
        except Exception as e:
            log.warning("Instagram login bo'lmadi: %s: %s", type(e).__name__, _scrub(str(e))[:300])
            return f"❌ Instagram'ga kirilmadi: {explain(e)}"
        await _connected(cl, username.strip().lstrip("@"))
    return f"✅ Instagram ulandi: @{st.username}\nPanel: /ig"


def clean_sessionid(raw: str) -> str:
    sid = (raw or "").strip().strip("\"'")
    sid = re.sub(r"^sessionid\s*[=:]\s*", "", sid, flags=re.IGNORECASE)
    return sid.split(";")[0].strip().strip("\"'")


async def login_session(raw: str) -> str:
    if problem := lib_problem():
        return f"❌ {problem}"
    sid = clean_sessionid(raw)
    if not re.match(r"^\d+(?:%3A|:)\S{10,}$", sid):
        return ("❌ Bu sessionid'ga o'xshamaydi. U raqamlar bilan boshlanadi, masalan: "
                "<code>1234567890%3AAbCdEf...</code>")
    async with _login_lock:
        old = await _saved_settings()
        try:
            cl = await asyncio.to_thread(_session_sync, sid, old)
        except Exception as e:
            log.warning("Instagram sessionid bilan kirilmadi: %s: %s", type(e).__name__, _scrub(str(e))[:300])
            return f"❌ Sessiya bilan kirilmadi: {_esc(explain(e))}"
        await _connected(cl)
    return f"✅ Instagram ulandi: @{_esc(st.username)}\nPanel: /ig"


async def logout() -> str:
    cl, st.client = st.client, None
    st.username, st.user_id, st.problem = "", "", ""
    if cl is not None:
        try:
            async with _api_lock:
                await asyncio.to_thread(cl.logout)
        except Exception as e:
            log.info("Instagram'dan chiqish so'rovi o'tmadi (%s)", type(e).__name__)
    try:
        await storage.ig_delete_session()
    except Exception as e:
        log.warning("Instagram sessiyasi bazadan o'chirilmadi (%s)", type(e).__name__)
    return "🚪 Instagram akkauntidan chiqildi, sessiya o'chirildi."


async def restore() -> bool:
    """Bazadagi sessiya bilan qayta ulanadi (login so'ralmaydi)."""
    if lib_problem():
        return False
    try:
        doc = await storage.ig_get_session()
    except Exception as e:
        log.warning("Instagram sessiyasi o'qilmadi (%s)", type(e).__name__)
        return False
    if not doc or not doc.get("settings") or doc.get("active") is False:
        return False
    try:
        cl = await asyncio.to_thread(_new_client, json.loads(doc["settings"]))
    except Exception as e:
        st.problem = f"sessiya tiklanmadi: {explain(e)}"
        log.warning("Instagram sessiyasi tiklanmadi (%s)", type(e).__name__)
        return False
    st.client, st.username = cl, doc.get("username") or ""
    st.user_id = str(doc.get("user_id") or getattr(cl, "user_id", "") or "")
    st.saved_at = time.time()
    log.info("Instagram sessiyasi tiklandi: @%s", st.username)
    return True


# ---------- Direct'ni tekshirish ----------
_PARAMS = {"visual_message_return_type": "unseen", "thread_message_limit": str(MESSAGE_LIMIT),
           "persistentBadging": "true", "limit": str(THREAD_LIMIT), "is_prefetching": "false"}


def _fetch_sync(cl, pending: bool) -> list[dict]:
    """Xom JSON: instagrapi modellari Instagram o'zgarganda tez-tez buziladi, shuning uchun o'zimiz o'qiymiz.
    Bu so'rovlar xabarlarni «o'qildi» qilmaydi va bir martalik mediani ochmaydi."""
    threads = []
    res = cl.private_request("direct_v2/inbox/", params=dict(_PARAMS))
    threads += [dict(t, _pending=False) for t in ((res or {}).get("inbox") or {}).get("threads") or []]
    if pending:
        res = cl.private_request("direct_v2/pending_inbox/", params=dict(_PARAMS))
        threads += [dict(t, _pending=True) for t in ((res or {}).get("inbox") or {}).get("threads") or []]
    return threads


async def _download(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": _UA}) as http:
        async with http.stream("GET", url) as r:
            r.raise_for_status()
            mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_MEDIA_BYTES:
                    raise ValueError(f"fayl juda katta (>{config.MAX_MEDIA_MB} MB, .env: MAX_MEDIA_MB)")
            return bytes(buf), mime


def _ext(kind: str, mime: str) -> str:
    if kind == "video":
        return ".mp4"
    return {"image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/avif": ".avif"}.get(mime, ".jpg")


async def _save_ephemeral(threads: list[Thread]) -> int:
    saved = 0
    for th in threads:
        for it in th.items:
            if not it.ephemeral or it.out or await storage.ig_saved_has(it.item_id):
                continue
            uname, full = th.person(it.sender_id)
            who = full or uname or th.title
            if not it.url:
                # siz ochib qo'ygansiz (yoki muddati o'tgan) — Instagram endi bermaydi; faqat yangilari haqida aytamiz
                if it.ts >= st.started:
                    await storage.ig_saved_add(it.item_id, {"status": "missed", "thread_id": th.thread_id,
                                                            "sender": who, "kind": it.kind, "date": it.ts})
                    await _say(f"😔 Instagram: {who} bir martalik {it.kind} yubordi, lekin u allaqachon ochilgan — "
                               "saqlab bo'lmadi. Keyingi safar bildirishnoma kelsa, avval botni tekshiring.")
                continue
            try:
                data, mime = await _download(it.url)
            except Exception as e:
                log.warning("Bir martalik media yuklab olinmadi (%s) — keyingi tekshiruvda yana urinaman",
                            type(e).__name__)
                continue
            ext = _ext(it.kind, mime)
            video = ({"duration": it.duration, "w": it.width, "h": it.height} if it.kind == "video" else None)
            ok = await _send_media(data, f"instagram_{it.item_id[-12:]}{ext}", media_caption(th, it), video,
                                   it.kind != "video" and ext not in (".jpg", ".png"))
            if ok:
                await storage.ig_saved_add(it.item_id, {"status": "saved", "thread_id": th.thread_id,
                                                        "sender": who, "kind": it.kind, "date": it.ts})
                saved += 1
                log.info("Instagram: %s yuborgan bir martalik %s saqlandi", who, it.kind)
    return saved


def unsent_text(th: Thread, docs: list[dict]) -> str:
    first = docs[0]
    who = who_html(first.get("sender") or "", first.get("username") or "")
    lines = [f"🗑 <b>Instagram: xabar qaytarib olindi</b>", f"👤 {who}"]
    if th.group:
        lines.append(f"👥 {_esc(th.title)}")
    lines.append("")
    for d in docs[:15]:
        body = d.get("text") or (f"[{d['media']}]" if d.get("media") else "xabar")
        lines.append(f"• <i>{_when(d.get('date'))}</i> — {_esc(body[:400])}")
    if len(docs) > 15:
        lines.append(f"… yana {len(docs) - 15} ta")
    return "\n".join(lines)


async def _archive_thread(th: Thread) -> None:
    if not th.items:
        return
    ai_texts = set(_ai_sent[th.thread_id])
    docs = [doc_of(th, it, it.out and it.text in ai_texts) for it in th.items]
    added = await storage.ig_archive_add_many(docs)
    usernames = [u for u, _ in th.users.values() if u]
    await storage.ig_chat_touch(th.thread_id, th.title, usernames, th.group, th.items[-1].ts, added)
    # Qaytarib olingan xabarlar: shu oynadagi vaqt oralig'ida arxivda bor, lekin Instagram'da yo'q
    present = {it.item_id for it in th.items}
    gone = []
    for d in await storage.ig_archive_window(th.thread_id, th.items[0].ts):
        key = (th.thread_id, d["msg_id"])
        if d["msg_id"] in present or d.get("view_once") or d.get("kind") in _SKIP_UNSENT:
            _missing.pop(key, None)
            continue
        _missing[key] = _missing.get(key, 0) + 1
        if _missing[key] >= MISSING_CONFIRM:   # bir martalik xato javob sabab noto'g'ri ogohlantirmaslik uchun
            _missing.pop(key, None)
            gone.append(d)
    if not gone:
        return
    await storage.ig_archive_mark_deleted(th.thread_id, [d["msg_id"] for d in gone], time.time())
    theirs = [d for d in gone if not d.get("out")]
    if theirs and _notify:
        await _notify(th.thread_id, unsent_text(th, theirs))


def _history(th: Thread, ai_ids: set[str]) -> list[dict]:
    ai_texts = set(_ai_sent[th.thread_id])
    out = []
    for it in th.items[-config.HISTORY_LIMIT:]:
        c = content_of(it)
        if not c:
            continue
        if not it.out:
            out.append({"role": "user", "content": c})
        elif it.item_id in ai_ids or it.text in ai_texts:
            out.append({"role": "assistant", "content": c})
        else:
            out.append({"role": "assistant", "content": f"{ai.OWNER_WROTE_PREFIX} {c}"})
    return out


def _send_sync(cl, thread_id: str, text: str) -> None:
    try:
        cl.direct_send(text, thread_ids=[int(thread_id)])
    except Exception as e:
        if _is(e, "ValidationError"):   # xabar ketdi, faqat javobni instagrapi modeli o'qiy olmadi
            return
        raise


async def _send_text(thread_id: str, text: str, by_ai: bool = False) -> None:
    cl = st.client
    if cl is None:
        raise LoginError("Instagram ulanmagan (/ig)")
    if by_ai:
        _ai_sent[thread_id].append(text)
    async with _api_lock:
        await asyncio.to_thread(_send_sync, cl, thread_id, text)


async def send_text(thread_id: str, text: str) -> str:
    """Egasi botdan yozgan javob — Instagram chatiga uning nomidan."""
    try:
        await _send_text(thread_id, text)
    except Exception as e:
        log.warning("Instagram'ga xabar yuborilmadi (%s)", type(e).__name__)
        return f"❌ Instagram'ga yuborilmadi: {explain(e)}"
    mute(thread_id)
    return f"✅ Instagram'ga yuborildi. Bu chatda AI {config.OWNER_TAKEOVER_MINUTES} daqiqa jim turadi."


def mute(thread_id: str, minutes: int | None = None) -> None:
    _muted_until[thread_id] = time.time() + (minutes or config.OWNER_TAKEOVER_MINUTES) * 60


def notify_text(th: Thread, items: list[Item], result: dict | None) -> str:
    uname, full = th.person(items[-1].sender_id)
    urgent = bool(result and (result.get("urgent") or result.get("call_owner")))
    head = "🔴 <b>SHOSHILINCH — Instagram</b>" if urgent else "📸 <b>Instagram: yangi xabar</b>"
    if th.pending:
        head += " <i>(so'rov — Requests papkasida)</i>"
    lines = [head, f"👤 {who_html(full, uname)}"]
    if th.group:
        lines.append(f"👥 {_esc(th.title)}")
    content = "\n".join(c for c in (content_of(i) for i in items) if c)
    lines.append(f"\n💬 <i>{_esc(content[:1000])}</i>")
    if result:
        if result.get("summary"):
            lines.append(f"\n📝 <b>Xulosa:</b> {_esc(result['summary'])}")
        if result.get("reply"):
            lines.append(f"\n🤖 <b>Anna javobi:</b> {_esc(result['reply'])}")
        if result.get("send_error"):
            lines.append(f"\n⚠️ Javob yuborilmadi: {_esc(result['send_error'])}")
    return "\n".join(lines)


async def _ai_reply(th: Thread, status) -> dict | None:
    key = f"ig{th.thread_id}"
    ok, reason = await usage.check(key)
    if not ok:
        if usage.should_alert(reason, key):
            await _say(f"💸 Instagram avtojavob: {reason}. Javob berilmadi, xabar botga yuborildi.")
        return None
    ai_ids: set[str] = set()
    if runtime.get("ig_archive"):
        try:
            ai_ids = await storage.ig_ai_ids(th.thread_id)
        except Exception:
            ai_ids = set()
    context = (f"{status.ai_context}\nBu suhbat Instagram Direct'da (Telegram emas) — "
               "Telegram'ga oid narsalarni aytma. Suhbatdosh turi: tanish.")
    try:
        result = await ai.generate(_history(th, ai_ids), context)
        await usage.record(key, "chat", result["tokens"])
    except Exception as e:
        log.warning("Instagram avtojavob: AI javob bermadi (%s)", type(e).__name__)
        await _say_once("ai", f"⚠️ Instagram avtojavob: AI ishlamadi ({type(e).__name__}). Xabarlar botga keladi.")
        return None
    try:
        await _send_text(th.thread_id, result["reply"], by_ai=True)
    except Exception as e:
        log.warning("Instagram avtojavob yuborilmadi (%s)", type(e).__name__)
        result["send_error"], result["reply"] = explain(e), None
    return result


async def _on_incoming(th: Thread, items: list[Item]) -> bool:
    """Yangi kelgan xabarlar: avtojavob va/yoki botga bildirishnoma. AI javob bergan bo'lsa True."""
    auto = runtime.get("ig_autoreply")
    status = await availability.get_status() if auto else None
    result = None
    if (auto and status.reply and not th.group and not th.pending
            and _muted_until.get(th.thread_id, 0) <= time.time()):
        result = await _ai_reply(th, status)
    want = (result is not None or runtime.get("ig_forward")
            or (auto and status.notify and not th.group))
    if want and _notify:
        await _notify(th.thread_id, notify_text(th, items, result))
    return bool(result and result.get("reply"))


async def _process_fresh(threads: list[Thread]) -> int:
    replied = 0
    now = time.time()
    for th in threads:
        known = _known.get(th.thread_id, set())
        fresh = [it for it in th.items if it.item_id not in known and it.ts >= st.started]
        _known[th.thread_id] = {it.item_id for it in th.items}
        if not fresh:
            continue
        ai_texts = set(_ai_sent[th.thread_id])
        for it in fresh:
            if it.out and it.text not in ai_texts:   # siz o'zingiz Instagram'da yozdingiz
                _muted_until[th.thread_id] = now + config.OWNER_TAKEOVER_MINUTES * 60
                try:
                    await availability.mark_owner_active()
                except Exception:
                    pass
        incoming = [it for it in fresh if not it.out and it.kind != "amal"]
        if not incoming:
            continue
        try:
            replied += await _on_incoming(th, incoming)
        except Exception:
            log.exception("Instagram xabari qayta ishlanmadi")
    return replied


def _any_on() -> bool:
    return any(runtime.get(k) for k in TOGGLES)


async def check_once() -> dict:
    """Bitta tekshiruv. Natija: {"threads", "saved", "replied"}. Xatoni yuqoriga uzatadi."""
    async with _check_lock:
        cl = st.client
        if cl is None:
            return {"threads": 0, "saved": 0, "replied": 0}
        st.polls += 1
        async with _api_lock:
            raw = await asyncio.to_thread(_fetch_sync, cl, st.polls % PENDING_EVERY == 1)
        threads = [parse_thread(t, st.user_id) for t in raw]
        st.last_check, st.problem, st.fail_streak = time.time(), "", 0
        res = {"threads": len(threads), "saved": 0, "replied": 0}
        if runtime.get("ig_save"):
            res["saved"] = await _save_ephemeral(threads)
        if runtime.get("ig_archive"):
            for th in threads:
                try:
                    await _archive_thread(th)
                except Exception:
                    log.exception("Instagram chati arxivlanmadi")
        res["replied"] = await _process_fresh(threads)
        if time.time() - st.saved_at > SETTINGS_SAVE_EVERY:
            await _save_session()
        return res


async def _on_error(e: Exception) -> None:
    kind = error_kind(e)
    if kind == "auth":
        log.warning("Instagram sessiyasi yaroqsiz (%s)", type(e).__name__)
        st.client = None
        st.problem = "sessiya tugadi yoki Instagram tasdiqlash so'rayapti — qayta ulaning"
        try:
            await storage.ig_set_session(active=False)
        except Exception:
            pass
        await _say("⚠️ Instagram sessiyasi tugadi yoki Instagram tasdiqlash so'rayapti.\n"
                   "Bir martalik media hozircha SAQLANMAYDI! Qayta ulaning: /iglogin username parol "
                   "(yoki /igsession). Batafsil: /ig")
    elif kind == "throttle":
        st.paused_until = time.time() + THROTTLE_PAUSE
        st.problem = "Instagram «biroz kuting» dedi — tekshiruv vaqtincha to'xtatildi"
        log.warning("Instagram cheklovi (%s) — %d daqiqa kutiladi", type(e).__name__, THROTTLE_PAUSE // 60)
        await _say(f"⏸ Instagram cheklov qo'ydi ({type(e).__name__}) — {THROTTLE_PAUSE // 60} daqiqa tekshirilmaydi. "
                   "Tez-tez bo'lsa .env dagi IG_POLL_SECONDS ni oshiring.")
    else:
        st.fail_streak += 1
        st.problem = explain(e)
        log.warning("Instagram tekshirilmadi: %s", st.problem)
        if st.fail_streak == FAIL_ALERT:
            await _say(f"⚠️ Instagram {FAIL_ALERT} marta ketma-ket tekshirilmadi: {st.problem}")


async def check_now() -> str:
    """Botdagi «🔄 Hozir tekshirish» tugmasi."""
    if st.client is None:
        return "Instagram ulanmagan"
    try:
        res = await check_once()
    except Exception as e:
        await _on_error(e)
        return f"❌ {explain(e)}"[:190]
    return f"✅ {res['threads']} ta chat tekshirildi, saqlandi: {res['saved']}, javob: {res['replied']}"


IG_PROBE_URL = "https://i.instagram.com/"
IP_PROBE_URL = "https://api.ipify.org"


def _probe(url: str, proxy: str | None) -> tuple[bool, str, float]:
    """(yetib bordimi, qiymat yoki xato, soniya). Har qanday HTTP javob = server yetib bordi.
    instagrapi kabi requests ishlatiladi (socks5 ham PySocks orqali ishlaydi)."""
    import requests
    started = time.monotonic()
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": _UA},
                         proxies={"http": proxy, "https": proxy} if proxy else None)
    except Exception as e:
        return False, f"{type(e).__name__}: {_scrub(' '.join(str(e).split()))[:160]}", 0.0
    body = (r.text or "").strip()[:40] if url == IP_PROBE_URL and r.ok else ""
    return True, body or f"HTTP {r.status_code}", time.monotonic() - started


def _shown(res: tuple[bool, str, float]) -> str:
    ok, value, took = res
    return _esc(value) + (f" · {took:.1f} s" if ok else "")


async def net_check() -> str:
    """Botdagi «🌐 Ulanish testi»: server Instagram'ga to'g'ridan-to'g'ri va proksi orqali yetadimi."""
    try:
        import requests  # noqa: F401 — instagrapi bilan birga o'rnatiladi
    except ImportError:
        return "❌ requests kutubxonasi yo'q — pip install -r requirements.txt"
    mark = lambda res: "✅" if res[0] else "❌"  # noqa: E731
    ip = await asyncio.to_thread(_probe, IP_PROBE_URL, None)
    ig = await asyncio.to_thread(_probe, IG_PROBE_URL, None)
    lines = ["🌐 <b>Instagram ulanish testi</b>", "",
             f"{mark(ip)} Server IP: {_shown(ip)}",
             f"{mark(ig)} Server → Instagram (to'g'ridan-to'g'ri): {_shown(ig)}"]
    if not PROXY:
        lines.append("▫️ IG_PROXY: bo'sh")
        verdict = ("Ulanish joyida. Login baribir o'tmasa — Instagram server IP'sidan kirishni yoqtirmayapti: "
                   "/igsession usulini sinang yoki residential proksi qo'ying." if ig[0] else
                   "Server Instagram'ga ulana olmayapti — residential proksi kerak (IG_PROXY).")
        return "\n".join(lines + ["", f"➡️ {verdict}"])
    pip = await asyncio.to_thread(_probe, IP_PROBE_URL, PROXY)
    pig = await asyncio.to_thread(_probe, IG_PROBE_URL, PROXY)
    lines += ["", f"🧦 IG_PROXY: <code>{_esc(mask_proxy(PROXY))}</code>"]
    if PROXY != config.IG_PROXY.strip().strip("\"'"):
        lines.append("ℹ️ Proksi formati avtomatik to'g'rilandi (masalan ip:port:login:parol → "
                     "http://login:parol@ip:port)")
    lines += [f"{mark(pip)} Proksi IP: {_shown(pip)}", f"{mark(pig)} Proksi → Instagram: {_shown(pig)}"]
    if pig[0] and pip[0] and ip[0] and pip[1] == ip[1]:
        verdict = "Proksi javob beryapti, lekin chiquvchi IP server IP'si bilan bir xil — proksi ishlamayapti."
    elif pig[0]:
        verdict = "Proksi ishlayapti. Login baribir o'tmasa — /igsession usulini sinang."
    elif ig[0]:
        verdict = ("Proksi ishlamayapti, to'g'ridan-to'g'ri ulanish esa ishlayapti. IG_PROXY ni tuzating (manzil, "
                   "port, login, parol; ba'zi provayderlarda server IP'sini ruxsat etilganlar ro'yxatiga qo'shish "
                   "kerak) yoki vaqtincha bo'shatib qo'ying.")
    else:
        verdict = "Na to'g'ridan-to'g'ri, na proksi orqali ulanib bo'lmadi — server internetini va proksini tekshiring."
    return "\n".join(lines + ["", f"➡️ {verdict}"])


async def run() -> None:
    """Fon vazifasi: sessiyani tiklaydi va Direct'ni muntazam tekshiradi."""
    await asyncio.sleep(5)
    if lib_problem():
        log.info("Instagram: %s", lib_problem())
    elif not await restore():
        log.info("Instagram ulanmagan — botda /ig orqali ulang")
    while True:
        _wake.clear()
        if st.client is not None and _any_on() and time.time() >= st.paused_until:
            try:
                await check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await _on_error(e)
        delay = config.IG_POLL_SECONDS * random.uniform(0.8, 1.2)  # bir xil oraliqda emas — odamga o'xshab
        try:
            await asyncio.wait_for(_wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


# ---------- nazorat boti uchun ko'rinishlar ----------
LOGIN_HELP = (
    "🔑 <b>Instagram'ni ulash</b>\n\n"
    "💡 Bot serverda (Render va h.k.) ishlasa — <b>2-usul</b> ishonchliroq: Instagram server IP'sidan parol "
    "bilan kirishni ko'pincha to'xtatadi.\n\n"
    "<b>1-usul — login va parol:</b>\n"
    "<code>/iglogin username parol</code>\n"
    "• Xabaringiz darhol o'chiriladi, parol hech qayerga yozilmaydi (faqat sessiya saqlanadi).\n"
    "• Instagram SMS/email kod yoki ikki bosqichli himoya kodini so'rasa — bot so'raydi, kodni shu yerga yozasiz.\n\n"
    "<b>2-usul — sessiya (1-usul ishlamasa):</b>\n"
    "1) Kompyuterda brauzerda instagram.com ga kiring\n"
    "2) F12 → Application (Ilova) → Cookies → https://www.instagram.com\n"
    "3) <b>sessionid</b> qatoridagi qiymatni nusxalang\n"
    "4) <code>/igsession QIYMAT</code>\n\n"
    "⚠️ Bu norasmiy yo'l. Instagram «yangi qurilmadan kirildi» desa — ilovada «Bu men edim» ni bosing.\n"
    "Chiqish: /iglogout"
)


async def panel() -> tuple[str, list]:
    lines = ["📸 <b>Instagram</b>"]
    if not config.INSTAGRAM_ENABLED:
        lines.append("⚪️ O'chirilgan (.env: INSTAGRAM_ENABLED=false)")
    if problem := lib_problem():
        lines.append(f"❌ {_esc(problem)}")
    elif v := lib_version():
        lines.append(f"📦 instagrapi {_esc(v)}")
    if st.client is not None:
        lines.append(f"✅ Ulangan: <b>@{_esc(st.username)}</b>")
    else:
        lines.append("❌ Akkaunt ulanmagan — «🔑 Ulash» tugmasini bosing")
    if st.last_check:
        lines.append(f"🕒 Oxirgi tekshiruv: {_when(st.last_check)} (har ~{config.IG_POLL_SECONDS} soniyada)")
    if st.paused_until > time.time():
        lines.append(f"⏸ Instagram cheklovi — {_when(st.paused_until)} gacha kutiladi")
    if st.problem:
        lines.append(f"⚠️ {_esc(st.problem)}")
    try:
        lines.append(f"🔥 Saqlangan bir martalik media: {await storage.ig_saved_count()} ta")
    except Exception:
        pass
    try:
        mode = await storage.get_mode()
    except Exception:
        mode = "?"
    mode_name = {"auto": "🤖 aqlli (/auto)", "on": "🟢 doim (/on)", "off": "⚪️ o'chiq (/off)"}.get(mode, mode)
    lines += [f"🤖 Avtojavob Telegram rejimiga bo'ysunadi — hozir: {mode_name}", "",
              "🔥 — bir martalik rasm/video saqlanadi\n🤖 — Anna Direct'ga javob beradi\n"
              "🗂 — yozishmalar arxivi, qaytarib olingan xabar haqida xabar\n📥 — yangi xabarlar botga keladi"]
    btn = [(f"{'✅' if runtime.get(k) else '▫️'} {label}", f"ig:set:{k}") for k, label in TOGGLES.items()]
    rows = [btn[0:2], btn[2:4], [("💬 Chatlar arxivi", "ig:chats:0"), ("🔄 Hozir tekshirish", "ig:check")],
            [("🚪 Chiqish", "ig:logout") if st.client is not None else ("🔑 Ulash", "ig:help"),
             ("🌐 Ulanish testi", "ig:net")]]
    return "\n".join(lines), rows


async def toggle(key: str) -> str:
    if key not in TOGGLES:
        return "Noma'lum sozlama"
    value = not runtime.get(key)
    await runtime.update(**{key: value})
    wake()
    return f"{TOGGLES[key]}: {'yoqildi ✅' if value else 'o‘chirildi'}"


CHATS_PER_PAGE = 8


async def chats_view(page: int = 0) -> tuple[str, list]:
    rows = await storage.ig_chats(page, CHATS_PER_PAGE)
    more, rows = len(rows) > CHATS_PER_PAGE, rows[:CHATS_PER_PAGE]
    back = ("⬅️ Instagram", "ig:panel")
    if not rows and page == 0:
        return ("🗂 Instagram arxivi hozircha bo'sh. «🗂 Arxiv» yoqilgan bo'lsa, yozishmalar har tekshiruvda "
                "yig'ilib boradi."), [[back]]
    buttons = [[(f"{'👥 ' if r.get('group') else ''}{(r.get('title') or r['thread_id'])[:32]} · {r.get('count', 0)}",
                 f"ig:html:{r['thread_id']}")] for r in rows]
    nav = ([("◀️", f"ig:chats:{page - 1}")] if page > 0 else []) + [back] \
        + ([("▶️", f"ig:chats:{page + 1}")] if more else [])
    text = ("🗂 <b>Instagram chatlari</b>\nChatni tanlang — yozishma HTML fayl bo'lib keladi "
            "(qaytarib olingan xabarlar qizil bilan belgilanadi).")
    return text, buttons + [nav]


async def export_html(thread_id: str) -> tuple[bytes, str, int]:
    info = await storage.ig_chat_get(thread_id) or {}
    docs = await storage.ig_archive_export(thread_id, config.ARCHIVE_EXPORT_LIMIT)
    title = info.get("title") or thread_id
    meta = {"title": f"Instagram: {title}", "username": (info.get("usernames") or [""])[0],
            "chat_id": thread_id, "kind": "group" if info.get("group") else "private"}
    page = archive.build_html(meta, docs, {}, False)
    name = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")[:40] or "chat"
    return page.encode("utf-8"), f"instagram_{name}.html", len(docs)


def describe() -> str:
    """/health uchun bir qator."""
    if not config.INSTAGRAM_ENABLED:
        return "o'chirilgan (INSTAGRAM_ENABLED=false)"
    if problem := lib_problem():
        return problem
    if st.client is None:
        return "ulanmagan (/ig)" + (f" — {st.problem}" if st.problem else "")
    s = f"@{st.username} ulangan (instagrapi {lib_version()})"
    if st.last_check:
        s += f", oxirgi tekshiruv {_when(st.last_check)}"
    return s + (f", muammo: {st.problem}" if st.problem else "")
