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
  📤 Men yuborgan media — siz Direct'da kimgadir yuborgan rasm/video (oddiy ham, o'chib ketadigan ham) botga
     nusxa bo'lib keladi.
  👥 2 ta akkaunt — /iglogin2 (/igsession2, /iglogout2) bilan ikkinchi akkaunt ulanadi; ikkalasi bir vaqtda
     tekshiriladi, xabarlarda qaysi akkauntga tegishli ekani yoziladi, javob o'sha akkauntdan ketadi.

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
import base64
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
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import ai
import availability
import config
import runtime
import storage
import usage

log = logging.getLogger("instagram")
for _name in ("instagrapi", "private_request", "public_request"):
    logging.getLogger(_name).setLevel(logging.WARNING)   # har 30 soniyadagi so'rovlar logni to'ldirmasin

THREAD_LIMIT = 20            # Direct'dan nechta chat olinadi
MESSAGE_LIMIT = 20           # har chatdan oxirgi nechta xabar (muntazam tekshiruvda)
THREAD_PAGE = 50             # tarix olinganda bitta so'rovda nechta xabar
BACKFILL_PAGES = 60          # bitta chatdan ko'pi bilan shuncha sahifa (60 x 50 = 3000 xabar)
BACKFILL_PAUSE = 2.5         # so'rovlar orasida (soniya) — Instagram cheklov qo'ymasligi uchun
BACKFILL_CHATS = 20          # bir yo'la nechta chat
BACKFILL_START_DELAY = 60    # ishga tushgandan keyin shuncha kutib boshlanadi
BACKFILL_IDLE = 3600         # keyin har soatda yangi chat bor-yo'qligi tekshiriladi
PENDING_EVERY = 4            # «So'rovlar» (message requests) papkasi har nechta tekshiruvda bir marta
CODE_TIMEOUT = 5 * 60        # Instagram tasdiqlash kodini kutish (soniya)
THROTTLE_PAUSE = 15 * 60     # Instagram «biroz kuting» desa — shuncha tekshirilmaydi
SETTINGS_SAVE_EVERY = 3600   # sessiya (cookie) bazaga shuncha soniyada bir yangilanadi
LOGIN_ATTEMPTS = 4           # «ilova eski» xatosida sinaladigan Instagram ilova profillari soni
LOGIN_PAUSE = 3.0            # urinishlar orasidagi pauza (soniya)
MISSING_CONFIRM = 2          # xabar ketma-ket shuncha tekshiruvda ko'rinmasa — qaytarib olingan
FAIL_ALERT = 5               # shuncha ketma-ket xatodan keyin egasi ogohlantiriladi
MAX_MEDIA_BYTES = config.MAX_MEDIA_MB * 1024 * 1024
CODE_RE = re.compile(r"\d{4,8}")
USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
_UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"

TOGGLES = {"ig_save": "🔥 Bir martalik media", "ig_autoreply": "🤖 Avtojavob",
           "ig_archive": "🗂 Arxiv", "ig_forward": "📥 Xabarlar botga",
           "ig_sent": "📤 Men yuborgan media"}
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


def active_proxy() -> str:
    """Ishlatiladigan proksi: IG_PROXY, agar botdan (/ig → 🧦 Proksi) o'chirilmagan bo'lsa."""
    return PROXY if runtime.get("ig_proxy") else ""


_PLACEHOLDERS = {"login", "parol", "host", "port", "1.2.3.4"}   # yo'riqnomadagi misol so'zlari


def proxy_problem() -> str | None:
    """IG_PROXY misol matni yoki noto'g'ri formatda bo'lsa — tushuntirish (bunday proksi bilan ulanib bo'lmaydi)."""
    proxy = active_proxy()
    if not proxy:
        return None
    from urllib.parse import urlsplit
    u = urlsplit(proxy)
    if {(u.hostname or "").lower(), (u.username or "").lower(), (u.password or "").lower()} & _PLACEHOLDERS:
        return ("IG_PROXY da misol matni turibdi (login/parol/host/port). U yerga proksi provayderi bergan "
                "haqiqiy manzil yoziladi — Instagram login/paroli EMAS. Proksi sotib olmagan bo'lsangiz: "
                "/ig → «🧦 Proksi» tugmasi bilan o'chiring (yoki Render → Environment da IG_PROXY ni o'chiring).")
    try:
        port = u.port
    except ValueError:
        port = None
    if u.scheme not in ("http", "https", "socks5", "socks5h", "socks4") or not u.hostname or not port:
        return ("IG_PROXY noto'g'ri formatda — to'g'risi: http://login:parol@ip:port (port — raqam). "
                "Proksi bo'lmasa: /ig → «🧦 Proksi» tugmasi bilan o'chiring.")
    return None


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
    if proxy := active_proxy():
        hint = (f"IG_PROXY ishlatilyapti ({mask_proxy(proxy)}) va u ishlamayapti. Proksi sotib olmagan "
                "bo'lsangiz: /ig → «🧦 Proksi» tugmasi bilan o'chiring (Render'da IG_PROXY ni ham o'chiring).")
    elif PROXY:
        hint = "IG_PROXY botdan o'chirilgan — server Instagram'ga to'g'ridan-to'g'ri ulana olmadi."
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
    "Instagram «ilova versiyasi eski» dedi — bot bir nechta Instagram ilova versiyasini sinab ko'rdi, "
    "hammasini rad etdi. Bu instagrapi kutubxonasining ma'lum muammosi (GitHub #2971): Instagram aslida "
    "boshqa narsani so'ragan bo'ladi — odatda yangi joydan (server IP'sidan) kirishni tasdiqlash, email kod "
    "yoki parol noto'g'ri.\n"
    "Nima qilish kerak:\n"
    "1) Telefoningizda Instagram ilovasini oching. «Bu siz edingizmi?» chiqsa — «Ha, men edim» ni bosing, "
    "keyin /iglogin ni qaytadan yuboring.\n"
    "2) Parolni tekshirib, qaytadan yuboring (bo'sh joy yoki qo'shimcha belgi tushib qolmaganmi).\n"
    "3) Baribir bo'lmasa — /igsession usuli bilan ulang: u login bosqichini umuman chetlab o'tadi va "
    "ishlashi aniq (yo'riqnoma: /ig → 🔑 Ulash)."
)


def explain(e: BaseException) -> str:
    names = _names(e)
    if isinstance(e, LoginError):
        return str(e)
    low = str(e).lower()
    if needs_upgrade(e):
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
        return ("instagrapi kutubxonasi o'rnatilmagan — Instagram qismi shusiz ishlamaydi (bot qolgan hamma "
                "narsani normal bajaradi).\n"
                "Kompyuterda: pip install \"instagrapi>=3.2.19\"\n"
                "Render'da — Settings → Build Command (oxiridagi «|| true» SHART: shunda kutubxona "
                "o'rnatilmasa ham bot baribir ishga tushadi):\n"
                "pip install -r requirements.txt && (pip install \"instagrapi>=3.2.19\" || true)")
    if _vtuple(v) < MIN_LIB:
        return (f"instagrapi eski ({v}) — bu versiyada login ishlamaydi. "
                "Yangilash: pip install -U \"instagrapi>=3.2.19\"")
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
    share_url: str = ""       # ulashilgan post/reels/storis havolasi (bosib ko'rish uchun)
    author: str = ""          # ulashilgan postning muallifi (@username)
    itype: str = ""           # Instagram'dagi asl xabar turi (item_type) — muammo bo'lsa logga yoziladi


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
        vm = sub("visual_media")
        media = vm.get("media") if isinstance(vm.get("media"), dict) else {}
        return ("video" if media.get("media_type") == 2 else "rasm"), ""
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


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _from_media(media: dict) -> tuple[str, str, int, int, float]:
    """Instagram «media» obyektidan eng sifatli fayl: (url, tur, eni, bo'yi, davomiyligi)."""
    if not isinstance(media, dict):
        return "", "", 0, 0, 0.0
    audio = ((media.get("audio") or {}) if isinstance(media.get("audio"), dict) else {}).get("audio_src")
    if audio:
        return str(audio), "ovozli xabar", 0, 0, _num(
            (media.get("audio") or {}).get("duration") or media.get("duration")) / 1000 or 0.0
    video = _best(media.get("video_versions"))
    image = _best((media.get("image_versions2") or {}).get("candidates"))
    best = video or image
    if not best:
        return "", "", 0, 0, 0.0
    kind = "video" if video else "rasm"
    w = int(best.get("width") or media.get("original_width") or 0)
    h = int(best.get("height") or media.get("original_height") or 0)
    dur = _num(media.get("video_duration")) if video else 0.0
    return str(best["url"]), kind, w, h, dur


def _gif_url(it: dict) -> str:
    """GIF (animated_media): mp4 bo'lsa o'sha, bo'lmasa webp/gif."""
    images = (it.get("animated_media") or {}).get("images") if isinstance(it.get("animated_media"), dict) else {}
    for key in ("fixed_height", "original", "fixed_height_downsampled"):
        block = (images or {}).get(key)
        if isinstance(block, dict):
            for field in ("mp4", "url", "webp"):
                if block.get(field):
                    return str(block[field])
    return ""


_SHARE_KEYS = ("clip", "media_share", "story_share", "felix_share", "xma_clip", "xma_media_share",
               "xma_story_share", "generic_xma", "xma", "reel_share")
_LINK_BY_KIND = {"reels": "reel", "post": "p", "storis": "p"}


def _find(block, *names: str):
    """Ichma-ich joylashgan lug'atlardan berilgan kalitni topadi (Instagram tuzilishi har xil bo'ladi)."""
    stack, seen = [block], 0
    while stack and seen < 60:
        cur = stack.pop()
        seen += 1
        if isinstance(cur, dict):
            for name in names:
                if cur.get(name):
                    return cur[name]
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(v for v in cur[:5] if isinstance(v, (dict, list)))
    return None


def share_link(it: dict, kind: str) -> tuple[str, str]:
    """Ulashilgan post/reels/storis uchun (havola, muallif). Topilmasa ("", "")."""
    for key in _SHARE_KEYS:
        block = it.get(key)
        if not isinstance(block, (dict, list)) or not block:
            continue
        author = str(_find(block, "username") or "")
        target = _find(block, "target_url", "url")
        if isinstance(target, str) and "instagram.com/" in target:
            return target.split("?")[0], author
        code = _find(block, "code", "shortcode")
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{5,40}", code):
            return f"https://www.instagram.com/{_LINK_BY_KIND.get(kind, 'p')}/{code}/", author
        if author:   # havola yo'q, lekin muallif ma'lum (masalan storis)
            return "", author
    return "", ""


