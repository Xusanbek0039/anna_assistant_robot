"""Sozlamalar .env faylidan o'qiladi. Maxfiy qiymatlar kodda saqlanmaydi."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()


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
USER_SESSION = _str("USER_SESSION", "anna")

# --- Nazorat boti va AI (faqat main.py / kotiba rejimi uchun) ---
BOT_TOKEN = _str("BOT_TOKEN")
BOT_SESSION = "anna_bot"
OPENAI_API_KEY = _str("OPENAI_API_KEY")
OPENAI_MODEL = _str("OPENAI_MODEL", "gpt-4o-mini")

OWNER_NAME = _str("OWNER_NAME", "Husan Suyunov")
ASSISTANT_NAME = _str("ASSISTANT_NAME", "Anna")
OWNER_TAKEOVER_MINUTES = _int("OWNER_TAKEOVER_MINUTES", 120)
MAX_REPLIES_PER_MINUTE = _int("MAX_REPLIES_PER_MINUTE", 4)
REPLY_DELAY_SECONDS = _float("REPLY_DELAY_SECONDS", 3)
HISTORY_LIMIT = 20

FALLBACK_TEXT = (
    f"Assalomu alaykum! {OWNER_NAME} hozirda band. "
    "Iltimos, xabaringizni yozib qoldiring, men unga yetkazaman."
)

# --- MongoDB Atlas ---
MONGODB_URI = _str("MONGODB_URI")
MONGODB_DB = _str("MONGODB_DB", "anna_assistant")

# --- Avtomatik bio ---
BIO_ENABLED = _bool("BIO_ENABLED", True)
DRY_RUN = _bool("DRY_RUN", True)  # xavfsiz standart: Telegram'da hech narsa o'zgarmaydi
BIO_PREFIX = _str("BIO_PREFIX", "Full Stack Developer")
BIO_TEMPLATE = _str("BIO_TEMPLATE", "{prefix} | 🕒 {time} | {icon} {temp}°C")
BIO_MAX_LENGTH = _int("BIO_MAX_LENGTH", 0)  # 0 = avtomatik (oddiy akkaunt 70, Premium 140)
BIO_UPDATE_INTERVAL_SECONDS = max(60, _int("BIO_UPDATE_INTERVAL_SECONDS", 300))
BIO_RETRY_SECONDS = max(30, _int("BIO_RETRY_SECONDS", 120))
BIO_BACKUP_FILE = _str("BIO_BACKUP_FILE", "bio_backup.json")

TIMEZONE = _str("TIMEZONE", "Asia/Tashkent")
CITY_NAME = _str("CITY_NAME", "Toshkent, Uzbekistan")
LATITUDE = _float("LATITUDE", 41.2995)
LONGITUDE = _float("LONGITUDE", 69.2401)
WEATHER_INTERVAL_SECONDS = max(300, _int("WEATHER_INTERVAL_SECONDS", 900))
WEATHER_RETRY_SECONDS = max(60, _int("WEATHER_RETRY_SECONDS", 120))
WEATHER_MAX_AGE_SECONDS = _int("WEATHER_MAX_AGE_SECONDS", 3 * 3600)
