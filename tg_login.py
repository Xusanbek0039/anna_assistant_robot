"""🔑 Telegram akkauntni nazorat boti orqali ulash (terminal kerak emas — server/Render uchun).

Akkaunt ulanmagan bo'lsa (sessiya yo'q yoki bekor qilingan) va terminal bo'lmasa, Anna nazorat botini
ishga tushiradi va botda so'raydi: telefon raqam -> Telegram kodi -> (yoqilgan bo'lsa) 2FA parol.
Kirilgach sessiya MongoDB'da saqlanadi — keyingi ishga tushishlarda qayta so'ralmaydi.

Kim login qila oladi:
- OWNER_ID (.env) yoki bazadagi oxirgi akkaunt egasi;
- ikkalasi ham bo'lmasa — logda chiqadigan bir martalik kod bilan: botga `/tglogin 123456`.

Muhim: Telegram kodini botga oddiy yozsangiz, Telegram uni darhol bekor qiladi (kod «ulashildi» deb).
Shuning uchun kod bo'sh joy bilan yuboriladi: `1 2 3 4 5`. Kod va parolli xabarlar darhol o'chiriladi va
hech qayerga yozilmaydi.
"""
import asyncio
import hmac
import logging
import os
import re
import secrets
import sqlite3
import sys

from telethon import TelegramClient, errors, events
from telethon.sessions import StringSession

import config
import storage
from tg_client import NeedsBotLogin, create_user_client  # noqa: F401 (NeedsBotLogin — main.py uchun)

log = logging.getLogger("tg_login")

MAX_SETUP_TRIES = 5   # begona odam bir martalik kodni topishga urinsa — shuncha xatodan keyin e'tiborsiz


def has_terminal() -> bool:
    return bool(sys.stdin) and sys.stdin.isatty()


def normalize_phone(text: str) -> str | None:
    """«+998 90 123-45-67», «998901234567», «90 123 45 67» -> «+998901234567»."""
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 9 and config.WATCH_COUNTRY_CODE:
        digits = config.WATCH_COUNTRY_CODE + digits
    return f"+{digits}" if 10 <= len(digits) <= 15 else None


def code_digits(text: str) -> str | None:
    """«1 2 3 4 5» / «12-345» -> «12345» (Telegram kodi 5-6 raqam)."""
    digits = re.sub(r"\D", "", text or "")
    return digits if 5 <= len(digits) <= 6 else None


async def _saved_session() -> str | None:
    try:
        doc = await storage.tg_get_session()
    except Exception as e:
        log.warning("Bazadagi Telegram sessiyasi o'qilmadi (%s)", type(e).__name__)
        return None
    return (doc or {}).get("string") or None


async def _authorized(client: TelegramClient) -> bool:
    await client.connect()
    return await client.is_user_authorized()


async def open_client() -> TelegramClient:
    """Ishlaydigan sessiyani topadi: SESSION_STRING -> anna.session fayli -> bazadagi sessiya.

    Hech biri kirilgan bo'lmasa: terminal bor — fayl sessiyasi (login terminalda so'raladi),
    terminal yo'q — bo'sh sessiya (login bot orqali).
    """
    candidates = []
    if config.SESSION_STRING:
        candidates.append(("SESSION_STRING", lambda: create_user_client(StringSession(config.SESSION_STRING))))
    if os.path.exists(f"{config.USER_SESSION}.session"):
        candidates.append(("session fayli", lambda: create_user_client(config.USER_SESSION)))
    saved = await _saved_session()
    if saved:
        candidates.append(("bazadagi sessiya", lambda: create_user_client(StringSession(saved))))

    for name, make in candidates:
        try:
            client = make()
        except ValueError:   # StringSession buzilgan / noto'liq nusxalangan
            log.warning("Telegram sessiyasi (%s) yaroqsiz matn — e'tiborsiz qoldirildi", name)
            continue
        try:
            if await _authorized(client):
                log.info("Telegram sessiyasi: %s", name)
                return client
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                sys.exit(f"[telegram] '{config.USER_SESSION}.session' boshqa jarayonda ochiq. "
                         "Avval boshqa nusxani (main.py yoki bio_updater.py) to'xtating.")
            raise
        except (ConnectionError, OSError):
            return client   # tarmoq yo'q — login() tushunarli xabar bilan to'xtaydi
        log.warning("Telegram sessiyasi (%s) bekor qilingan yoki kirilmagan", name)
        await client.disconnect()

    if has_terminal():
        return create_user_client(config.USER_SESSION)
    return create_user_client(StringSession())