def _attach_media(item: Item, it: dict) -> None:
    """Xabardagi yuklab olinadigan faylni topadi (bir martalik, oddiy rasm/video, ovoz, GIF, ulashilgan post)."""
    vm = it.get("visual_media")
    if isinstance(vm, dict) and str(vm.get("view_mode") or "").lower() == "permanent":
        # Yangi formatdagi ODDIY (saqlanib qoladigan) rasm/video — fayl visual_media ichida keladi
        media = vm.get("media") if isinstance(vm.get("media"), dict) else {}
        url, kind, w, h, dur = _from_media(media)
        if not url and isinstance(media.get("carousel_media"), list) and media["carousel_media"]:
            url, kind, w, h, dur = _from_media(media["carousel_media"][0])
        item.kind = kind or item.kind
        if url:
            item.url, item.width, item.height, item.duration = url, w, h, dur
        return
    if isinstance(vm, dict):
        item.ephemeral = True
        item.view_mode = str(vm.get("view_mode") or "once").lower()
        media = vm.get("media") if isinstance(vm.get("media"), dict) else {}
        url, kind, w, h, dur = _from_media(media)
        item.kind = kind or ("video" if media.get("media_type") == 2 else "rasm")
        if url:   # ochilgan yoki muddati o'tgan bo'lsa Instagram manzil bermaydi
            item.url, item.width, item.height = url, w, h
            item.duration = dur or _num(vm.get("playback_duration_secs"))
        return
    if isinstance(it.get("voice_media"), dict):
        inner = (it["voice_media"].get("media") or {}) if isinstance(it["voice_media"].get("media"), dict) else {}
        url, _, _, _, dur = _from_media(inner)
        if url:
            item.url, item.kind, item.duration = url, "ovozli xabar", dur
        return
    if item.kind == "GIF":
        item.url = _gif_url(it)
        return
    for key in ("media", "clip", "media_share", "felix_share"):
        block = it.get(key)
        if isinstance(block, dict):
            inner = block.get(key) if isinstance(block.get(key), dict) else block
            url, kind, w, h, dur = _from_media(inner)
            if not url and isinstance(inner.get("carousel_media"), list) and inner["carousel_media"]:
                url, kind, w, h, dur = _from_media(inner["carousel_media"][0])   # bir nechta rasm birga
            if url:
                item.url, item.width, item.height, item.duration = url, w, h, dur
                if key == "media":
                    item.kind = kind or item.kind
                return


def parse_item(it: dict, viewer_id: str = "") -> Item | None:
    item_id = str(it.get("item_id") or "")
    if not item_id:
        return None
    sender = str(it.get("user_id") or "")
    out = bool(it.get("is_sent_by_viewer")) or (bool(viewer_id) and sender == str(viewer_id))
    kind, text = _describe(it)
    item = Item(item_id, sender, out, _ts(it.get("timestamp")), kind, text, itype=str(it.get("item_type") or ""))
    if kind in ("reels", "post", "storis", "storisga javob"):
        item.share_url, item.author = share_link(it, kind)
    _attach_media(item, it)
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
    out = f"[{it.kind}]"
    if it.author:
        out += f" @{it.author}"
    # Ulashilgan post/reels/storis — havolasi ham ko'rinadi: bosib ko'rish va yuklab olish uchun
    for part in (it.share_url, it.text):
        if part:
            out += f" {part}"
    return out


def who_html(full: str, username: str) -> str:
    s = f"<b>{_esc(full or username or 'Nomaʼlum')}</b>"
    if username:
        s += f' (<a href="https://instagram.com/{_esc(username)}">@{_esc(username)}</a>)'
    return s


def media_caption(th: Thread, it: Item, acc: "_State | None" = None) -> str:
    uname, full = th.person(it.sender_id)
    mode = "bir marta ko'riladigan" if it.view_mode == "once" else "o'chib ketadigan"
    lines = [f"📸 <b>Instagram</b> — 🔥 {mode} {it.kind}", f"👤 {who_html(full, uname)}"]
    if acc is not None and (line := acc_line(acc)):
        lines.append(line)
    if th.group:
        lines.append(f"👥 {_esc(th.title)}")
    lines.append(f"🕒 {_when(it.ts)}")
    lines.append("✅ Saqlab olindi. Instagram'da hali ochilmagan — yuboruvchiga hech narsa bildirilmadi.")
    return "\n".join(lines)


def doc_of(th: Thread, it: Item, ai_text: bool = False) -> dict:
    """Arxiv hujjati. Media havolasi ham yoziladi: fayl keyin yuklab olinadi va arxivda qoladi
    (suhbatdosh o'chirsa ham). Instagram havolalari tez eskiradi — shuning uchun tez yuklanadi."""
    uname, full = th.person(it.sender_id)
    doc = {"thread_id": th.thread_id, "msg_id": it.item_id, "date": it.ts, "out": it.out,
           "sender": "Siz" if it.out else (full or uname or it.sender_id), "sender_id": it.sender_id,
           "username": "" if it.out else uname, "kind": it.kind, "text": it.text,
           "media": None if it.kind in _TEXT_KINDS else it.kind, "view_once": it.ephemeral, "ai": ai_text}
    if it.url:
        doc.update(media_url=it.url, media_state="pending", media_w=it.width, media_h=it.height,
                   media_dur=round(it.duration, 1))
    if it.share_url:
        doc["share_url"] = it.share_url     # ulashilgan post/reels havolasi (arxivda bosiladi)
    if it.author:
        doc["author"] = it.author
    return doc


# ---------- holat (har bir ulangan Instagram akkaunt uchun alohida) ----------
SLOTS = ("1", "2")           # bir vaqtda 2 ta akkaunt: /iglogin va /iglogin2


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

    def __init__(self, slot: str = "1"):
        self.slot = slot

    def key(self, value: str) -> str:
        """1-akkaunt uchun o'zi (eski yozuvlar bilan mos), 2-akkaunt uchun «2:» old qo'shimchasi bilan."""
        return value if self.slot == "1" else f"{self.slot}:{value}"

    def name(self) -> str:
        return f"@{self.username}" if self.username else f"{self.slot}-akkaunt"


st = _State("1")             # 1-akkaunt (asosiy)
st2 = _State("2")            # 2-akkaunt
_thread_acc: dict[str, str] = {}                 # thread_id -> qaysi akkauntning chati (slot)


def accounts() -> list[_State]:
    return [st, st2]


def account(slot="1") -> _State:
    return st2 if str(slot) == "2" else st


def connected_accounts() -> list[_State]:
    return [a for a in accounts() if a.client is not None]


def multi() -> bool:
    """Ikkala akkaunt ulangan — xabarlarda qaysi akkauntga kelgani yoziladi."""
    return len(connected_accounts()) > 1


def acc_line(acc: _State) -> str:
    return f"📱 Akkaunt: <b>{_esc(acc.name())}</b>" if multi() else ""


async def _acc_for(thread_id: str, info: dict | None = None) -> _State | None:
    """Chat qaysi (ulangan) akkauntga tegishli. Topilmasa — None."""
    slot = _thread_acc.get(thread_id)
    if slot is None:
        if info is None:
            try:
                info = await storage.ig_chat_get(thread_id)
            except Exception:
                info = None
        slot = (info or {}).get("acc") or "1"
    acc = account(slot)
    return acc if acc.client is not None else None
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


def connected(slot=None) -> bool:
    if slot is None:
        return bool(connected_accounts())
    return account(slot).client is not None


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
    if proxy := active_proxy():
        cl.set_proxy(proxy)
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


def needs_upgrade(e: BaseException) -> bool:
    """Instagram «ilova versiyasi eski» dedi (instagrapi'ning ma'lum muammosi)."""
    low = str(e).lower()
    return "ClientNeedsUpgradeError" in _names(e) or "needs_upgrade" in low or "out of date" in low


def app_versions() -> list[str]:
    """Kutubxonadagi Instagram ilova profillari: standarti birinchi, keyin eskiroqlari."""
    try:
        from instagrapi import config as ig_config
        default = ig_config.DEFAULT_APP_VERSION
        rest = [v for v in ig_config.APP_SETTINGS if v != default]
        return [default, *rest][:LOGIN_ATTEMPTS]
    except Exception:
        return [""]


def _try_login(cl, username: str, password: str, ask) -> None:
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


def _login_sync(username: str, password: str, ask, old: dict | None, say=None):
    """Login va parol bilan kirish. Instagram «ilova eski» desa — boshqa ilova profillari ham sinaladi
    (faqat shu xatoda: noto'g'ri parol yoki tasdiqlash so'ralganda qayta urinish akkauntga zarar qiladi)."""
    versions = app_versions()
    last: Exception | None = None
    for i, version in enumerate(versions):
        cl = _new_client(_device_only(old))
        if i and version:
            try:
                cl.set_app(version)      # eskiroq Instagram ilovasi sifatida tanishtiramiz
                cl.set_user_agent()
            except Exception as e:
                log.info("Instagram ilova profili %s qo'llanmadi (%s)", version, type(e).__name__)
                continue
        try:
            _try_login(cl, username, password, ask)
            if i:
                log.info("Instagram'ga %s ilova profili bilan kirildi", version)
            return cl
        except Exception as e:
            last = e
            if not needs_upgrade(e) or i == len(versions) - 1:
                raise
            log.info("Instagram «ilova eski» dedi (profil %s) — boshqasi sinaladi", version)
            if say:
                say(f"⏳ Instagram «ilova eski» dedi. Boshqa variant sinalmoqda "
                    f"({i + 2}/{len(versions)})...")
            time.sleep(LOGIN_PAUSE)
    raise last if last else LoginError("login bo'lmadi")


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


async def _saved_settings(acc: _State | None = None) -> dict | None:
    try:
        doc = await storage.ig_get_session((acc or st).slot)
        return json.loads(doc["settings"]) if doc and doc.get("settings") else None
    except Exception:
        return None


async def _save_session(acc: _State | None = None) -> None:
    acc = acc or st
    cl = acc.client
    if cl is None:
        return
    try:
        settings = json.dumps(cl.get_settings(), default=str)
        await storage.ig_set_session(acc.slot, username=acc.username, user_id=acc.user_id, settings=settings,
                                     active=True)
        acc.saved_at = time.time()
    except Exception as e:
        log.warning("Instagram sessiyasi bazaga yozilmadi (%s)", type(e).__name__)


async def _connected(cl, typed: str = "", acc: _State | None = None) -> None:
    acc = acc or st
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
    acc.client, acc.username, acc.user_id = cl, name, str(getattr(cl, "user_id", "") or "")
    acc.problem, acc.fail_streak, acc.paused_until = "", 0, 0.0
    if acc.slot != "1":
        # Yangi qo'shilgan akkaunt: eski xabarlar (ulanishdan oldingilari) botga to'kilib kelmasin
        acc.started = time.time()
    await _save_session(acc)
    wake()


def _cmds_for_slot(text: str, slot: str) -> str:
    """2-akkaunt uchun matndagi /iglogin, /igsession, /iglogout -> /iglogin2, ... ga almashtiriladi."""
    if str(slot) == "1":
        return text
    for cmd in ("/iglogin", "/igsession", "/iglogout"):
        text = re.sub(re.escape(cmd) + r"(?![\w])", cmd + str(slot), text)
    return text


def _slot_hint(slot: str) -> str:
    return "" if slot == "1" else f" ({slot}-akkaunt)"


async def login(username: str, password: str, say, slot: str = "1") -> str:
    """say(text) — egasiga xabar (kod so'rash). Natija matnini qaytaradi."""
    acc = account(slot)
    if problem := lib_problem() or proxy_problem():
        return f"❌ {problem}"
    if _login_lock.locked():
        return "⏳ Login allaqachon ketyapti — Instagram yuborgan kodni shu yerga yozing."
    async with _login_lock:
        loop = asyncio.get_running_loop()
        ask = _asker(loop, say)
        # Oqim ichidan egasiga xabar (javob kutmasdan): «boshqa variant sinalmoqda...»
        tell = (lambda text: asyncio.run_coroutine_threadsafe(say(text), loop)) if say else None
        old = await _saved_settings(acc)
        try:
            cl = await asyncio.to_thread(_login_sync, username.strip().lstrip("@"), password, ask, old, tell)
        except Exception as e:
            log.warning("Instagram login bo'lmadi: %s: %s", type(e).__name__, _scrub(str(e))[:300])
            return f"❌ Instagram'ga kirilmadi{_slot_hint(acc.slot)}: {_cmds_for_slot(explain(e), acc.slot)}"
        await _connected(cl, username.strip().lstrip("@"), acc)
    return f"✅ Instagram ulandi{_slot_hint(acc.slot)}: @{acc.username}\nPanel: /ig"


