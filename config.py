"""Sozlamalar .env faylidan o'qiladi. Maxfiy qiymatlar kodda saqlanmaydi."""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

# Barcha fayllar (.env, session, zaxira, iqtiboslar) loyiha papkasiga nisbatan —
# dastur qaysi papkadan ishga tushirilishidan qat'i nazar
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _find_env_duplicates(path: Path) -> dict[str, list[int]]:
    """.env da bir necha marta yozilgan sozlamalar: {nom: [qator raqamlari]} (qiymatlar o'qilmaydi)."""
    seen: dict[str, list[int]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    for no, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].removeprefix("export ").strip()
        seen.setdefault(key, []).append(no)
    return {k: v for k, v in seen.items() if len(v) > 1}


# Takroriy sozlamada OXIRGI qator amal qiladi — bu ko'pincha kutilmagan natija beradi
ENV_DUPLICATES = _find_env_duplicates(BASE_DIR / ".env")


def _path(value: str) -> str:
    p = Path(value)
    return str(p if p.is_absolute() else BASE_DIR / p)


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        sys.exit(f"[config] {name} butun son bo'lishi kerak")


def _float(name: str, default: float) -> float:
    raw = _str(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        sys.exit(f"[config] {name} son bo'lishi kerak")


def _bool(name: str, default: bool) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "ha")


def require(*names: str) -> None:
    """Berilgan sozlamalar to'ldirilganini tekshiradi (qiymatlarini chiqarmaydi)."""
    missing = [n for n in names if not _str(n)]
    if missing:
        sys.exit(f"[config] .env faylida to'ldirilmagan: {', '.join(missing)}")


# --- Telegram akkaunt ---
API_ID = _int("API_ID", 0)
API_HASH = _str("API_HASH")
PHONE = _str("PHONE")  # ixtiyoriy: bo'sh bo'lsa, Telethon login paytida so'raydi
USER_SESSION = _path(_str("USER_SESSION", "anna"))
# Server (Render va h.k.) uchun: terminal yo'q, kod kiritib bo'lmaydi — session matn ko'rinishida beriladi.
# Olish: kompyuterda `python export_session.py`. MAXFIY — akkauntga to'liq kirish beradi!
SESSION_STRING = _str("SESSION_STRING")

# --- Nazorat boti va AI (faqat main.py / kotiba rejimi uchun) ---
BOT_TOKEN = _str("BOT_TOKEN")
BOT_SESSION = _path("anna_bot")
OPENAI_API_KEY = _str("OPENAI_API_KEY")
OPENAI_MODEL = _str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TRANSCRIBE_MODEL = _str("OPENAI_TRANSCRIBE_MODEL", "whisper-1")  # ovozli xabarlar uchun
# --- Ovozni matnga o'girish: VoiceLab (voicelab.uz) — kalit bo'lsa asosiy, ishlamasa OpenAI zaxira ---
VOICELAB_API_KEY = _str("VOICELAB_API_KEY")  # vlk_... (maxfiy!); bir nechta: kalit1,kalit2,kalit3
VOICELAB_BASE_URL = _str("VOICELAB_BASE_URL", "https://api.voicelab.uz")
VOICELAB_LANGUAGE = _str("VOICELAB_LANGUAGE", "auto")  # auto | uz | ru | en ...
VOICELAB_TIMEOUT = max(15, _int("VOICELAB_TIMEOUT", 120))  # natijani kutish (soniya)
VOICELAB_DELETE_AFTER = _bool("VOICELAB_DELETE_AFTER", True)  # o'qilgach VoiceLab'dan o'chirish
OWNER_PROFILE_FILE = _path(_str("OWNER_PROFILE_FILE", "owner_profile.txt"))  # kotiba aytadigan ochiq ma'lumotlar

OWNER_NAME = _str("OWNER_NAME", "Husan Suyunov")
ASSISTANT_NAME = _str("ASSISTANT_NAME", "Anna")
OWNER_CALL_NAME = _str("OWNER_CALL_NAME", "Husan aka")  # suhbatda egasini shunday ataydi
OWNER_TAKEOVER_MINUTES = _int("OWNER_TAKEOVER_MINUTES", 120)

# --- Aqlli rejim (/auto) ---
BUSY_HOURS = _str("BUSY_HOURS")  # masalan: "Mon-Fri 09:00-18:00; Sat 10:00-14:00"
OWNER_ACTIVE_MINUTES = _int("OWNER_ACTIVE_MINUTES", 10)  # shuncha vaqt ichida yozgan bo'lsangiz — faol
CALENDAR_ICS_URL = _str("CALENDAR_ICS_URL")  # Google Calendar "maxfiy iCal manzili" (maxfiy!)

