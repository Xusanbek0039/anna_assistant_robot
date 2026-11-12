import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"[config] .env faylida {name} to'ldirilmagan")
    return value


API_ID = int(_req("API_ID"))
API_HASH = _req("API_HASH")
PHONE = _req("PHONE")
BOT_TOKEN = _req("BOT_TOKEN")
OPENAI_API_KEY = _req("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

OWNER_NAME = os.getenv("OWNER_NAME", "Husan Suyunov")
ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Anna")
OWNER_TAKEOVER_MINUTES = int(os.getenv("OWNER_TAKEOVER_MINUTES", "120"))
MAX_REPLIES_PER_MINUTE = int(os.getenv("MAX_REPLIES_PER_MINUTE", "4"))
REPLY_DELAY_SECONDS = float(os.getenv("REPLY_DELAY_SECONDS", "3"))

HISTORY_LIMIT = 20
DB_PATH = "anna.db"
USER_SESSION = "anna"
BOT_SESSION = "anna_bot"

FALLBACK_TEXT = (
    f"Assalomu alaykum! {OWNER_NAME} hozirda band. "
    "Iltimos, xabaringizni yozib qoldiring, men unga yetkazaman."
)