def clean_sessionid(raw: str) -> str:
    sid = (raw or "").strip().strip("\"'")
    sid = re.sub(r"^sessionid\s*[=:]\s*", "", sid, flags=re.IGNORECASE)
    return sid.split(";")[0].strip().strip("\"'")


async def login_session(raw: str, slot: str = "1") -> str:
    acc = account(slot)
    if problem := lib_problem() or proxy_problem():
        return f"❌ {html.escape(problem, quote=False)}"
    sid = clean_sessionid(raw)
    if not re.match(r"^\d+(?:%3A|:)\S{10,}$", sid):
        return ("❌ Bu sessionid'ga o'xshamaydi. U raqamlar bilan boshlanadi, masalan: "
                "<code>1234567890%3AAbCdEf...</code>")
    async with _login_lock:
        old = await _saved_settings(acc)
        try:
            cl = await asyncio.to_thread(_session_sync, sid, old)
        except Exception as e:
            log.warning("Instagram sessionid bilan kirilmadi: %s: %s", type(e).__name__, _scrub(str(e))[:300])
            return f"❌ Sessiya bilan kirilmadi{_slot_hint(acc.slot)}: {_esc(_cmds_for_slot(explain(e), acc.slot))}"
        await _connected(cl, "", acc)
    return f"✅ Instagram ulandi{_slot_hint(acc.slot)}: @{_esc(acc.username)}\nPanel: /ig"


async def logout(slot: str = "1") -> str:
    acc = account(slot)
    name = acc.name()
    cl, acc.client = acc.client, None
    acc.username, acc.user_id, acc.problem = "", "", ""
    if cl is not None:
        try:
            async with _api_lock:
                await asyncio.to_thread(cl.logout)
        except Exception as e:
            log.info("Instagram'dan chiqish so'rovi o'tmadi (%s)", type(e).__name__)
    try:
        await storage.ig_delete_session(acc.slot)
    except Exception as e:
        log.warning("Instagram sessiyasi bazadan o'chirilmadi (%s)", type(e).__name__)
    if acc.slot != "1":
        return f"🚪 {acc.slot}-akkauntdan ({name}) chiqildi, sessiya o'chirildi."
    return "🚪 Instagram akkauntidan chiqildi, sessiya o'chirildi."


async def restore(acc: _State | None = None) -> bool:
    """Bazadagi sessiya bilan qayta ulanadi (login so'ralmaydi)."""
    acc = acc or st
    if lib_problem():
        return False
    try:
        doc = await storage.ig_get_session(acc.slot)
    except Exception as e:
        log.warning("Instagram sessiyasi o'qilmadi (%s)", type(e).__name__)
        return False
    if not doc or not doc.get("settings") or doc.get("active") is False:
        return False
    try:
        cl = await asyncio.to_thread(_new_client, json.loads(doc["settings"]))
    except Exception as e:
        acc.problem = f"sessiya tiklanmadi: {explain(e)}"
        log.warning("Instagram sessiyasi tiklanmadi (%s)", type(e).__name__)
        return False
    acc.client, acc.username = cl, doc.get("username") or ""
    acc.user_id = str(doc.get("user_id") or getattr(cl, "user_id", "") or "")
    acc.saved_at = time.time()
    log.info("Instagram sessiyasi tiklandi (%s-akkaunt): @%s", acc.slot, acc.username)
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


def _thread_page_sync(cl, thread_id: str, cursor: str = "") -> dict:
    """Chatning bitta sahifasi (eskiga qarab). Bu so'rov xabarlarni «o'qildi» qilmaydi va bir martalik
    mediani ochmaydi (visual_message_return_type=unseen)."""
    params = {"visual_message_return_type": "unseen", "direction": "older", "seq_id": "40065",
              "limit": str(THREAD_PAGE)}
    if cursor:
        params["cursor"] = cursor
    res = cl.private_request(f"direct_v2/threads/{thread_id}/", params=params)
    return ((res or {}).get("thread") or {}) if isinstance(res, dict) else {}


async def backfill_thread(thread_id: str, limit: int, title: str = "", acc: _State | None = None) -> int:
    """Chatning eski yozishmalarini arxivga oladi (sahifalab). Yangi qo'shilgan xabarlar soni."""
    acc = acc or await _acc_for(thread_id)
    cl = acc.client if acc else None
    if cl is None:
        raise LoginError("Instagram ulanmagan (/ig)")
    added, cursor, seen = 0, "", 0
    for _ in range(BACKFILL_PAGES):
        async with _api_lock:
            raw = await asyncio.to_thread(_thread_page_sync, cl, thread_id, cursor)
        th = parse_thread({**raw, "thread_id": thread_id, "thread_title": raw.get("thread_title") or title},
                          acc.user_id)
        if not th.items:
            break
        seen += len(th.items)
        docs = [doc_of(th, it) for it in th.items]
        added += await storage.ig_archive_add_many(docs)
        if any(d.get("media_url") for d in docs):
            media_wake()
        usernames = [u for u, _ in th.users.values() if u]
        await storage.ig_chat_touch(thread_id, th.title, usernames, th.group, th.items[-1].ts, len(docs))
        if runtime.get("ig_save"):      # orada ochilmagan bir martalik media bo'lsa — saqlab qolamiz
            try:
                await _save_ephemeral([th], acc)
            except Exception:
                log.exception("Tarixdagi bir martalik media saqlanmadi")
        cursor = str(raw.get("oldest_cursor") or "")
        if not cursor or seen >= limit:
            break
        await asyncio.sleep(BACKFILL_PAUSE)   # Instagram cheklov qo'ymasligi uchun
    await storage.ig_chat_set(thread_id, backfilled=time.time())
    return added


async def backfill_all(alert=None) -> tuple[int, int]:
    """Barcha chatlarning eski yozishmalarini arxivga oladi (bir martalik ish). (chatlar, xabarlar)."""
    chats = msgs = 0
    while True:
        rows = await storage.ig_backfill_pending(BACKFILL_CHATS)
        if not rows or not connected_accounts():
            break
        tried = 0
        for info in rows:
            if not connected_accounts():
                break
            acc = await _acc_for(info["thread_id"], info)
            if acc is None:          # bu chat hozir ulanmagan akkauntniki — keyinroq
                continue
            tried += 1
            try:
                msgs += await backfill_thread(info["thread_id"], config.IG_SYNC_LIMIT, info.get("title") or "")
                chats += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                kind = error_kind(e)
                log.warning("Instagram chat tarixi olinmadi (%s): %s", info.get("title"), explain(e)[:120])
                if kind in ("auth", "throttle"):
                    await _on_error(e, acc)
                    return chats, msgs
                await storage.ig_chat_set(info["thread_id"], backfilled=time.time())  # qayta urinmaymiz
            await asyncio.sleep(BACKFILL_PAUSE)
        if not tried or len(rows) < BACKFILL_CHATS:
            break
    if chats and alert:
        await alert(f"🗂 Instagram arxivi: {chats} ta chatdan {msgs} ta xabar olindi. "
                    "Endi ular qaytarib olinsa ham sizda qoladi — /ig → 💬 Chatlar arxivi")
    return chats, msgs


async def backfill_loop(alert) -> None:
    """Fon vazifasi: Instagram ulangach eski yozishmalarni bir marta arxivga nusxalaydi."""
    if config.IG_SYNC_LIMIT <= 0:
        log.info("Instagram tarix nusxasi o'chirilgan (IG_SYNC_LIMIT=0)")
        return
    while True:
        await asyncio.sleep(BACKFILL_START_DELAY)
        # Hali ulanmagan yoki arxiv o'chirilgan bo'lsa — uzoq kutmaymiz, keyingi daqiqada yana tekshiramiz
        # (shunda botdan ulangandan keyin tarix darhol olinadi)
        if not connected_accounts() or not runtime.get("ig_archive"):
            continue
        try:
            if await storage.ig_backfill_pending(1):
                log.info("Instagram: eski yozishmalar arxivga olinmoqda")
                await backfill_all(alert)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Instagram tarixi arxivlanmadi")
        # Keyin ham davom etadi: yangi chat paydo bo'lsa, uning tarixi ham olinadi
        await asyncio.sleep(BACKFILL_IDLE)


# ---------- 🔗 havola orqali yuklash: post, reels, storis, rasm, video ----------
# instagram.com/p/<kod>/ · /reel/<kod>/ · /reels/<kod>/ · /tv/<kod>/ · share/<kod> (yangi qisqa havola)
LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/(?:[^/\s?]+/)?(p|reel|reels|tv|share)/"
                     r"([A-Za-z0-9_-]{5,40})", re.IGNORECASE)
# instagram.com/stories/<username>/<id>/
STORY_RE = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/stories/([A-Za-z0-9._]+)/(\d{5,25})",
                      re.IGNORECASE)
MAX_LINKS = 5                # bitta xabarda ko'pi bilan shuncha havola
ALBUM_MAX = 10               # albomdan ko'pi bilan shuncha fayl


@dataclass
class Link:
    url: str
    kind: str                # "media" (post/reels/tv) yoki "story"
    code: str = ""           # post kodi
    pk: str = ""             # storis id si
    user: str = ""           # storis egasi


def parse_media_links(text: str) -> list[Link]:
    """Matndagi Instagram havolalari (takrorlanmaydi)."""
    out, seen = [], set()
    for m in STORY_RE.finditer(text or ""):
        key = ("story", m.group(2))
        if key not in seen:
            seen.add(key)
            out.append(Link(m.group(0), "story", pk=m.group(2), user=m.group(1)))
    for m in LINK_RE.finditer(text or ""):
        key = ("media", m.group(2))
        if key not in seen:
            seen.add(key)
            out.append(Link(m.group(0), "media", code=m.group(2)))
    return out[:MAX_LINKS]


def has_media_link(text: str) -> bool:
    return bool(parse_media_links(text))


def _info_sync(cl, pk: str) -> dict:
    """media/<pk>/info/ — xom JSON (instagrapi modellari Instagram o'zgarganda buziladi)."""
    res = cl.private_request(f"media/{pk}/info/")
    items = (res or {}).get("items") if isinstance(res, dict) else None
    return items[0] if items else {}


def _pk_sync(cl, link: Link) -> str:
    if link.pk:
        return link.pk
    return str(cl.media_pk_from_code(link.code))


def media_parts(item: dict) -> list[tuple[str, str, int, int, float]]:
    """Posttdagi fayllar: albom bo'lsa hammasi, aks holda bittasi."""
    carousel = item.get("carousel_media")
    blocks = carousel[:ALBUM_MAX] if isinstance(carousel, list) and carousel else [item]
    out = []
    for block in blocks:
        url, kind, w, h, dur = _from_media(block if isinstance(block, dict) else {})
        if url:
            out.append((url, kind, w, h, dur))
    return out


def link_caption(item: dict, link: Link, index: int = 0, total: int = 1) -> str:
    owner = ((item.get("user") or {}) if isinstance(item.get("user"), dict) else {}).get("username") or link.user
    caption = ((item.get("caption") or {}) if isinstance(item.get("caption"), dict) else {}).get("text") or ""
    head = "📖 <b>Instagram storis</b>" if link.kind == "story" else "📥 <b>Instagram'dan yuklandi</b>"
    lines = [head + (f" · {index + 1}/{total}" if total > 1 else "")]
    if owner:
        lines.append(f"👤 <a href=\"https://instagram.com/{_esc(owner)}\">@{_esc(owner)}</a>")
    if caption:
        lines.append(f"\n💬 <i>{_esc(caption[:600])}</i>")
    return "\n".join(lines)


