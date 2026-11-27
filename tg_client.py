"""Umumiy yordamchilar: xavfsiz logging va shaxsiy akkauntga (userbot) login."""
import logging
import sqlite3
import sys

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

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
        if config.IG_PROXY:
            # Instagram proksisi: butun qiymat va login:parol qismi (ip:port:login:parol ko'rinishi ham)
            raw = config.IG_PROXY.strip()
            secrets.append(raw)
            if "@" in raw:
                creds = raw.split("://", 1)[-1].rsplit("@", 1)[0]
                secrets += [creds, *creds.split(":", 1)]
            elif raw.count(":") >= 3:
                secrets += raw.split(":", 3)[2:]
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


class NeedsBotLogin(Exception):
    """Akkauntga kirilmagan va terminal yo'q — login nazorat boti orqali (tg_login.via_bot)."""


def create_user_client(session=None) -> TelegramClient:
    """session: fayl nomi yoki StringSession. Berilmasa — SESSION_STRING, u ham bo'lmasa anna.session."""
    if session is None:
        session = StringSession(config.SESSION_STRING) if config.SESSION_STRING else config.USER_SESSION
    return TelegramClient(
        session, config.API_ID, config.API_HASH,
        connection_retries=10, retry_delay=10, auto_reconnect=True,
    )


def _ask_phone() -> str:
    return input("Telegram telefon raqamingiz (+998...): ").strip()


async def login(client: TelegramClient, via_bot: bool = False) -> None:
    """Telethon'ning standart login jarayoni: telefon -> SMS/Telegram kodi -> (bo'lsa) 2FA parol.

    Kod va parol faqat terminalda so'raladi, hech qayerga yozilmaydi va logga chiqmaydi.
    via_bot=True: terminal yo'q va akkauntga kirilmagan bo'lsa — NeedsBotLogin (login botda so'raladi).
    """
    if not sys.stdin or not sys.stdin.isatty():
        # Serverda (Render) kodni kiritadigan terminal yo'q — kod so'rash befoyda va FloodWait'ga olib keladi
        if not client.is_connected():
            try:
                await client.connect()
            except (ConnectionError, OSError) as e:
                sys.exit(f"[telegram] Telegram serveriga ulanib bo'lmadi ({type(e).__name__}). "
                         "Internetni tekshirib, qayta ishga tushiring.")
        if not await client.is_user_authorized():
            if via_bot:
                raise NeedsBotLogin()
            sys.exit("[telegram] Akkauntga kirilmagan va terminal yo'q (server). `python main.py` ni "
                     "ishga tushiring — u nazorat botida login so'raydi (/tglogin). Yoki kompyuterda "
                     "`python export_session.py` chiqargan matnni SESSION_STRING ga qo'ying.")
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


# ---------- nazorat boti ----------
BOT_CONNECT_TIMEOUT = 60   # Telethon qotib qolmasligi uchun (serverda log jim qolib restart bo'lardi)
BOT_ME_TIMEOUT = 30


def _bot_session_files() -> list[str]:
    return [f"{config.BOT_SESSION}.session", f"{config.BOT_SESSION}.session-journal"]


def _wipe_bot_session() -> bool:
    """Bot sessiya faylini o'chiradi (tokendan qayta yaratiladi). Biror fayl o'chirilgan bo'lsa True."""
    import os
    removed = False
    for path in _bot_session_files():
        try:
            os.remove(path)
            removed = True
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("%s o'chirilmadi (%s)", path, type(e).__name__)
    return removed


BOT_FLOOD_MAX_WAIT = 6 * 3600   # Telegram bundan uzoq kuting desa — baribir kutamiz, lekin logda ogohlantiramiz


def _bot_id() -> str:
    """Token boshidagi bot raqami — saqlangan sessiya shu tokenga tegishli ekanini tekshirish uchun."""
    return (config.BOT_TOKEN or "").split(":", 1)[0]


async def _saved_bot_session() -> str | None:
    """MongoDB'dagi bot sessiyasi (Render qayta ishga tushganda fayl yo'qoladi — qayta login FloodWait beradi)."""
    try:
        import storage
        doc = await storage.bot_get_session()
    except Exception as e:
        log.info("Bot sessiyasi bazadan o'qilmadi (%s)", type(e).__name__)
        return None
    if doc and doc.get("bot_id") == _bot_id() and doc.get("string"):
        return doc["string"]
    return None