# --- Kunlik hisobot ---
REPORT_ENABLED = _bool("REPORT_ENABLED", True)
REPORT_TIME = _str("REPORT_TIME", "21:00")  # Toshkent vaqti bilan

# --- Shaxsiy buxgalter (botga yozilgan/aytilgan kirim-chiqimlar) ---
FINANCE_ENABLED = _bool("FINANCE_ENABLED", True)
FINANCE_REPORT_TIME = _str("FINANCE_REPORT_TIME", "22:00")  # kunlik moliya hisoboti (Toshkent vaqti)
FINANCE_CURRENCY = _str("FINANCE_CURRENCY", "so'm")
FINANCE_WEEK_DAY = min(7, max(1, _int("FINANCE_WEEK_DAY", 7)))  # haftalik hisobot kuni: 1=Du ... 7=Ya

# --- Xarajat nazorati (OpenAI) ---
AI_DAILY_LIMIT = _int("AI_DAILY_LIMIT", 300)            # kuniga jami AI so'rovlari (0 = cheksiz)
AI_CHAT_DAILY_LIMIT = _int("AI_CHAT_DAILY_LIMIT", 30)   # bitta odam uchun kuniga (0 = cheksiz)

# --- Holat nazorati ---
HEALTH_CHECK_SECONDS = max(60, _int("HEALTH_CHECK_SECONDS", 300))