async def download_link(link: Link, bar=None, prefix: str = "") -> str:
    """Havoladagi mediani yuklab, egasiga yuboradi. bar — jonli ko'rsatkich (control_bot.Progress)."""
    acc = next(iter(connected_accounts()), None)
    cl = acc.client if acc else None
    if cl is None:
        return "❌ Instagram ulanmagan — /ig"
    if _send_media is None:
        return "❌ Yuborish sozlanmagan"
    if bar:
        bar.set(f"{prefix}Instagram'dan ma'lumot olinmoqda...")
    try:
        async with _api_lock:
            pk = await asyncio.to_thread(_pk_sync, cl, link)
            item = await asyncio.to_thread(_info_sync, cl, pk)
    except Exception as e:
        log.warning("Instagram havolasi o'qilmadi (%s)", type(e).__name__)
        if error_kind(e) in ("auth", "throttle"):
            await _on_error(e, acc)
        return f"❌ {explain(e)}"[:350]
    parts = media_parts(item)
    if not parts:
        return ("❌ Bu havolada yuklab olinadigan media topilmadi (yopiq akkaunt bo'lishi mumkin — "
                "akkauntingiz unga obuna bo'lsa ishlaydi)")
    sent = 0
    limit = max(config.IG_MEDIA_MAX_KB * 1024, config.IG_MEDIA_DRIVE_MAX_MB * 1024 * 1024)
    icons = {"video": "🎬", "rasm": "🖼", "ovozli xabar": "🎤"}
    for i, (url, kind, w, h, dur) in enumerate(parts):
        step = f"{prefix}{i + 1}/{len(parts)} " if len(parts) > 1 else prefix
        icon = icons.get(kind, "📥")

        def shown(done: int, total: int, _step=step, _icon=icon, _kind=kind) -> None:
            # Hajm, tezlik va qolgan vaqtni ko'rsatkichning o'zi yozadi (progress bar ostida)
            if bar is not None:
                bar.set(f"{_step}{_icon} {_kind} yuklanmoqda", (done / total) if total else None,
                        done=done, total=total)

        shown(0, 0)
        try:
            data, mime = await _download(url, limit, shown)
        except Exception as e:
            log.warning("Instagram media yuklab olinmadi (%s)", type(e).__name__)
            continue
        if bar:
            bar.set(f"{step}📤 Telegram'ga yuborilmoqda", 1.0, done=len(data), total=len(data))
            await bar.render()
        ext = _ext_for(kind, mime, url)
        video = {"duration": dur, "w": w, "h": h} if kind == "video" else None
        ok = await _send_media(data, f"instagram_{pk}_{i + 1}{ext}",
                               link_caption(item, link, i, len(parts)), video,
                               kind not in ("video", "rasm") or ext not in (".jpg", ".png", ".mp4"))
        sent += bool(ok)
    if not sent:
        return "❌ Yuklab bo'lmadi — havola eskirgan yoki fayl juda katta"
    return f"✅ Yuborildi ({sent} ta fayl)" if sent > 1 else "✅ Yuborildi"


async def download_links(links: list[Link], bar=None) -> str:
    """Bir nechta havola: natijalar ro'yxati."""
    out = []
    for i, link in enumerate(links, 1):
        prefix = f"{i}/{len(links)} " if len(links) > 1 else ""
        out.append(prefix + await download_link(link, bar, prefix))
    return "\n".join(out)


# ---------- 🖼 media (rasm, video, ovozli xabar) arxivga yuklab olinadi ----------
MEDIA_BATCH = 6              # bitta aylanishda nechta fayl
MEDIA_LOOP_SECONDS = 20      # Instagram havolalari tez eskiradi — tez-tez tekshiriladi
MEDIA_ATTEMPTS = 3
_media_wake = asyncio.Event()
media_state = {"saved": 0, "failed": 0, "bytes": 0, "problem": ""}


def media_wake() -> None:
    _media_wake.set()


def _ext_for(kind: str, mime: str, url: str = "") -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    known = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
             "image/gif": ".gif", "video/mp4": ".mp4", "video/quicktime": ".mov", "audio/mp4": ".m4a",
             "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/aac": ".aac", "audio/webm": ".webm"}
    if mime in known:
        return known[mime]
    for ext in (".mp4", ".jpg", ".png", ".webp", ".gif", ".m4a", ".mp3", ".ogg"):
        if ext in url.lower():
            return ext
    return {"video": ".mp4", "ovozli xabar": ".m4a", "audio": ".mp3", "GIF": ".mp4"}.get(kind, ".jpg")


def _guess_mime(kind: str, ext: str) -> str:
    by_ext = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
              ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
              ".ogg": "audio/ogg", ".aac": "audio/aac", ".webm": "audio/webm", ".heic": "image/heic"}
    if ext in by_ext:
        return by_ext[ext]
    return {"video": "video/mp4", "ovozli xabar": "audio/mp4", "audio": "audio/mpeg"}.get(kind, "image/jpeg")


def media_file_name(doc: dict, ext: str) -> str:
    kind = (doc.get("kind") or "media").replace(" ", "-")
    when = _when(doc.get("date")).replace(".", "-").replace(":", "-").replace(" ", "_") or "0"
    who = "men" if doc.get("out") else re.sub(r"[^\w.-]+", "", (doc.get("username") or doc.get("sender") or ""))[:20]
    once = "_bir-martalik" if doc.get("view_once") else ""
    return f"{when}_{kind}{once}_{who or 'nomalum'}_{str(doc.get('msg_id'))[-8:]}{ext}"


async def _media_to_drive(doc: dict, data: bytes, name: str, mime: str) -> str:
    """Asl faylni Drive'ga: <asosiy papka>/Instagram/<chat>/ ichiga. Havolani qaytaradi (yoki '')."""
    import gdrive
    if not drive_on() or len(data) > config.IG_MEDIA_DRIVE_MAX_MB * 1024 * 1024:
        return ""
    info = await storage.ig_chat_get(doc["thread_id"]) or {"thread_id": doc["thread_id"]}
    folder_name = re.sub(r"[\\/:*?\"<>|]+", "-", info.get("title") or doc["thread_id"])[:80]
    try:
        parent = await gdrive.ensure_folder(folder_name, parent=await _drive_dir())
        res = await gdrive.upload(name, data, mime, None, parent=parent)
    except Exception as e:
        log.warning("Instagram media Drive'ga yuklanmadi (%s)", type(e).__name__)
        return ""
    return res.get("webViewLink") or f"https://drive.google.com/file/d/{res['id']}/view"


async def _fetch_media(doc: dict) -> bool:
    """Bitta xabarning faylini yuklab oladi va arxivga saqlaydi. Muvaffaqiyatli bo'lsa True."""
    tid, mid, url = doc["thread_id"], doc["msg_id"], doc.get("media_url") or ""
    if not url:
        await storage.ig_media_set(tid, mid, media_state="skipped", media_problem="havola yo'q")
        return False
    limit = max(config.IG_MEDIA_MAX_KB * 1024, config.IG_MEDIA_DRIVE_MAX_MB * 1024 * 1024)
    try:
        data, mime = await _download(url, limit)
    except Exception as e:
        tries = int(doc.get("media_tries") or 0) + 1
        gone = tries >= MEDIA_ATTEMPTS or "403" in str(e) or "410" in str(e)
        reason = _scrub(" ".join(str(e).split()))[:120] or type(e).__name__
        await storage.ig_media_set(tid, mid, media_state="gone" if gone else "retry",
                                   media_tries=tries, media_problem=reason)
        if gone:
            media_state["failed"] += 1
            log.info("Instagram media olinmadi (havola eskirgan): %s", reason)
        return False
    ext = _ext_for(doc.get("kind") or "", mime, url)
    mime = mime if "/" in mime else _guess_mime(doc.get("kind") or "", ext)
    name = media_file_name(doc, ext)
    link = await _media_to_drive(doc, data, name, mime)
    fields = {"media_state": "done", "media_size": len(data), "media_mime": mime, "media_name": name,
              "media_problem": "", "media_url": ""}       # havola endi kerak emas (u baribir eskiradi)
    if link:
        fields["media_drive"] = link
    if len(data) <= config.IG_MEDIA_MAX_KB * 1024:
        await storage.ig_media_save(tid, mid, data, mime)
        fields["media_saved"] = True
    elif not link:
        fields.update(media_state="too_big", media_problem=f"{len(data) / 1024 / 1024:.1f} MB — bazaga sig'maydi")
    await storage.ig_media_set(tid, mid, **fields)
    await storage.ig_chat_set(tid, changed=time.time(), drive_dirty=True)   # HTML yangilansin
    media_state["saved"] += 1
    media_state["bytes"] += len(data)
    return True


