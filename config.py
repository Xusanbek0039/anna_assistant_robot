"""Sozlamalar .env faylidan o'qiladi. Maxfiy qiymatlar kodda saqlanmaydi."""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

# Barcha fayllar (.env, session, zaxira, iqtiboslar) loyiha papkasiga nisbatan —
# dastur qaysi papkadan ishga tushirilishidan qat'i nazar
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


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

# --- Nazorat boti va AI (faqat main.py / kotiba rejimi uchun) ---
BOT_TOKEN = _str("BOT_TOKEN")
BOT_SESSION = _path("anna_bot")
OPENAI_API_KEY = _str("OPENAI_API_KEY")
OPENAI_MODEL = _str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TRANSCRIBE_MODEL = _str("OPENAI_TRANSCRIBE_MODEL", "whisper-1")  # ovozli xabarlar uchun
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

# --- Xarajat nazorati (OpenAI) ---
AI_DAILY_LIMIT = _int("AI_DAILY_LIMIT", 300)            # kuniga jami AI so'rovlari (0 = cheksiz)
AI_CHAT_DAILY_LIMIT = _int("AI_CHAT_DAILY_LIMIT", 30)   # bitta odam uchun kuniga (0 = cheksiz)

# --- Holat nazorati ---
HEALTH_CHECK_SECONDS = max(60, _int("HEALTH_CHECK_SECONDS", 300))

# --- Ovozli javob ---
VOICE_REPLY_ENABLED = _bool("VOICE_REPLY_ENABLED", True)  # ovozli xabarga ovoz bilan javob
TTS_MODEL = _str("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = _str("TTS_VOICE", "nova")
MAX_REPLIES_PER_MINUTE = _int("MAX_REPLIES_PER_MINUTE", 4)
REPLY_DELAY_SECONDS = _float("REPLY_DELAY_SECONDS", 3)
HISTORY_LIMIT = 20

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