async def save_session(client: TelegramClient, user_id: int) -> None:
    """Kirilgan sessiyani bazaga yozadi (server qayta ishga tushganda login so'ralmaydi)."""
    try:
        await storage.tg_set_session(StringSession.save(client.session), user_id)
        await storage.tg_set_owner(user_id)
    except Exception as e:
        log.warning("Telegram sessiyasi bazaga saqlanmadi (%s) — keyingi safar yana login so'raladi",
                    type(e).__name__)


async def remember_owner(user_id: int) -> None:
    try:
        await storage.tg_set_owner(user_id)
    except Exception:
        pass


async def _known_owner() -> int | None:
    if config.OWNER_ID:
        return config.OWNER_ID
    try:
        return await storage.tg_get_owner()
    except Exception as e:
        log.warning("Akkaunt egasi bazadan o'qilmadi (%s)", type(e).__name__)
        return None


class _Dialog:
    """Bot bilan savol-javob: egasining navbatdagi xabarini kutadi."""

    def __init__(self, bot: TelegramClient, owner: int | None):
        self.bot = bot
        self.owner = owner
        self.setup_code = None if owner else f"{secrets.randbelow(10 ** 6):06d}"
        self.queue: asyncio.Queue = asyncio.Queue()
        self.fails: dict[int, int] = {}

    async def on_message(self, event) -> None:
        if not event.is_private:
            return
        text = (event.raw_text or "").strip()
        sender = event.sender_id
        if self.owner is None:
            m = re.match(r"^/(?:tglogin|start)(?:@\w+)?\s+(\d{6})$", text)
            if m and self.fails.get(sender, 0) < MAX_SETUP_TRIES and \
                    hmac.compare_digest(m.group(1), self.setup_code):
                self.owner = sender
                await self.queue.put(event)
            elif self.fails.get(sender, 0) < MAX_SETUP_TRIES:
                if m:
                    self.fails[sender] = self.fails.get(sender, 0) + 1
                await event.respond("⏳ Anna hali ishga tushmagan: Telegram akkaunt ulanmagan.\n"
                                    "Egasi bo'lsangiz — server logidagi kod bilan yuboring: /tglogin 123456")
            return
        if sender == self.owner:
            await self.queue.put(event)

    async def say(self, text: str) -> None:
        try:
            await self.bot.send_message(self.owner, text, parse_mode="html")
        except Exception as e:
            log.warning("Botdan egasiga yozib bo'lmadi (%s) — botga /start bosing", type(e).__name__)

    async def ask(self, text: str | None = None):
        if text:
            await self.say(text)
        return await self.queue.get()

    async def forget(self, event) -> None:
        """Kod yoki parolli xabarni chatdan o'chiradi."""
        try:
            await event.delete()
        except Exception:
            pass


PHONE_PROMPT = ("🔑 <b>Telegram akkaunt ulanmagan.</b>\n\n"
                "Telefon raqamingizni yuboring: <code>+998901234567</code>\n"
                "<i>Bekor qilish/qaytadan: /tglogin</i>")
CODE_PROMPT = ("📨 Telegram'dan kod keldi (Telegram ilovasidagi «Telegram» chatida yoki SMS).\n\n"
               "Kodni <b>bo'sh joy bilan</b> yuboring: <code>1 2 3 4 5</code>\n"
               "⚠️ Bo'sh joysiz yozsangiz, Telegram kodni darhol bekor qiladi.")
PASSWORD_PROMPT = ("🔒 Akkauntda ikki bosqichli parol yoqilgan. Parolni yuboring — xabar darhol o'chiriladi "
                   "va hech qayerga yozilmaydi.")


async def via_bot(bot: TelegramClient, user: TelegramClient) -> None:
    """Akkauntga nazorat boti orqali kiradi. Kirilmaguncha kutadi (ishga tushish shu yerda turadi)."""
    dialog = _Dialog(bot, await _known_owner())
    handler = dialog.on_message
    bot.add_event_handler(handler, events.NewMessage(incoming=True))
    try:
        if dialog.owner is None:
            log.warning("🔑 Telegram akkaunt ulanmagan. Nazorat botiga yuboring: /tglogin %s  "
                        "(bir martalik kod; yoki .env da OWNER_ID ni yozing)", dialog.setup_code)
            await dialog.ask()   # to'g'ri kod kelguncha kutadi
        else:
            log.warning("🔑 Telegram akkaunt ulanmagan — nazorat botida login so'raldi (telefon, kod, parol)")
        await _login_dialog(dialog, user)
    finally:
        bot.remove_event_handler(handler)

    me = await user.get_me()
    await save_session(user, me.id)
    await dialog.say(f"✅ Akkauntga kirildi: <b>{me.first_name or ''}</b> (id <code>{me.id}</code>). "
                     "Anna ishga tushyapti...")