async def media_sync_pending() -> int:
    """Kutayotgan fayllarni yuklab oladi. Yuklanganlar soni."""
    rows = await storage.ig_media_pending(MEDIA_BATCH)
    done = 0
    for doc in rows:
        try:
            done += await _fetch_media(doc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Instagram media saqlanmadi")
    return done


async def media_loop() -> None:
    """Fon vazifasi: yozishmalardagi rasm, video va ovozli xabarlarni arxivga yuklab oladi."""
    await asyncio.sleep(45)
    while True:
        _media_wake.clear()
        if connected_accounts() and runtime.get("ig_media") and runtime.get("ig_archive"):
            try:
                if await media_sync_pending():
                    continue      # yana bor — darhol davom etamiz
            except asyncio.CancelledError:
                raise
            except Exception as e:
                media_state["problem"] = type(e).__name__
                log.exception("Instagram media yuklab olinmadi")
        try:
            await asyncio.wait_for(_media_wake.wait(), timeout=MEDIA_LOOP_SECONDS)
        except asyncio.TimeoutError:
            pass


# ---------- ☁️ Google Drive: Instagram arxivi HTML bo'lib yuklanadi ----------
DRIVE_FOLDER = "Instagram"
DRIVE_BATCH = 50
_drive_lock = asyncio.Lock()
_drive_wake = asyncio.Event()
_drive_folder_id = ""
drive_state = {"ok": None, "problem": "", "last": 0.0, "files": 0}


def drive_on() -> bool:
    import gdrive
    return gdrive.enabled() and runtime.get("ig_drive") and runtime.get("ig_archive")


async def _drive_dir(fresh: bool = False) -> str:
    """<asosiy papka>/Instagram/ — har chatga bitta HTML fayl."""
    import gdrive
    global _drive_folder_id
    if _drive_folder_id and not fresh:
        return _drive_folder_id
    _drive_folder_id = await gdrive.ensure_folder(DRIVE_FOLDER)
    return _drive_folder_id


def drive_file_name(info: dict) -> str:
    title = info.get("title") or str(info.get("thread_id") or "")
    users = [u for u in (info.get("usernames") or []) if u][:2]
    tag = f" (@{', @'.join(users)})" if users else ""
    name = re.sub(r"[\\/:*?\"<>|]+", "-", f"{title}{tag}").strip() or "chat"
    return f"{name[:90]}.html"


async def drive_sync_thread(thread_id: str, info: dict | None = None) -> str:
    """Bitta Instagram chatini Drive'ga yuklaydi (bor fayl yangilanadi). Havolani qaytaradi."""
    import gdrive
    info = info or await storage.ig_chat_get(thread_id) or {"thread_id": thread_id}
    changed = info.get("changed")
    async with _drive_lock:
        data, _, _ = await export_html(thread_id)
        name = drive_file_name(info)
        try:
            res = await gdrive.upload(name, data, "text/html", info.get("drive_file_id"),
                                      parent=await _drive_dir())
        except gdrive.DriveError as e:
            if e.status != 404:
                raise
            log.info("Instagram papkasi yoki fayli Drive'da topilmadi — qaytadan yaratiladi")
            res = await gdrive.upload(name, data, "text/html", None, parent=await _drive_dir(fresh=True))
    link = res.get("webViewLink") or f"https://drive.google.com/file/d/{res['id']}/view"
    await storage.ig_drive_done(thread_id, changed, res["id"], link)
    return link


async def drive_sync_pending() -> int:
    """O'zgargan barcha Instagram chatlarini Drive'ga yuklaydi. Yuklanganlar soni."""
    import gdrive
    done = 0
    while True:
        rows = await storage.ig_drive_pending(DRIVE_BATCH)
        if not rows:
            break
        progressed = 0
        for info in rows:
            try:
                await drive_sync_thread(info["thread_id"], info)
                done += 1
                progressed += 1
            except gdrive.DriveError as e:
                if e.status in (401, 403) or "invalid_grant" in e.reason or "invalid_client" in e.reason:
                    raise   # sozlamada xato — qolganlari ham yuklanmaydi
                log.warning("Instagram chati Drive'ga yuklanmadi: %s", gdrive.explain(e))
            except Exception as e:
                log.warning("Instagram chati Drive'ga yuklanmadi (%s)", type(e).__name__)
        if progressed == 0 or len(rows) < DRIVE_BATCH:
            break
    return done


def drive_wake() -> None:
    _drive_wake.set()


async def drive_now() -> str:
    """Botdagi «☁️ Drive'ga hozir yuklash»."""
    import gdrive
    if not gdrive.enabled():
        return "☁️ Google Drive sozlanmagan: .env da yo'q — " + ", ".join(gdrive.missing())
    if not runtime.get("ig_drive"):
        return "☁️ Instagram arxivi Drive'ga yuklash o'chirilgan (paneldagi «☁️ Drive» tugmasi)"
    await storage.ig_drive_mark_all()
    try:
        n = await drive_sync_pending()
    except Exception as e:
        return f"☁️❌ {gdrive.explain(e)}"[:190]
    drive_state.update(ok=True, problem="", last=time.time())
    return f"☁️ {n} ta chat Drive'ga yuklandi" if n else "☁️ Yuklanadigan chat yo'q"


async def drive_loop(alert) -> None:
    """Fon vazifasi: har DRIVE_SYNC_MINUTES da o'zgargan Instagram chatlari Drive'ga yuklanadi."""
    import gdrive
    await asyncio.sleep(90)
    alerted = ""
    while True:
        _drive_wake.clear()
        if drive_on():
            try:
                await storage.ig_drive_mark_all(only_missing=True)
                n = await drive_sync_pending()
                drive_state.update(ok=True, problem="", last=time.time())
                drive_state["files"] += n
                if n:
                    log.info("Instagram arxivi: %d ta chat Drive'ga yuklandi", n)
                if alerted:
                    alerted = ""
                    await alert("☁️ Instagram arxivi yana Drive'ga yuklanmoqda.")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                reason = gdrive.explain(e)
                drive_state.update(ok=False, problem=reason)
                log.warning("Instagram arxivi Drive'ga yuklanmadi: %s", reason)
                if reason != alerted:
                    alerted = reason
                    await alert(f"☁️❌ Instagram arxivi Drive'ga yuklanmadi: {reason}")
        try:
            await asyncio.wait_for(_drive_wake.wait(), timeout=config.DRIVE_SYNC_MINUTES * 60)
        except asyncio.TimeoutError:
            pass


async def _download(url: str, limit: int = 0, on_progress=None) -> tuple[bytes, str]:
    """on_progress(olingan_bayt, jami_bayt) — jonli ko'rsatkich uchun (jami noma'lum bo'lsa 0)."""
    limit = limit or MAX_MEDIA_BYTES
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": _UA}) as http:
        async with http.stream("GET", url) as r:
            r.raise_for_status()
            mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            try:
                total = int(r.headers.get("content-length") or 0)
            except ValueError:
                total = 0
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise ValueError(f"fayl juda katta (>{limit / 1024 / 1024:.0f} MB)")
                if on_progress:
                    on_progress(len(buf), total)
            return bytes(buf), mime


def _ext(kind: str, mime: str) -> str:
    if kind == "video":
        return ".mp4"
    return {"image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/avif": ".avif"}.get(mime, ".jpg")


async def _save_ephemeral(threads: list[Thread], acc: _State | None = None) -> int:
    acc = acc or st
    saved = 0
    for th in threads:
        for it in th.items:
            key = acc.key(it.item_id)
            if not it.ephemeral or it.out or await storage.ig_saved_has(key):
                continue
            uname, full = th.person(it.sender_id)
            who = full or uname or th.title
            if not it.url:
                # siz ochib qo'ygansiz (yoki muddati o'tgan) — Instagram endi bermaydi; faqat yangilari haqida aytamiz
                if it.ts >= acc.started:
                    await storage.ig_saved_add(key, {"status": "missed", "thread_id": th.thread_id,
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
            ok = await _send_media(data, f"instagram_{it.item_id[-12:]}{ext}", media_caption(th, it, acc), video,
                                   it.kind != "video" and ext not in (".jpg", ".png"))
            if ok:
                await storage.ig_saved_add(key, {"status": "saved", "thread_id": th.thread_id,
                                                 "sender": who, "kind": it.kind, "date": it.ts})
                saved += 1
                log.info("Instagram: %s yuborgan bir martalik %s saqlandi", who, it.kind)
    return saved


SENT_KINDS = ("rasm", "video")
SENT_WAIT = 10 * 60          # yuborgan mediangiz fayli shuncha vaqt kutiladi (Instagram'ga yuklanib bo'lishi uchun)
SENT_REFETCH_EVERY = 60      # fayl ko'rinmasa — chat shuncha soniyada bir to'liq qayta o'qiladi
_sent_wait: dict[str, list[float]] = {}          # kalit -> [birinchi ko'rilgan vaqt, oxirgi qayta o'qish]


def _find_item_sync(cl, thread_id: str, item_id: str) -> dict | None:
    """Chatni to'g'ridan-to'g'ri o'qib, bitta xabarni topadi (inbox ba'zan fayl manzilini bermaydi)."""
    raw = _thread_page_sync(cl, thread_id)
    return next((i for i in raw.get("items") or [] if isinstance(i, dict)
                 and str(i.get("item_id")) == item_id), None)


async def _sent_retry(acc: _State, th: Thread, it: Item, key: str) -> Item | None:
    """Fayl manzili yo'q: chatni qayta o'qib ko'radi. Topilsa — yangilangan xabar; vaqt tugagan bo'lsa —
    o'zi (manzilsiz); hali kutish kerak bo'lsa — None."""
    now = time.time()
    first, last = _sent_wait.setdefault(key, [now, 0.0])
    if now - last >= SENT_REFETCH_EVERY and acc.client is not None:
        _sent_wait[key][1] = now
        try:
            async with _api_lock:
                raw = await asyncio.to_thread(_find_item_sync, acc.client, th.thread_id, it.item_id)
            fresh = parse_item(raw, acc.user_id) if raw else None
            if fresh is not None and fresh.url:
                return fresh
        except Exception as e:
            log.info("Yuborgan media uchun chat qayta o'qilmadi (%s)", type(e).__name__)
    return it if now - first >= SENT_WAIT else None


def sent_caption(th: Thread, it: Item, acc: _State | None = None) -> str:
    """Siz Direct'da yuborgan rasm/video — kimga yuborilgani bilan."""
    others = [(u, f) for pk, (u, f) in th.users.items() if pk != it.sender_id]
    if it.ephemeral:
        mode = "bir marta ko'riladigan" if it.view_mode == "once" else "o'chib ketadigan"
        head = f"📤 <b>Instagram</b> — siz yuborgan 🔥 {mode} {it.kind}"
    else:
        head = f"📤 <b>Instagram</b> — siz yuborgan {it.kind}"
    lines = [head]
    if acc is not None and (line := acc_line(acc)):
        lines.append(line)
    if th.group:
        lines.append(f"👥 Guruh: {_esc(th.title)}")
    elif others:
        uname, full = others[0]
        lines.append(f"➡️ Kimga: {who_html(full, uname)}")
    else:
        lines.append(f"➡️ Kimga: {_esc(th.title)}")
    lines.append(f"🕒 {_when(it.ts)}")
    return "\n".join(lines)


async def _forward_sent(threads: list[Thread], acc: _State | None = None) -> int:
    """Siz (shu akkauntdan) kimgadir yuborgan rasm/video — oddiy ham, o'chib ketadigan ham — botga keladi.
    Faqat dastur ishga tushgandan (akkaunt ulangandan) keyin yuborilganlari; har biri bir marta."""
    acc = acc or st
    sent = 0
    for th in threads:
        for it in th.items:
            if not it.out or it.kind not in SENT_KINDS or it.ts < acc.started:
                continue
            key = f"out:{acc.key(it.item_id)}"
            if await storage.ig_saved_has(key):
                continue
            meta = {"status": "out", "thread_id": th.thread_id, "sender": acc.name(), "kind": it.kind,
                    "date": it.ts, "view_once": it.ephemeral}
            if not it.url:
                it = await _sent_retry(acc, th, it, key)
                if it is None:       # hali kutyapmiz — keyingi tekshiruvda yana qaraymiz
                    continue
            if not it.url:
                # 10 daqiqa ichida ham Instagram faylni bermadi (ko'pincha bir martalik media ko'rilgach)
                _sent_wait.pop(key, None)
                await storage.ig_saved_add(key, {**meta, "status": "out_missed"})
                log.warning("Siz yuborgan %s ning fayli topilmadi (item_type=%s, ephemeral=%s)",
                            it.kind, it.itype, it.ephemeral)
                await _say(f"📤 Instagram ({acc.name()}): siz «{th.title}» ga {it.kind} yubordingiz, lekin "
                           "Instagram faylni bermadi — nusxa olib bo'lmadi.")
                continue
            _sent_wait.pop(key, None)
            try:
                data, mime = await _download(it.url)
            except Exception as e:
                log.warning("Siz yuborgan media yuklab olinmadi (%s) — keyingi tekshiruvda yana urinaman",
                            type(e).__name__)
                continue
            ext = _ext(it.kind, mime)
            video = ({"duration": it.duration, "w": it.width, "h": it.height} if it.kind == "video" else None)
            ok = await _send_media(data, f"instagram_men_{it.item_id[-12:]}{ext}", sent_caption(th, it, acc),
                                   video, it.kind != "video" and ext not in (".jpg", ".png"))
            if ok:
                await storage.ig_saved_add(key, meta)
                sent += 1
                log.info("Instagram: siz yuborgan %s botga yuborildi (%s)", it.kind, acc.name())
    return sent


def unsent_text(th: Thread, docs: list[dict]) -> str:
    first = docs[0]
    who = who_html(first.get("sender") or "", first.get("username") or "")
    lines = [f"🗑 <b>Instagram: xabar qaytarib olindi</b>", f"👤 {who}"]
    if th.group:
        lines.append(f"👥 {_esc(th.title)}")
    lines.append("")
    for d in docs[:12]:
        body = d.get("text") or (f"[{d['media']}]" if d.get("media") else "xabar")
        lines.append(f"• <i>{_when(d.get('date'))}</i> — {_esc(body[:250])}")
    if len(docs) > 12:
        lines.append(f"… yana {len(docs) - 12} ta (hammasi «📄 Yozishma (HTML)» da)")
    return "\n".join(lines)


async def _archive_thread(th: Thread, acc: _State | None = None) -> None:
    acc = acc or st
    if not th.items:
        return
    ai_texts = set(_ai_sent[th.thread_id])
    docs = [doc_of(th, it, it.out and it.text in ai_texts) for it in th.items]
    added = await storage.ig_archive_add_many(docs)
    usernames = [u for u, _ in th.users.values() if u]
    await storage.ig_chat_touch(th.thread_id, th.title, usernames, th.group, th.items[-1].ts, added)
    if added and any(d.get("media_url") for d in docs):
        media_wake()      # Instagram havolalari tez eskiradi — fayllarni darhol yuklab olamiz
    # Qaytarib olingan xabarlar: shu oynadagi vaqt oralig'ida arxivda bor, lekin Instagram'da yo'q
    present = {it.item_id for it in th.items}
    gone = []
    for d in await storage.ig_archive_window(th.thread_id, th.items[0].ts):
        key = (acc.key(th.thread_id), d["msg_id"])
        if d["msg_id"] in present or d.get("view_once") or d.get("kind") in _SKIP_UNSENT:
            _missing.pop(key, None)
            continue
        _missing[key] = _missing.get(key, 0) + 1
        if _missing[key] >= MISSING_CONFIRM:   # bir martalik xato javob sabab noto'g'ri ogohlantirmaslik uchun
            _missing.pop(key, None)
            gone.append(d)
    if not gone:
        return
    now = time.time()
    await storage.ig_archive_mark_deleted(th.thread_id, [d["msg_id"] for d in gone], now)
    # Drive'dagi nusxa ham yangilansin — o'chirilgan xabar u yerda belgilangan holda qoladi
    await storage.ig_chat_set(th.thread_id, changed=now, drive_dirty=True)
    theirs = [d for d in gone if not d.get("out")]
    if theirs and _notify:
        text = unsent_text(th, theirs)
        if line := acc_line(acc):
            text = text.replace("\n", f"\n{line}\n", 1)
        await _notify(th.thread_id, text)


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


async def _send_text(thread_id: str, text: str, by_ai: bool = False, acc: _State | None = None) -> None:
    acc = acc or await _acc_for(thread_id)
    cl = acc.client if acc else None
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


def share_of(items: list[Item]) -> str:
    """Xabarlardagi oxirgi ulashilgan post/reels havolasi (botda «yuklab olish» tugmasi uchun)."""
    return next((i.share_url for i in reversed(items) if i.share_url), "")


def notify_text(th: Thread, items: list[Item], result: dict | None, acc: "_State | None" = None) -> str:
    uname, full = th.person(items[-1].sender_id)
    urgent = bool(result and (result.get("urgent") or result.get("call_owner")))
    head = "🔴 <b>SHOSHILINCH — Instagram</b>" if urgent else "📸 <b>Instagram: yangi xabar</b>"
    if th.pending:
        head += " <i>(so'rov — Requests papkasida)</i>"
    lines = [head, f"👤 {who_html(full, uname)}"]
    if acc is not None and (line := acc_line(acc)):
        lines.append(line)
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


async def _ai_reply(th: Thread, status, acc: _State | None = None) -> dict | None:
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
        await _send_text(th.thread_id, result["reply"], by_ai=True, acc=acc or st)
    except Exception as e:
        log.warning("Instagram avtojavob yuborilmadi (%s)", type(e).__name__)
        result["send_error"], result["reply"] = explain(e), None
    return result


async def _on_incoming(th: Thread, items: list[Item], acc: _State | None = None) -> bool:
    """Yangi kelgan xabarlar: avtojavob va/yoki botga bildirishnoma. AI javob bergan bo'lsa True."""
    auto = runtime.get("ig_autoreply")
    status = await availability.get_status() if auto else None
    result = None
    if (auto and status.reply and not th.group and not th.pending
            and _muted_until.get(th.thread_id, 0) <= time.time()):
        result = await _ai_reply(th, status, acc)
    want = (result is not None or runtime.get("ig_forward")
            or (auto and status.notify and not th.group))
    if want and _notify:
        await _notify(th.thread_id, notify_text(th, items, result, acc), share_of(items))
    return bool(result and result.get("reply"))


async def _process_fresh(threads: list[Thread], acc: _State | None = None) -> int:
    acc = acc or st
    replied = 0
    now = time.time()
    for th in threads:
        kkey = acc.key(th.thread_id)
        known = _known.get(kkey, set())
        fresh = [it for it in th.items if it.item_id not in known and it.ts >= acc.started]
        _known[kkey] = {it.item_id for it in th.items}
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
            replied += await _on_incoming(th, incoming, acc)
        except Exception:
            log.exception("Instagram xabari qayta ishlanmadi")
    return replied


def _any_on() -> bool:
    return any(runtime.get(k) for k in TOGGLES)


async def check_once(acc: _State | None = None) -> dict:
    """Bitta akkauntni bir marta tekshiradi. Natija: {"threads", "saved", "replied", "sent"}.
    Xatoni yuqoriga uzatadi."""
    acc = acc or st
    async with _check_lock:
        cl = acc.client
        if cl is None:
            return {"threads": 0, "saved": 0, "replied": 0, "sent": 0}
        acc.polls += 1
        async with _api_lock:
            raw = await asyncio.to_thread(_fetch_sync, cl, acc.polls % PENDING_EVERY == 1)
        threads = [parse_thread(t, acc.user_id) for t in raw]
        acc.last_check, acc.problem, acc.fail_streak = time.time(), "", 0
        res = {"threads": len(threads), "saved": 0, "replied": 0, "sent": 0}
        if runtime.get("ig_save"):
            res["saved"] = await _save_ephemeral(threads, acc)
        if runtime.get("ig_sent"):
            res["sent"] = await _forward_sent(threads, acc)
        if runtime.get("ig_archive"):
            for th in threads:
                try:
                    await _archive_thread(th, acc)
                except Exception:
                    log.exception("Instagram chati arxivlanmadi")
        for th in threads:   # chat qaysi akkauntniki — javob o'sha akkauntdan ketadi
            if _thread_acc.get(th.thread_id) != acc.slot:
                _thread_acc[th.thread_id] = acc.slot
                if runtime.get("ig_archive"):
                    try:
                        await storage.ig_chat_set(th.thread_id, acc=acc.slot)
                    except Exception:
                        pass
        res["replied"] = await _process_fresh(threads, acc)
        if time.time() - acc.saved_at > SETTINGS_SAVE_EVERY:
            await _save_session(acc)
        return res


async def _on_error(e: Exception, acc: _State | None = None) -> None:
    acc = acc or st
    kind = error_kind(e)
    who = f" ({acc.slot}-akkaunt {acc.name()})" if acc.slot != "1" or multi() else ""
    suffix = "" if acc.slot == "1" else acc.slot
    if kind == "auth":
        log.warning("Instagram sessiyasi yaroqsiz (%s-akkaunt, %s)", acc.slot, type(e).__name__)
        acc.client = None
        acc.problem = "sessiya tugadi yoki Instagram tasdiqlash so'rayapti — qayta ulaning"
        try:
            await storage.ig_set_session(acc.slot, active=False)
        except Exception:
            pass
        await _say(f"⚠️ Instagram{who} sessiyasi tugadi yoki Instagram tasdiqlash so'rayapti.\n"
                   f"Bir martalik media hozircha SAQLANMAYDI! Qayta ulaning: /iglogin{suffix} username parol "
                   f"(yoki /igsession{suffix}). Batafsil: /ig")
    elif kind == "throttle":
        acc.paused_until = time.time() + THROTTLE_PAUSE
        acc.problem = "Instagram «biroz kuting» dedi — tekshiruv vaqtincha to'xtatildi"
        log.warning("Instagram cheklovi (%s) — %d daqiqa kutiladi", type(e).__name__, THROTTLE_PAUSE // 60)
        await _say(f"⏸ Instagram{who} cheklov qo'ydi ({type(e).__name__}) — {THROTTLE_PAUSE // 60} daqiqa "
                   "tekshirilmaydi. Tez-tez bo'lsa .env dagi IG_POLL_SECONDS ni oshiring.")
    else:
        acc.fail_streak += 1
        acc.problem = explain(e)
        log.warning("Instagram tekshirilmadi (%s-akkaunt): %s", acc.slot, acc.problem)
        if acc.fail_streak == FAIL_ALERT:
            await _say(f"⚠️ Instagram{who} {FAIL_ALERT} marta ketma-ket tekshirilmadi: {acc.problem}")


async def check_now() -> str:
    """Botdagi «🔄 Hozir tekshirish» tugmasi — ulangan barcha akkauntlar."""
    accs = connected_accounts()
    if not accs:
        return "Instagram ulanmagan"
    out = []
    for acc in accs:
        prefix = f"{acc.name()}: " if len(accs) > 1 else ""
        try:
            res = await check_once(acc)
        except Exception as e:
            await _on_error(e, acc)
            out.append(f"{prefix}❌ {explain(e)}"[:190])
            continue
        line = (f"{prefix}✅ {res['threads']} ta chat tekshirildi, saqlandi: {res['saved']}, "
                f"javob: {res['replied']}")
        if res.get("sent"):
            line += f", siz yuborgan media: {res['sent']}"
        out.append(line)
    return "\n".join(out)


IG_PROBE_URL = "https://i.instagram.com/"
IP_PROBE_URL = "https://api.ipify.org"


def _probe(url: str, proxy: str | None) -> tuple[bool, str, float]:
    """(yetib bordimi, qiymat yoki xato, soniya). Har qanday HTTP javob = server yetib bordi.
    instagrapi kabi requests ishlatiladi (socks5 ham PySocks orqali ishlaydi)."""
    try:
        import requests  # instagrapi bilan birga o'rnatiladi
    except ImportError:
        return False, "requests o'rnatilmagan (instagrapi bilan birga keladi)", 0.0
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
    mark =lambda res: "✅" if res[0] else "❌"  # noqa: E731
    ip = await asyncio.to_thread(_probe, IP_PROBE_URL, None)
    ig = await asyncio.to_thread(_probe, IG_PROBE_URL, None)
    lines = ["🌐 <b>Instagram ulanish testi</b>", "",
             f"{mark(ip)} Server IP: {_shown(ip)}",
             f"{mark(ig)} Server → Instagram (to'g'ridan-to'g'ri): {_shown(ig)}"]
    proxy = active_proxy()
    if not proxy:
        lines.append("⏸ IG_PROXY: botdan o'chirilgan (/ig → 🧦 Proksi)" if PROXY else "▫️ IG_PROXY: bo'sh")
        verdict = ("Ulanish joyida. Login baribir o'tmasa — Instagram server IP'sidan kirishni yoqtirmayapti: "
                   "/igsession usulini sinang yoki residential proksi qo'ying." if ig[0] else
                   "Server Instagram'ga ulana olmayapti — residential proksi kerak (IG_PROXY).")
        return "\n".join(lines + ["", f"➡️ {verdict}"])
    if problem := proxy_problem():
        lines += ["", f"❌ {_esc(problem)}"]
        return "\n".join(lines)
    pip = await asyncio.to_thread(_probe, IP_PROBE_URL, proxy)
    pig = await asyncio.to_thread(_probe, IG_PROBE_URL, proxy)
    lines += ["", f"🧦 IG_PROXY: <code>{_esc(mask_proxy(proxy))}</code>"]
    if proxy != config.IG_PROXY.strip().strip("\"'"):
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
    else:
        restored = [await restore(acc) for acc in accounts()]
        if not any(restored):
            log.info("Instagram ulanmagan — botda /ig orqali ulang")
    while True:
        _wake.clear()
        if _any_on():
            for acc in accounts():   # ikkala akkaunt ham har aylanishda tekshiriladi
                if acc.client is None or time.time() < acc.paused_until:
                    continue
                try:
                    await check_once(acc)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    await _on_error(e, acc)
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
    "Chiqish: /iglogout\n\n"
    "➕ <b>2-akkaunt</b> (ikkalasi bir vaqtda ishlaydi): xuddi shunday, faqat buyruq oxirida «2» — "
    "<code>/iglogin2 username parol</code>, <code>/igsession2 QIYMAT</code>, chiqish: /iglogout2"
)


def login_help(slot: str = "1") -> str:
    """Ulash yo'riqnomasi: 2-akkaunt uchun buyruqlar «2» bilan ko'rsatiladi."""
    if str(slot) != "2":
        return LOGIN_HELP
    text = _cmds_for_slot(LOGIN_HELP.split("\n\n➕")[0], slot)
    return text.replace("🔑 <b>Instagram'ni ulash</b>", "🔑 <b>2-Instagram akkauntni ulash</b>", 1)


async def panel() -> tuple[str, list]:
    lines = ["📸 <b>Instagram</b>"]
    if not config.INSTAGRAM_ENABLED:
        lines.append("⚪️ O'chirilgan (.env: INSTAGRAM_ENABLED=false)")
    if problem := lib_problem():
        lines.append(f"❌ {_esc(problem)}")
    elif v := lib_version():
        lines.append(f"📦 instagrapi {_esc(v)}")
    if PROXY:
        state = "yoqiq" if active_proxy() else "o'chiq — to'g'ridan-to'g'ri ulanadi"
        lines.append(f"🧦 Proksi: <code>{_esc(mask_proxy(PROXY))}</code> ({state})")
    if problem := proxy_problem():
        lines.append(f"⚠️ {_esc(problem)}")
    for acc in accounts():
        tag = "" if acc.slot == "1" else f"{acc.slot}-akkaunt: "
        if acc.client is not None:
            lines.append(f"✅ {tag}Ulangan: <b>@{_esc(acc.username)}</b>")
        elif acc.slot == "1":
            lines.append("❌ Akkaunt ulanmagan — «🔑 Ulash» tugmasini bosing")
        else:
            lines.append(f"➕ {tag}ulanmagan — «🔑 2-akkauntni ulash» (/iglogin2)")
        if acc.last_check:
            lines.append(f"🕒 {tag}Oxirgi tekshiruv: {_when(acc.last_check)} "
                         f"(har ~{config.IG_POLL_SECONDS} soniyada)")
        if acc.paused_until > time.time():
            lines.append(f"⏸ {tag}Instagram cheklovi — {_when(acc.paused_until)} gacha kutiladi")
        if acc.problem:
            lines.append(f"⚠️ {tag}{_esc(acc.problem)}")
    try:
        lines.append(f"🔥 Saqlangan bir martalik media: {await storage.ig_saved_count()} ta")
    except Exception:
        pass
    try:
        s = await storage.ig_stats()
        lines.append(f"🗂 Arxiv: {s['chats']} ta chat, {s['messages']} ta xabar"
                     + (f", 🗑 {s['unsent']} ta qaytarib olingan" if s["unsent"] else ""))
        if s["left"]:
            lines.append(f"⏳ Eski yozishmalar olinmoqda — yana {s['left']} ta chat")
    except Exception:
        pass
    try:
        counts = await storage.ig_media_counts()
        total_mb = (await storage.ig_media_bytes()) / 1024 / 1024
        done, left = counts.get("done", 0), counts.get("pending", 0) + counts.get("retry", 0)
        gone = counts.get("gone", 0) + counts.get("too_big", 0)
        if done or left or gone:
            line = f"🖼 Fayllar: {done} ta saqlandi ({total_mb:.1f} MB)"
            if left:
                line += f", {left} ta yuklanmoqda"
            if gone:
                line += f", {gone} tasi olinmadi (havolasi eskirgan)"
            lines.append(line)
    except Exception:
        pass
    lines.append(_drive_line())
    try:
        mode = await storage.get_mode()
    except Exception:
        mode = "?"
    mode_name = {"auto": "🤖 aqlli (/auto)", "on": "🟢 doim (/on)", "off": "⚪️ o'chiq (/off)"}.get(mode, mode)
    lines += [f"🤖 Avtojavob Telegram rejimiga bo'ysunadi — hozir: {mode_name}", "",
              "🔥 — bir martalik rasm/video saqlanadi\n🤖 — Anna Direct'ga javob beradi\n"
              "🗂 — yozishmalar arxivi, qaytarib olingan xabar haqida xabar\n📥 — yangi xabarlar botga keladi\n"
              "📤 — siz Direct'da yuborgan rasm/video (oddiy va o'chib ketadigan) botga keladi\n"
              "🖼 — rasm, video, ovozli xabarlar arxivga yuklab olinadi (o'chirilsa ham sizda qoladi)"]
    btn = [(f"{'✅' if runtime.get(k) else '▫️'} {label}", f"ig:set:{k}") for k, label in TOGGLES.items()]
    rows = [btn[0:2], btn[2:4], btn[4:], [("💬 Chatlar arxivi", "ig:chats:0"), ("🔄 Hozir tekshirish", "ig:check")]]
    if st2.client is not None:
        rows.append([(f"🚪 2-akkauntdan chiqish (@{st2.username})"[:60], "ig:logout:2")])
    else:
        rows.append([("🔑 2-akkauntni ulash", "ig:help:2")])
    rows.append([(f"{'✅' if runtime.get('ig_media') else '▫️'} 🖼 Rasm/video/ovozni saqlash",
                  "ig:set:ig_media")])
    import gdrive
    if gdrive.enabled():
        rows.append([(f"{'✅' if runtime.get('ig_drive') else '▫️'} ☁️ Drive'ga saqlash", "ig:set:ig_drive"),
                     ("☁️ Hozir yuklash", "ig:drive")])
    if PROXY:
        rows.append([(f"{'✅' if active_proxy() else '▫️'} 🧦 Proksi (bosib o'chirish/yoqish)", "ig:set:ig_proxy")])
    rows += [[("🚪 Chiqish", "ig:logout") if st.client is not None else ("🔑 Ulash", "ig:help"),
             ("🌐 Ulanish testi", "ig:net")]]
    return "\n".join(lines), rows


def _drive_line() -> str:
    import gdrive
    if not gdrive.enabled():
        return "☁️ Google Drive sozlanmagan (.env: " + ", ".join(gdrive.missing()) + ")"
    if not runtime.get("ig_drive"):
        return "☁️ Drive'ga saqlash o'chirilgan"
    if drive_state["ok"] is False:
        return f"☁️❌ Drive: {_esc(drive_state['problem'])[:120]}"
    when = f", oxirgi: {_when(drive_state['last'])}" if drive_state["last"] else ""
    return f"☁️ Drive papkasi «{DRIVE_FOLDER}» — {drive_state['files']} ta fayl yuklandi{when}"


async def toggle(key: str) -> str:
    if key == "ig_proxy":
        return await _toggle_proxy()
    if key == "ig_media":
        value = not runtime.get("ig_media")
        await runtime.update(ig_media=value)
        if value:
            media_wake()
        return ("🖼 Rasm/video/ovoz arxivga yuklab olinadi — HTML arxivda ochiladi" if value
                else "🖼 Fayllarni yuklab olish o'chirildi (faqat matn arxivlanadi)")
    if key == "ig_drive":
        value = not runtime.get("ig_drive")
        await runtime.update(ig_drive=value)
        if value:
            await storage.ig_drive_mark_all()
            drive_wake()
        return ("☁️ Drive'ga saqlash yoqildi — chatlar «Instagram» papkasiga yuklanadi" if value
                else "☁️ Drive'ga saqlash o'chirildi")
    if key not in TOGGLES:
        return "Noma'lum sozlama"
    value = not runtime.get(key)
    await runtime.update(**{key: value})
    wake()
    return f"{TOGGLES[key]}: {'yoqildi ✅' if value else 'o‘chirildi'}"


async def _toggle_proxy() -> str:
    if not PROXY:
        return "IG_PROXY bo'sh — o'chiradigan proksi yo'q"
    value = not runtime.get("ig_proxy")
    await runtime.update(ig_proxy=value)
    for acc in connected_accounts():   # ulangan klient yangi sozlama bilan qayta yasaladi (sessiya saqlanadi)
        try:
            async with _api_lock:
                acc.client = await asyncio.to_thread(_new_client, acc.client.get_settings())
        except Exception as e:
            log.warning("Proksi almashtirilgach klient qayta yasalmadi (%s)", type(e).__name__)
    wake()
    return ("🧦 Proksi yoqildi" if value
            else "🧦 Proksi o'chirildi — Instagram'ga to'g'ridan-to'g'ri ulanadi")


CHATS_PER_PAGE = 8


async def chats_view(page: int = 0) -> tuple[str, list]:
    rows = await storage.ig_chats(page, CHATS_PER_PAGE)
    more, rows = len(rows) > CHATS_PER_PAGE, rows[:CHATS_PER_PAGE]
    back = ("⬅️ Instagram", "ig:panel")
    if not rows and page == 0:
        return ("🗂 Instagram arxivi hozircha bo'sh. «🗂 Arxiv» yoqilgan bo'lsa, yozishmalar har tekshiruvda "
                "yig'ilib boradi."), [[back]]
    buttons = [[(f"{'👥 ' if r.get('group') else ''}{(r.get('title') or r['thread_id'])[:30]} · {r.get('count', 0)}",
                 f"ig:view:{r['thread_id']}:0")] for r in rows]
    nav = ([("◀️", f"ig:chats:{page - 1}")] if page > 0 else []) + [back] \
        + ([("▶️", f"ig:chats:{page + 1}")] if more else [])
    stats = await storage.ig_stats()
    text = (f"🗂 <b>Instagram chatlari</b> — {stats['chats']} ta chat, {stats['messages']} ta xabar"
            + (f", 🗑 {stats['unsent']} ta qaytarib olingan" if stats["unsent"] else "") + "\n"
            "Chatni bosing — yozishmani shu yerda o'qiysiz (HTML fayl ham olasiz).")
    if stats["left"]:
        text += f"\n⏳ Yana {stats['left']} ta chatning eski yozishmalari olinmoqda..."
    return text, buttons + [nav]


MSGS_PER_PAGE = 15


async def chat_view(thread_id: str, page: int = 0) -> tuple[str, list]:
    """Yozishmani botda o'qish: eng yangi xabarlardan bir sahifa."""
    info = await storage.ig_chat_get(thread_id) or {"thread_id": thread_id}
    rows = await storage.ig_archive_page(thread_id, page * MSGS_PER_PAGE, MSGS_PER_PAGE)
    more, rows = len(rows) > MSGS_PER_PAGE, rows[:MSGS_PER_PAGE]
    total = info.get("count") or await storage.ig_chat_count(thread_id)
    users = [u for u in (info.get("usernames") or []) if u][:2]
    head = [f"💬 <b>{_esc(info.get('title') or thread_id)}</b>"
            + (f" (@{_esc(users[0])})" if users else ""),
            f"{total} ta xabar · {page * MSGS_PER_PAGE + 1}–{page * MSGS_PER_PAGE + len(rows)} "
            "(yangidan eskiga)"]
    if info.get("drive_link"):
        head.append(f"☁️ <a href=\"{_esc(info['drive_link'])}\">Google Drive'dagi nusxasi</a>")
    lines = []
    for d in rows:
        who = "🫵 <b>Siz</b>" if d.get("out") else f"<b>{_esc(d.get('sender') or '')}</b>"
        body = d.get("text") or ""
        if d.get("media"):
            icon = "🔥" if d.get("view_once") else "📎"
            body = f"{icon} [{_esc(d['media'])}]" + (f" {_esc(body[:300])}" if body else "")
        else:
            body = _esc(body[:600]) or "<i>(bo'sh)</i>"
        tag = " 🗑 <i>qaytarib olingan</i>" if d.get("deleted") else ""
        if d.get("ai"):
            tag += " 🤖"
        lines.append(f"<i>{_when(d.get('date'))}</i> · {who}: {body}{tag}")
    body = "\n\n".join(lines) if lines else "<i>Bu chatda hali arxivlangan xabar yo'q.</i>"
    nav = ([("◀️ Yangiroq", f"ig:view:{thread_id}:{page - 1}")] if page > 0 else []) \
        + ([("▶️ Eskiroq", f"ig:view:{thread_id}:{page + 1}")] if more else [])
    buttons = [nav] if nav else []
    buttons.append([("📄 HTML fayl", f"ig:html:{thread_id}"), ("💬 Chatlar", "ig:chats:0")])
    return "\n".join(head) + "\n\n" + body, buttons


_CSS = """
*{box-sizing:border-box}body{margin:0;background:#0d0d14;color:#f2f2f7;
font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
header{position:sticky;top:0;z-index:5;padding:14px 18px;
background:linear-gradient(90deg,#833ab4 0%,#c13584 45%,#e1306c 70%,#fd1d1d 100%);
box-shadow:0 2px 14px rgba(0,0,0,.45)}
h1{margin:0;font-size:18px;display:flex;align-items:center;gap:8px}
.sub{font-size:13px;margin-top:4px;opacity:.92}.sub a{color:#fff}
.legend{font-size:12px;margin-top:6px;opacity:.9}.legend span{margin-right:12px}
main{max-width:860px;margin:0 auto;padding:14px 12px 40px}
.day{text-align:center;margin:18px 0 10px}
.day span{background:#1c1c28;color:#b9b9c6;border-radius:14px;padding:4px 14px;font-size:12px}
.row{display:flex;margin:6px 0}.row.out{justify-content:flex-end}
.msg{max-width:82%;border-radius:18px;padding:9px 13px;word-wrap:break-word;white-space:pre-wrap;
background:#23232e;position:relative}
.out .msg{background:linear-gradient(135deg,#8a3ab9,#e1306c);color:#fff}
.msg.del{outline:2px solid #ff4d4f;background:#3a1b22}.out .msg.del{background:#5a2330}
.who{font-size:12px;font-weight:600;color:#ff8fc5;margin-bottom:3px}
.meta{font-size:11px;opacity:.72;text-align:right;margin-top:5px;white-space:normal}
.tag{display:inline-block;border-radius:7px;padding:0 6px;font-size:11px;margin-left:6px}
.tdel{background:#ff4d4f;color:#fff}.tonce{background:#ff9f0a;color:#1a1a1a}.tai{background:#3a3a4a}
img,video{max-width:100%;max-height:70vh;border-radius:12px;display:block;margin:4px 0;background:#000}
audio{width:260px;max-width:100%;margin:4px 0}
.media{font-style:italic;opacity:.85;font-size:13px}
.media a{color:#ff8fc5}.file{font-size:13px}
footer{text-align:center;font-size:12px;opacity:.6;padding:24px}
.empty{text-align:center;opacity:.7;padding:40px}
"""

_ICONS = {"rasm": "🖼", "video": "🎬", "ovozli xabar": "🎤", "GIF": "🎞", "audio": "🎵",
          "post": "📰", "reels": "🎬", "storis": "📖", "storisga javob": "📖", "havola": "🔗",
          "like": "❤️", "kontakt": "👤", "lokatsiya": "📍"}


def _fmt_dur(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}" if sec else ""


def _media_block(d: dict, blob: tuple[bytes, str] | None) -> str:
    """Xabardagi media: fayl bo'lsa HTML ichida ochiladi, bo'lmasa sabab yoki Drive havolasi."""
    kind = d.get("media") or ""
    icon = _ICONS.get(kind, "📎")
    out = []
    if blob:
        data, mime = blob
        src = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        if mime.startswith("image/"):
            out.append(f"<img src='{src}' alt='{_esc(kind)}' loading='lazy'>")
        elif mime.startswith("audio/"):
            dur = _fmt_dur(d.get("media_dur"))
            out.append(f"<div class='media'>{icon} ovozli xabar{(' · ' + dur) if dur else ''}</div>"
                       f"<audio controls preload='metadata' src='{src}'></audio>")
        else:
            out.append(f"<video controls preload='metadata' src='{src}'></video>")
    else:
        state = d.get("media_state")
        note = {"gone": "havolasi eskirgan — Instagram endi bermaydi",
                "too_big": "fayl juda katta (asli Drive'da)",
                "pending": "yuklab olinmoqda…", "retry": "yuklab olinmoqda…",
                "skipped": "yuklab olinmadi"}.get(state, "")
        if d.get("media_drive"):
            note = "asl fayl Google Drive'da"
        label = f"{icon} {_esc(kind) or 'media'}"
        if d.get("media_problem") and state in ("gone", "skipped"):
            note = note or _esc(str(d["media_problem"]))
        out.append(f"<div class='media'>[{label}]{(' — ' + note) if note else ''}</div>")
    if d.get("media_drive"):
        out.append(f"<div class='media'><a href='{_esc(d['media_drive'])}' target='_blank' "
                   "rel='noopener'>☁️ Asl fayl — Google Drive</a></div>")
    return "".join(out)


def build_html(info: dict, docs: list[dict], media: dict[str, tuple[bytes, str]]) -> str:
    """Instagram yozishmasining o'z ko'rinishidagi HTML sahifasi (rasm, video, ovoz ichida ochiladi)."""
    tz = ZoneInfo(config.TIMEZONE)
    title = info.get("title") or str(info.get("thread_id") or "")
    users = [u for u in (info.get("usernames") or []) if u]
    group = bool(info.get("group"))
    n_del = sum(1 for d in docs if d.get("deleted"))
    n_once = sum(1 for d in docs if d.get("view_once"))
    # Hammasi sig'masa: avval o'chirilgan va bir martalik media (boshqa joyda qolmagan), keyin yangilari
    budget, embed = config.IG_HTML_MEDIA_MB * 1024 * 1024, {}
    for d in sorted(docs, key=lambda x: (not (x.get("deleted") or x.get("view_once")), -(x.get("date") or 0))):
        blob = media.get(d["msg_id"])
        if blob and len(blob[0]) <= budget:
            budget -= len(blob[0])
            embed[d["msg_id"]] = blob
    who = f"@{users[0]}" if users else ""
    sub = [_esc(who), f"{len(docs)} ta xabar"]
    if n_del:
        sub.append(f"🗑 {n_del} ta qaytarib olingan")
    if n_once:
        sub.append(f"🔥 {n_once} ta bir martalik")
    if len(embed) < sum(1 for d in docs if d.get("media")):
        sub.append(f"🖼 {len(embed)} ta fayl ichida")
    out = [
        "<!DOCTYPE html><html lang='uz'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Instagram — {_esc(title)}</title><style>{_CSS}</style></head><body>",
        f"<header><h1>📸 {_esc(title)}{' · 👥 guruh' if group else ''}</h1>",
        f"<div class='sub'>{' · '.join(s for s in sub if s)}</div>",
        "<div class='legend'><span>🔥 bir marta ko‘riladigan</span>"
        "<span>🗑 qaytarib olingan</span><span>🤖 Anna javobi</span></div></header><main>",
    ]
    if not docs:
        out.append("<p class='empty'>Bu chatda hali arxivlangan xabar yo‘q.</p>")
    day = None
    for d in docs:
        dkey = _fmt(d.get("date"), "%d.%m.%Y", tz)
        if dkey != day:
            day = dkey
            out.append(f"<div class='day'><span>{dkey}</span></div>")
        parts = []
        if group and not d.get("out") and d.get("sender"):
            parts.append(f"<div class='who'>{_esc(d['sender'])}</div>")
        if d.get("media"):
            parts.append(_media_block(d, embed.get(d["msg_id"])))
        if d.get("share_url"):   # ulashilgan post/reels/storis — bosib ochiladi
            who = f" @{_esc(d['author'])}" if d.get("author") else ""
            parts.append(f"<div class='media'><a href='{_esc(d['share_url'])}' target='_blank' "
                         f"rel='noopener'>🔗 {_esc(d.get('kind') or 'post')}{who}</a></div>")
        if d.get("text"):
            parts.append(f"<div>{_esc(d['text'])}</div>")
        tags = []
        if d.get("view_once"):
            tags.append("<span class='tag tonce'>🔥 bir martalik</span>")
        if d.get("ai"):
            tags.append("<span class='tag tai'>🤖 Anna</span>")
        if d.get("deleted"):
            tags.append(f"<span class='tag tdel'>🗑 qaytarib olindi "
                        f"{_fmt(d.get('deleted_at'), '%d.%m %H:%M', tz)}</span>")
        name = "Siz" if d.get("out") else ""
        parts.append(f"<div class='meta'>{name + ' · ' if name else ''}"
                     f"{_fmt(d.get('date'), '%H:%M', tz)}{''.join(tags)}</div>")
        side = "out" if d.get("out") else "in"
        cls = "msg del" if d.get("deleted") else "msg"
        out.append(f"<div class='row {side}'><div class='{cls}'>{''.join(parts)}</div></div>")
    made = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    out.append(f"</main><footer>Anna · Instagram arxivi · {made} da tayyorlandi</footer></body></html>")
    return "".join(out)


def _fmt(ts: float | None, pattern: str, tz) -> str:
    return datetime.fromtimestamp(ts, tz).strftime(pattern) if ts else ""


async def export_html(thread_id: str) -> tuple[bytes, str, int]:
    info = await storage.ig_chat_get(thread_id) or {"thread_id": thread_id}
    docs = await storage.ig_archive_export(thread_id, config.ARCHIVE_EXPORT_LIMIT)
    try:
        media = await storage.ig_media_get(thread_id, [d["msg_id"] for d in docs if d.get("media_saved")])
    except Exception:
        log.exception("Instagram arxividagi media o'qilmadi")
        media = {}
    page = build_html(info, docs, media)
    title = info.get("title") or thread_id
    name = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")[:40] or "chat"
    return page.encode("utf-8"), f"instagram_{name}.html", len(docs)


def describe() -> str:
    """/health uchun bir qator."""
    if not config.INSTAGRAM_ENABLED:
        return "o'chirilgan (INSTAGRAM_ENABLED=false)"
    if problem := lib_problem():
        return problem
    if not connected_accounts():
        return "ulanmagan (/ig)" + (f" — {st.problem}" if st.problem else "")
    parts = []
    for acc in connected_accounts():
        s = f"@{acc.username} ulangan"
        if acc.last_check:
            s += f", oxirgi tekshiruv {_when(acc.last_check)}"
        parts.append(s + (f", muammo: {acc.problem}" if acc.problem else ""))
    return "; ".join(parts) + f" (instagrapi {lib_version()})"