async def _save_bot_session(bot: TelegramClient) -> None:
    try:
        import storage
        await storage.bot_set_session(StringSession.save(bot.session), _bot_id())
    except Exception as e:
        log.info("Bot sessiyasi bazaga saqlanmadi (%s)", type(e).__name__)


async def _forget_bot_session() -> None:
    try:
        import storage
        await storage.bot_delete_session()
    except Exception:
        pass


async def start_bot(notify=None) -> tuple[TelegramClient, object]:
    """Nazorat botini tokendan ulaydi va (bot, bot_me) qaytaradi.

    - Sessiya MongoDB'da saqlanadi: server qayta ishga tushganda bot qayta «login» qilmaydi. Har safar login
      qilinsa, Telegram «N soniya kuting» (FloodWait) deydi va bot umuman ishlamay qoladi.
    - FloodWait bo'lsa — dastur TO'XTAMAYDI (to'xtasa server qayta ishga tushirib, cheklovni uzaytirardi):
      kerakli vaqt kutiladi va qayta uriniladi. notify(matn) — egasiga xabar (userbot orqali).
    - Sessiya eskirgan bo'lsa (BOT_TOKEN @BotFather'da yangilangan), Telethon ulanishni kutib QOTIB qolishi
      mumkin — kutish vaqti cheklangan: birinchi urinish o'tmasa, sessiya tozalanib bitta qayta urinish qilinadi.
    """
    import asyncio

    last: Exception | None = None
    attempt = 0
    use_saved = True
    while attempt < 2:
        attempt += 1
        saved = await _saved_bot_session() if use_saved else None
        session = StringSession(saved) if saved else config.BOT_SESSION
        bot = TelegramClient(session, config.API_ID, config.API_HASH)
        try:
            await asyncio.wait_for(bot.start(bot_token=config.BOT_TOKEN), timeout=BOT_CONNECT_TIMEOUT)
            me = await asyncio.wait_for(bot.get_me(), timeout=BOT_ME_TIMEOUT)
            if me is None:
                raise RuntimeError("bot o'zini tanimadi (sessiya yaroqsiz)")
            await _save_bot_session(bot)
            if saved:
                log.info("Nazorat boti saqlangan sessiya bilan ulandi (qayta login shart bo'lmadi)")
            return bot, me
        except (errors.AccessTokenInvalidError, errors.AccessTokenExpiredError):
            await _forget_bot_session()
            sys.exit("[telegram] BOT_TOKEN noto'g'ri yoki bekor qilingan. @BotFather -> /mybots -> "
                     "<botingiz> -> API Token dan yangisini oling va .env (serverda: Environment) ga qo'ying.")
        except errors.FloodWaitError as e:
            try:
                await bot.disconnect()
            except Exception:
                pass
            wait = int(getattr(e, "seconds", 0) or 0) + 5
            if wait > BOT_FLOOD_MAX_WAIT:
                log.warning("Telegram juda uzoq kutishni so'radi (%d s)", wait)
            log.warning("Bot uchun Telegram cheklovi: %d soniya (~%d daqiqa) kutaman, keyin o'zim qayta "
                        "ulanaman. Dasturni qayta ishga tushirmang — har qayta urinish cheklovni uzaytiradi.",
                        wait, wait // 60 + 1)
            if notify:
                try:
                    await notify(f"⏳ Anna: nazorat boti uchun Telegram cheklovi — {wait // 60 + 1} daqiqa kutyapman, "
                                 "keyin bot o'zi ishga tushadi. Serverni qayta ishga tushirmang (cheklov uzayadi).")
                except Exception:
                    pass
            await asyncio.sleep(wait)
            attempt -= 1          # cheklov — urinish hisoblanmaydi
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last = e
            name = type(e).__name__
            reason = "kutish vaqti tugadi" if isinstance(e, asyncio.TimeoutError) else name
            try:
                await bot.disconnect()
            except Exception:
                pass
            if attempt == 1:
                log.warning("Nazorat botiga ulanib bo'lmadi (%s) — sessiya tozalanib qayta urinaman", reason)
                _wipe_bot_session()
                await _forget_bot_session()
                use_saved = False
                continue
            sys.exit(f"[telegram] Nazorat botiga ulanib bo'lmadi ({reason}). BOT_TOKEN to'g'riligini va "
                     "serverning internet aloqasini tekshiring.")
    raise last if last else RuntimeError("botga ulanib bo'lmadi")
