"""Umumiy yordamchilar: xavfsiz logging va shaxsiy akkauntga (userbot) login."""
import logging
import sqlite3
import sys

from telethon import TelegramClient, errors

import config

log = logging.getLogger("telegram")


class _RedactSecrets(logging.Filter):
    """Har qanday log yozuvidan maxfiy qiymatlarni (API_HASH, telefon, token, URI) olib tashlaydi."""

    def __init__(self) -> None:
        super().__init__()
        secrets = [config.API_HASH, config.PHONE, config.BOT_TOKEN, config.OPENAI_API_KEY,
                   config.MONGODB_URI, config.CALENDAR_ICS_URL]
        if config.MONGODB_URI and "@" in config.MONGODB_URI:
            # URI ichidagi login:parol qismi alohida ham yashiriladi
            creds = config.MONGODB_URI.split("://", 1)[-1].split("@", 1)[0]
            secrets += [creds, *creds.split(":", 1)]
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = msg
        for s in self._secrets:
            clean = clean.replace(s, "***")
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows konsolida emoji uchun
        except (AttributeError, ValueError):
            pass
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    handler.addFilter(_RedactSecrets())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # Telethon INFO darajasida ichki tafsilotlarni yozadi — faqat ogohlantirishlar qoladi
    logging.getLogger("telethon").setLevel(logging.WARNING)
    for name in ("pymongo", "httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    for key, lines in config.ENV_DUPLICATES.items():
        logging.getLogger("config").warning(
            ".env da %s %d marta yozilgan (qatorlar: %s) — faqat OXIRGISI (%d-qator) amal qiladi. "
            "Ortiqchasini o'chiring.", key, len(lines), ", ".join(map(str, lines)), lines[-1])


def create_user_client() -> TelegramClient:
    return TelegramClient(
        config.USER_SESSION, config.API_ID, config.API_HASH,
        connection_retries=10, retry_delay=10, auto_reconnect=True,
    )


def _ask_phone() -> str:
    return input("Telegram telefon raqamingiz (+998...): ").strip()


async def login(client: TelegramClient) -> None:
    """Telethon'ning standart login jarayoni: telefon -> SMS/Telegram kodi -> (bo'lsa) 2FA parol.

    Kod va parol faqat terminalda so'raladi, hech qayerga yozilmaydi va logga chiqmaydi.
    """
    try:
        await client.start(phone=config.PHONE or _ask_phone)
    except errors.ApiIdInvalidError:
        sys.exit("[telegram] API_ID yoki API_HASH noto'g'ri. my.telegram.org dan qayta tekshiring.")
    except errors.PhoneNumberInvalidError:
        sys.exit("[telegram] Telefon raqami noto'g'ri formatda (+998XXXXXXXXX ko'rinishida kiriting).")
    except errors.PhoneNumberBannedError:
        sys.exit("[telegram] Bu telefon raqami Telegram tomonidan bloklangan.")
    except errors.FloodWaitError as e:
        sys.exit(f"[telegram] Login urinishlari ko'p bo'ldi. {e.seconds} soniyadan keyin qayta urining.")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            sys.exit(f"[telegram] '{config.USER_SESSION}.session' boshqa jarayonda ochiq. "
                     "Avval boshqa nusxani (main.py yoki bio_updater.py) to'xtating.")
        raise
    except (ConnectionError, OSError) as e:
        sys.exit(f"[telegram] Telegram serveriga ulanib bo'lmadi ({type(e).__name__}). "
                 "Internetni tekshirib, qayta ishga tushiring.")

    me = await client.get_me()
    if me is None or getattr(me, "bot", False):
        sys.exit("[telegram] Shaxsiy akkaunt bilan kirish kerak (bot token emas).")
    log.info("Akkauntga kirildi: %s (id=%s)", me.first_name, me.id)