async def _login_dialog(dialog: _Dialog, user: TelegramClient) -> None:
    if not user.is_connected():
        await user.connect()
    phone = normalize_phone(config.PHONE) if config.PHONE else None
    prompt = PHONE_PROMPT
    while True:
        if not phone:
            event = await dialog.ask(prompt)
            text = (event.raw_text or "").strip()
            if text.lower().startswith(("/tglogin", "/start")):
                prompt = PHONE_PROMPT
                continue
            phone = normalize_phone(text)
            if not phone:
                prompt = "❌ Raqam tushunarsiz. Masalan: <code>+998901234567</code>"
                continue

        try:
            sent = await user.send_code_request(phone)
        except errors.PhoneNumberInvalidError:
            phone, prompt = None, "❌ Bu raqam noto'g'ri. Qaytadan yuboring: <code>+998901234567</code>"
            continue
        except errors.PhoneNumberBannedError:
            phone, prompt = None, "⛔️ Bu raqam Telegram tomonidan bloklangan. Boshqa raqam yuboring."
            continue
        except errors.ApiIdInvalidError:
            await dialog.say("❌ API_ID yoki API_HASH noto'g'ri (my.telegram.org). Sozlamani tuzatib, qayta "
                             "ishga tushiring.")
            sys.exit("[telegram] API_ID yoki API_HASH noto'g'ri. my.telegram.org dan qayta tekshiring.")
        except errors.FloodWaitError as e:
            await dialog.say(f"⏳ Telegram cheklovi: {e.seconds} soniyadan keyin kod qayta so'raladi.")
            await asyncio.sleep(e.seconds + 1)
            continue

        if await _enter_code(dialog, user, phone, sent.phone_code_hash):
            return
        phone, prompt = None, PHONE_PROMPT   # bekor qilindi yoki kod eskirdi — boshidan


async def _enter_code(dialog: _Dialog, user: TelegramClient, phone: str, code_hash: str) -> bool:
    """True — akkauntga kirildi; False — boshidan boshlash kerak (telefon so'raladi)."""
    prompt = CODE_PROMPT
    while True:
        event = await dialog.ask(prompt)
        text = (event.raw_text or "").strip()
        if text.lower().startswith(("/tglogin", "/start")):
            return False
        await dialog.forget(event)
        code = code_digits(text)
        if not code:
            prompt = "❌ Kod 5 raqamdan iborat. Bo'sh joy bilan yuboring: <code>1 2 3 4 5</code>"
            continue
        try:
            await user.sign_in(phone, code, phone_code_hash=code_hash)
            return True
        except errors.SessionPasswordNeededError:
            return await _enter_password(dialog, user)
        except errors.PhoneCodeInvalidError:
            prompt = "❌ Kod noto'g'ri. Qaytadan yuboring (bo'sh joy bilan): <code>1 2 3 4 5</code>"
        except errors.PhoneCodeExpiredError:
            await dialog.say("⌛️ Kod eskirgan yoki bekor qilingan (bo'sh joysiz yuborilgan bo'lishi mumkin). "
                             "Yangi kod so'rayman.")
            return False
        except errors.PhoneNumberUnoccupiedError:
            await dialog.say("❌ Bu raqamda Telegram akkaunt yo'q. Avval Telegram ilovasida ro'yxatdan o'ting.")
            return False
        except errors.FloodWaitError as e:
            await dialog.say(f"⏳ Telegram cheklovi: {e.seconds} soniya kuting, keyin kodni qayta yuboring.")
            await asyncio.sleep(e.seconds + 1)


async def _enter_password(dialog: _Dialog, user: TelegramClient) -> bool:
    prompt = PASSWORD_PROMPT
    while True:
        event = await dialog.ask(prompt)
        text = event.raw_text or ""
        if text.strip().lower().startswith(("/tglogin", "/start")):
            return False
        await dialog.forget(event)
        try:
            await user.sign_in(password=text)
            return True
        except errors.PasswordHashInvalidError:
            prompt = "❌ Parol noto'g'ri. Qaytadan yuboring:"
        except errors.FloodWaitError as e:
            await dialog.say(f"⏳ Telegram cheklovi: {e.seconds} soniya kuting, keyin parolni qayta yuboring.")
            await asyncio.sleep(e.seconds + 1)