# --- Ovozli javob ---
VOICE_REPLY_ENABLED = _bool("VOICE_REPLY_ENABLED", True)  # ovozli xabarga ovoz bilan javob
# Ovozli javob faqat VoiceLab orqali (VOICELAB_API_KEY kerak); zaxira ovoz yo'q
# VoiceLab ovozi: "Madina — Honey" (o'zbek, ayol, iliq va muloyim). Boshqalari (ayol):
#   Nozima — Soft: voice_3602ec76-5393-43b6-9e0d-784328cb77ba, Kamola — Silk: voice_47260d60-5635-4fb0-904d-0f3c19c04ce6
#   Safiya — Reassuring guide: voice_2785aabd-21b5-4cc7-9845-2230186ef7d9, Dilnoza — Velvet: voice_f7179d1c-07ab-4b3d-b5be-84dcf2935388
#   Gulya — Sweet streamer: voice_7b350d91-4e56-4bc3-869c-8cd6ba646c3c
VOICELAB_TTS_VOICE = _str("VOICELAB_TTS_VOICE", "voice_4ef06e6b-2229-4023-ad66-c2a7a2b736ff")
VOICELAB_TTS_SPEED = _float("VOICELAB_TTS_SPEED", 1.0)  # 0.5 — 2.0
VOICELAB_TTS_MODEL = _str("VOICELAB_TTS_MODEL")  # bo'sh — VoiceLab standarti (lison)
MAX_REPLIES_PER_MINUTE = _int("MAX_REPLIES_PER_MINUTE", 4)
REPLY_DELAY_SECONDS = _float("REPLY_DELAY_SECONDS", 3)
# Kotiba javobni suhbatdoshning aynan o'sha xabariga "reply" qilib yozadi
REPLY_QUOTE = _bool("REPLY_QUOTE", True)
MAX_MEDIA_MB = _int("MAX_MEDIA_MB", 50)  # bundan katta media yuklab olinmaydi (egasiga ham yuborilmaydi)
SAVE_MAX_MB = _int("SAVE_MAX_MB", 2000)  # havola orqali saqlashda (/save) eng katta fayl hajmi
# --- Chat arxivi: suhbatdosh yozishmani o'chirsa ham, bot orqali HTML qilib qayta olish ---
ARCHIVE_ENABLED = _bool("ARCHIVE_ENABLED", True)      # barcha shaxsiy chatlar (istisnodagilar ham) saqlanadi
ARCHIVE_GROUPS = _bool("ARCHIVE_GROUPS", False)       # guruhlar ham (standart o'chiq — xabar ko'p bo'ladi)
ARCHIVE_ALERTS = _bool("ARCHIVE_ALERTS", True)        # xabar o'chirilsa — botga ogohlantirish
ARCHIVE_MEDIA_MAX_KB = _int("ARCHIVE_MEDIA_MAX_KB", 512)  # rasm/ovozli xabar shu hajmgacha arxivga (0 = yo'q)
ARCHIVE_EXPORT_LIMIT = _int("ARCHIVE_EXPORT_LIMIT", 5000)  # HTML da chatning oxirgi nechta xabari
# Chat tarixini olganda arxivda yo'q rasm/ovozli/video-xabarlar Telegram'dan shu hajmgacha (KB) yuklab olinadi
# va HTML ichida ko'rinadi (0 = yo'q). Kattaroq rasmning kichikroq nusxasi olinadi
EXPORT_MEDIA_MAX_KB = _int("EXPORT_MEDIA_MAX_KB", 2048)
# Ishga tushganda mavjud chatlardan ham nusxa: birinchi marta har chatdan shuncha oxirgi xabar (0 = yo'q)
ARCHIVE_SYNC_LIMIT = _int("ARCHIVE_SYNC_LIMIT", 1000)
# --- Google Drive: chat arxivi Drive papkasiga HTML fayl bo'lib yuklanadi (har chatga bitta fayl) ---
GOOGLE_OAUTH_CLIENT_ID = _str("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = _str("GOOGLE_OAUTH_CLIENT_SECRET")      # maxfiy!
GOOGLE_OAUTH_REFRESH_TOKEN = _str("GOOGLE_OAUTH_REFRESH_TOKEN")      # maxfiy! `python drive_auth.py` bilan olinadi
GOOGLE_DRIVE_ROOT_FOLDER_ID = _str("GOOGLE_DRIVE_ROOT_FOLDER_ID")
DRIVE_SYNC_MINUTES = max(1, _int("DRIVE_SYNC_MINUTES", 10))  # o'zgargan chatlar shuncha daqiqada bir yuklanadi
# Chatlardagi rasm, video, ovozli va video-xabarlar Drive'ga asl sifatida (chat papkasiga) yuklanib boradi
DRIVE_MEDIA_ENABLED = _bool("DRIVE_MEDIA_ENABLED", True)
DRIVE_MEDIA_MAX_MB = _int("DRIVE_MEDIA_MAX_MB", 300)  # bundan katta fayl yuklanmaydi
DRIVE_MEDIA_HISTORY = _bool("DRIVE_MEDIA_HISTORY", True)  # mavjud chatlardagi eski rasm/videolar ham

# --- Kuzatuv: tahrir va profil o'zgarishlari ---
EDIT_ALERTS = _bool("EDIT_ALERTS", True)              # suhbatdosh xabarini tahrirlasa — ogohlantirish
PROFILE_WATCH = _bool("PROFILE_WATCH", True)          # ism, username, bio, rasm o'zgarsa — ogohlantirish
PROFILE_CHECK_HOURS = max(1, _int("PROFILE_CHECK_HOURS", 6))
PROFILE_PHOTOS = _bool("PROFILE_PHOTOS", True)        # profil rasmlari tarixi Drive'dagi chat papkasiga

# --- 🔔 Eslatmalar: raqam Telegram'da paydo bo'lsa / odam online bo'lsa — xabar (watchers.py) ---
WATCH_ENABLED = _bool("WATCH_ENABLED", True)
WATCH_PHONE_MINUTES = max(5, _int("WATCH_PHONE_MINUTES", 15))      # raqam har shuncha daqiqada tekshiriladi
WATCH_ONLINE_SECONDS = max(30, _int("WATCH_ONLINE_SECONDS", 60))   # online holat zaxira tekshiruvi
WATCH_ONLINE_COOLDOWN_MINUTES = max(1, _int("WATCH_ONLINE_COOLDOWN_MINUTES", 10))  # «har safar» oralig'i
WATCH_COUNTRY_CODE = _str("WATCH_COUNTRY_CODE", "998")  # 9 xonali raqamga qo'shiladi: 90 123 45 67

# --- 📸 Instagram (instagram.py, botda /ig): bir martalik media, avtojavob, chat arxivi ---
INSTAGRAM_ENABLED = _bool("INSTAGRAM_ENABLED", True)
IG_POLL_SECONDS = max(15, _int("IG_POLL_SECONDS", 30))   # Direct shuncha soniyada bir tekshiriladi
IG_AUTOREPLY = _bool("IG_AUTOREPLY", False)              # standart holat (botdan yoqib-o'chiriladi)
IG_FORWARD = _bool("IG_FORWARD", False)                  # yangi Direct xabarlarini botga yuborish
IG_PROXY = _str("IG_PROXY")                              # ixtiyoriy: http://user:pass@host:port (server IP bloklansa)

# --- 👀 Kim qiziqdi (viewers.py, botda /korganlar): storis ko'ruvchilari va profildagi havola ---
VIEWERS_ENABLED = _bool("VIEWERS_ENABLED", True)
VIEWERS_CHECK_MINUTES = max(1, _int("VIEWERS_CHECK_MINUTES", 3))

# --- Va'dalar (AI chat yozishmalaridan topadi va eslatadi) ---
PROMISE_SCAN = _bool("PROMISE_SCAN", True)
PROMISE_SCAN_TIME = _str("PROMISE_SCAN_TIME", "21:30")
PROMISE_REMIND_TIME = _str("PROMISE_REMIND_TIME", "09:00")
PROMISE_SCAN_CHATS = max(1, _int("PROMISE_SCAN_CHATS", 15))  # kechki tekshiruvda ko'pi bilan nechta chat

# --- Haftalik chat hisoboti ---
WEEKLY_REPORT = _bool("WEEKLY_REPORT", True)
WEEKLY_REPORT_DAY = min(7, max(1, _int("WEEKLY_REPORT_DAY", 7)))  # 1=Du ... 7=Ya
WEEKLY_REPORT_TIME = _str("WEEKLY_REPORT_TIME", "20:00")

# --- Bazani Google Drive'ga zaxiralash ---
BACKUP_ENABLED = _bool("BACKUP_ENABLED", True)
BACKUP_TIME = _str("BACKUP_TIME", "04:00")
BACKUP_KEEP_DAYS = max(1, _int("BACKUP_KEEP_DAYS", 14))
BACKUP_MEDIA = _bool("BACKUP_MEDIA", True)  # arxivdagi kichik media (archive_media) ham
DRIVE_MEDIA_FILES = _bool("DRIVE_MEDIA_FILES", False)  # hujjat/fayllar (pdf, zip ...) ham yuklansinmi
# Bir vaqtda faqat bitta nusxa ishlasin (ikki nusxa = bot har xabarni ikki marta yuboradi)
SINGLE_INSTANCE = _bool("SINGLE_INSTANCE", True)
HISTORY_LIMIT = _int("HISTORY_LIMIT", 30)  # AI ga beriladigan oxirgi xabarlar soni

FALLBACK_TEXT = (
    f"Assalomu alaykum! {OWNER_NAME} hozirda band. "
    "Iltimos, xabaringizni yozib qoldiring, men unga yetkazaman."
)

# --- Online holat ---
KEEP_ONLINE = _bool("KEEP_ONLINE", True)  # dastur ishlayotganda akkaunt "online" ko'rinadi
ONLINE_INTERVAL_SECONDS = max(30, _int("ONLINE_INTERVAL_SECONDS", 60))

# --- MongoDB Atlas ---
MONGODB_URI = _str("MONGODB_URI")
MONGODB_DB = _str("MONGODB_DB", "anna_assistant")

# --- Avtomatik bio ---
BIO_ENABLED = _bool("BIO_ENABLED", True)
DRY_RUN = _bool("DRY_RUN", True)  # xavfsiz standart: Telegram'da hech narsa o'zgarmaydi
BIO_PREFIX = _str("BIO_PREFIX", "Full Stack Developer")
BIO_TEMPLATE = _str("BIO_TEMPLATE", "{prefix} | 🕒 {time} | {icon} {temp}°C")
NAME_CLOCK_ENABLED = _bool("NAME_CLOCK_ENABLED", True)  # familiya yonida joriy vaqt
NAME_TEMPLATE = _str("NAME_TEMPLATE", "{last_name} 🕒 {time}")
BIO_QUOTE_ENABLED = _bool("BIO_QUOTE_ENABLED", True)
BIO_QUOTE = _str("BIO_QUOTE")  # bitta doimiy iqtibos; bo'sh bo'lsa quotes.txt dan har kuni almashadi
BIO_QUOTES_FILE = _path(_str("BIO_QUOTES_FILE", "quotes.txt"))
BIO_MAX_LENGTH = _int("BIO_MAX_LENGTH", 0)  # 0 = avtomatik (oddiy akkaunt 70, Premium 140)
BIO_UPDATE_INTERVAL_SECONDS = max(60, _int("BIO_UPDATE_INTERVAL_SECONDS", 60))
BIO_RETRY_SECONDS = max(30, _int("BIO_RETRY_SECONDS", 120))
BIO_BACKUP_FILE = _path(_str("BIO_BACKUP_FILE", "bio_backup.json"))

TIMEZONE = _str("TIMEZONE", "Asia/Tashkent")
CITY_NAME = _str("CITY_NAME", "Toshkent, Uzbekistan")
LATITUDE = _float("LATITUDE", 41.2995)
LONGITUDE = _float("LONGITUDE", 69.2401)
WEATHER_INTERVAL_SECONDS = max(300, _int("WEATHER_INTERVAL_SECONDS", 900))
WEATHER_RETRY_SECONDS = max(60, _int("WEATHER_RETRY_SECONDS", 120))
WEATHER_MAX_AGE_SECONDS = _int("WEATHER_MAX_AGE_SECONDS", 3 * 3600)
