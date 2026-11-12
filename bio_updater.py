"""Telegram bio'sini avtomatik yangilash.

Natija:  Full Stack Developer | 🕒 14:35 | 🌧️ 18°C

Alohida ishga tushirish:
    python bio_updater.py            # doimiy ishlaydi (Ctrl+C bilan to'xtaydi)
    python bio_updater.py --once     # bir marta yangilab chiqadi
    python bio_updater.py --restore  # asl (zaxiradagi) bio'ni qaytaradi
    python bio_updater.py --show     # zaxiradagi asl bio'ni ko'rsatadi

main.py ishga tushganda (BIO_ENABLED=true) shu modul kotiba bilan birga ishlaydi.
"""
import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo.errors import PyMongoError
from telethon import TelegramClient, errors
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest

import config
import runtime
import storage

log = logging.getLogger("bio")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
FALLBACK_ICON = "🌡️"
FALLBACK_TEMP = "--"
BIO_LIMIT_DEFAULT = 70   # oddiy akkaunt
BIO_LIMIT_PREMIUM = 140  # Telegram Premium
NAME_LIMIT = 64          # ism/familiya cheklovi
QUOTE_FORMAT = " | “{}”"
INSTANCE: "BioUpdater | None" = None  # ishlayotgan nusxa (nazorat boti uchun)

# Open-Meteo WMO ob-havo kodlari (https://open-meteo.com/en/docs -> "WMO Weather interpretation codes")
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("☀️", "ochiq osmon"),
    1: ("🌤️", "asosan ochiq"),
    2: ("⛅", "qisman bulutli"),
    3: ("☁️", "bulutli"),
    45: ("🌫️", "tuman"),
    48: ("🌫️", "qirov tuman"),
    51: ("🌧️", "yengil shivalama"),
    53: ("🌧️", "shivalama"),
    55: ("🌧️", "kuchli shivalama"),
    56: ("🌧️", "muzli shivalama"),
    57: ("🌧️", "kuchli muzli shivalama"),
    61: ("🌧️", "yengil yomg'ir"),
    63: ("🌧️", "yomg'ir"),
    65: ("🌧️", "kuchli yomg'ir"),
    66: ("🌧️", "muzli yomg'ir"),
    67: ("🌧️", "kuchli muzli yomg'ir"),
    71: ("❄️", "yengil qor"),
    73: ("❄️", "qor"),
    75: ("❄️", "kuchli qor"),
    77: ("❄️", "qor donachalari"),
    80: ("🌧️", "yengil jala"),
    81: ("🌧️", "jala"),
    82: ("🌧️", "kuchli jala"),
    85: ("❄️", "qor yog'ishi"),
    86: ("❄️", "kuchli qor yog'ishi"),
    95: ("⛈️", "momaqaldiroq"),
    96: ("⛈️", "do'lli momaqaldiroq"),
    99: ("⛈️", "kuchli do'lli momaqaldiroq"),
}


class WeatherError(Exception):
    pass


@dataclass
class Weather:
    temp: int
    code: int
    icon: str
    desc: str
    fetched_at: float


def weather_icon(code: int) -> tuple[str, str]:
    return WMO_CODES.get(code, (FALLBACK_ICON, f"noma'lum kod {code}"))


def round_temp(value: float) -> int:
    """Matematik yaxlitlash (Python round() 0.5 ni juftga yaxlitlaydi)."""
    return math.floor(value + 0.5)


def tg_len(text: str) -> int:
    """Telegram matn uzunligini UTF-16 birliklarida sanaydi (emoji 2 ta hisoblanadi) — xavfsiz tomonga."""
    return len(text.encode("utf-16-le")) // 2


def _fetch_weather_sync() -> Weather:
    query = urllib.parse.urlencode({
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "current": "temperature_2m,weather_code",
        "temperature_unit": "celsius",
        "timezone": config.TIMEZONE,
    })
    req = urllib.request.Request(f"{OPEN_METEO_URL}?{query}",
                                 headers={"User-Agent": "anna-assistant-bio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise WeatherError(f"Open-Meteo HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WeatherError(f"tarmoq xatosi ({type(e).__name__})") from None
    except json.JSONDecodeError:
        raise WeatherError("javob JSON emas") from None
    try:
        cur = data["current"]
        temp = float(cur["temperature_2m"])
        code = int(cur["weather_code"])
    except (KeyError, TypeError, ValueError):
        raise WeatherError("javob formati kutilganidek emas") from None
    icon, desc = weather_icon(code)
    return Weather(round_temp(temp), code, icon, desc, time.time())


async def _safe_db(coro, what: str):
    """MongoDB vaqtincha ishlamasa, bio yangilanishi to'xtab qolmasligi uchun."""
    if not config.MONGODB_URI:  # Mongo sozlanmagan — faqat mahalliy zaxira
        coro.close()
        return None
    try:
        return await coro
    except PyMongoError as e:
        log.warning("MongoDB: %s saqlanmadi/o'qilmadi (%s)", what, type(e).__name__)
        return None


# ---------- asl bio zaxirasi (mahalliy fayl + MongoDB) ----------
def _read_local_backup() -> dict | None:
    try:
        with open(config.BIO_BACKUP_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and "original_bio" in data else None
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        log.warning("%s o'qib bo'lmadi", config.BIO_BACKUP_FILE)
        return None


def _write_local_backup(original: str, saved_at: float, **extra) -> bool:
    """Zaxira faylini yozadi. `extra` — mavjud faylga qo'shiladigan maydonlar (masalan asl ism)."""
    tmp = config.BIO_BACKUP_FILE + ".tmp"
    data = {**(_read_local_backup() or {}), "original_bio": original, "saved_at": saved_at, **extra}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config.BIO_BACKUP_FILE)
        return True
    except OSError as e:
        log.error("Mahalliy zaxira yozilmadi: %s", type(e).__name__)
        return False


async def get_original_bio() -> str | None:
    """Zaxiradagi asl bio: avval mahalliy fayldan, bo'lmasa MongoDB'dan."""
    local = _read_local_backup()
    if local is not None:
        return local["original_bio"]
    state = await _safe_db(storage.get_bio_state(), "bio zaxirasi") or {}
    return state.get("original_bio")


def load_quotes() -> list[str]:
    """BIO_QUOTE (bitta doimiy iqtibos) yoki quotes.txt dagi ro'yxat. O'chirilgan bo'lsa — bo'sh."""
    if not config.BIO_QUOTE_ENABLED:
        return []
    if config.BIO_QUOTE:
        return [config.BIO_QUOTE]
    try:
        with open(config.BIO_QUOTES_FILE, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except FileNotFoundError:
        log.warning("%s topilmadi — bio iqtibossiz chiqadi", config.BIO_QUOTES_FILE)
    except OSError as e:
        log.warning("%s o'qilmadi (%s) — bio iqtibossiz chiqadi", config.BIO_QUOTES_FILE, type(e).__name__)
    return []


class BioUpdater:
    def __init__(self, client: TelegramClient, dry_run: bool = config.DRY_RUN) -> None:
        self.client = client
        self.dry_run = dry_run
        self.tz = ZoneInfo(config.TIMEZONE)
        self.max_len = config.BIO_MAX_LENGTH or BIO_LIMIT_DEFAULT
        self.weather: Weather | None = None
        self.next_weather_at = 0.0
        self.current_bio: str | None = None
        self.backup_ok = False
        self.quotes = load_quotes()
        self._wake = runtime.listener()
        global INSTANCE
        INSTANCE = self  # nazorat boti /bio buyrug'i uchun
        self.current_first = ""
        self.current_last = ""
        self.orig_first: str | None = None
        self.orig_last: str | None = None  # None = ism zaxirasi hali yo'q -> ismga tegilmaydi

    # ----- tayyorgarlik -----
    async def prepare(self) -> None:
        me = await self.client.get_me()
        if not config.BIO_MAX_LENGTH:
            self.max_len = BIO_LIMIT_PREMIUM if getattr(me, "premium", False) else BIO_LIMIT_DEFAULT
        full = await self.client(GetFullUserRequest(me))
        self.current_bio = full.full_user.about or ""
        self.current_first = me.first_name or ""
        self.current_last = me.last_name or ""
        await self.ensure_backup()

        state = await _safe_db(storage.get_bio_state(), "bio holati") or {}
        lw = state.get("last_weather")
        if lw:
            try:
                self.weather = Weather(**lw)
            except TypeError:
                pass
        log.info("Bio sozlamalari: shahar=%s, vaqt zonasi=%s, yangilash har %ds, ob-havo har %ds, "
                 "maks. uzunlik=%d, DRY_RUN=%s", config.CITY_NAME, config.TIMEZONE,
                 config.BIO_UPDATE_INTERVAL_SECONDS, config.WEATHER_INTERVAL_SECONDS,
                 self.max_len, self.dry_run)
        if config.BIO_UPDATE_INTERVAL_SECONDS > 60:
            log.warning("Soat har %d daqiqada yangilanadi — %d daqiqagacha ortda qolishi mumkin. "
                        "Aniq vaqt uchun .env da BIO_UPDATE_INTERVAL_SECONDS=60 qiling.",
                        config.BIO_UPDATE_INTERVAL_SECONDS // 60, config.BIO_UPDATE_INTERVAL_SECONDS // 60 - 1)

    async def ensure_backup(self) -> bool:
        """Asl bio'ni birinchi marta saqlaydi. Zaxira bo'lmasa, haqiqiy yangilash qilinmaydi."""
        local = _read_local_backup()
        state = await _safe_db(storage.get_bio_state(), "bio zaxirasi")
        remote_original = (state or {}).get("original_bio")

        if local is not None:
            original, saved_at = local["original_bio"], local.get("saved_at", time.time())
        elif remote_original is not None:
            original, saved_at = remote_original, (state or {}).get("original_saved_at", time.time())
            log.info("Asl bio zaxirasi MongoDB'dan tiklandi")
        else:
            original, saved_at = self.current_bio or "", time.time()
            log.info("Asl bio birinchi marta zaxiralanmoqda (%d belgi)", tg_len(original))
            if "🕒" in original:
                log.warning("Diqqat: joriy bio avtomatik formatga o'xshaydi — u asl bio sifatida saqlandi. "
                            "Kerak bo'lsa %s faylini tahrirlang.", config.BIO_BACKUP_FILE)

        local_ok = local is not None or _write_local_backup(original, saved_at)
        remote_ok = remote_original is not None or bool(
            await _safe_db(storage.save_original_bio_once(original, saved_at), "asl bio")
        )
        self.backup_ok = local_ok or remote_ok
        if not self.backup_ok:
            log.error("Asl bio hech qayerga saqlanmadi — xavfsizlik uchun bio o'zgartirilmaydi")
        if self.backup_ok and config.NAME_CLOCK_ENABLED:
            await self.ensure_name_backup()
        return self.backup_ok

    async def ensure_name_backup(self) -> None:
        """Asl ism/familiyani birinchi marta saqlaydi (keyin hech qachon ustidan yozmaydi)."""
        local = _read_local_backup() or {}
        if "original_last_name" in local:
            self.orig_first = local.get("original_first_name") or self.current_first
            self.orig_last = local["original_last_name"]
            return
        state = await _safe_db(storage.get_bio_state(), "ism zaxirasi") or {}
        if "original_last_name" in state:
            self.orig_first = state.get("original_first_name") or self.current_first
            self.orig_last = state["original_last_name"]
            log.info("Asl ism zaxirasi MongoDB'dan tiklandi")
        else:
            self.orig_first, self.orig_last = self.current_first, self.current_last
            log.info("Asl ism zaxiralandi: %s %s", self.orig_first, self.orig_last)
            if "🕒" in self.orig_last:
                log.warning("Diqqat: familiyada allaqachon vaqt bor — %s faylida original_last_name ni "
                            "to'g'rilang", config.BIO_BACKUP_FILE)
            await _safe_db(storage.save_bio_state(original_first_name=self.orig_first,
                                                  original_last_name=self.orig_last), "asl ism")
        if local:
            _write_local_backup(local["original_bio"], local.get("saved_at", time.time()),
                                original_first_name=self.orig_first, original_last_name=self.orig_last)

    def build_last_name(self, now: datetime) -> str | None:
        """Ism yonidagi vaqt: familiya + 🕒 HH:MM (Telegram cheklovi 64 belgi)."""
        if not config.NAME_CLOCK_ENABLED or self.orig_last is None:
            return None
        hhmm = now.strftime("%H:%M")
        text = config.NAME_TEMPLATE.format(last_name=self.orig_last, time=hhmm).strip()
        return text if tg_len(text) <= NAME_LIMIT else f"🕒 {hhmm}"

    # ----- ob-havo -----
    def _fresh_weather(self) -> Weather | None:
        if self.weather and time.time() - self.weather.fetched_at <= config.WEATHER_MAX_AGE_SECONDS:
            return self.weather
        return None

    async def refresh_weather_if_due(self) -> bool:
        """True qaytarsa — yangi ob-havo olindi (MongoDB'ga saqlash kerak)."""
        if time.time() < self.next_weather_at:
            return False
        try:
            w = await asyncio.to_thread(_fetch_weather_sync)
        except WeatherError as e:
            self.next_weather_at = time.time() + config.WEATHER_RETRY_SECONDS
            old = self._fresh_weather()
            if old:
                age = int((time.time() - old.fetched_at) // 60)
                log.warning("Ob-havo olinmadi (%s). Oxirgi ma'lumot ishlatiladi (%d daqiqa oldingi). "
                            "%ds dan keyin qayta urinaman.", e, age, config.WEATHER_RETRY_SECONDS)
            else:
                log.warning("Ob-havo olinmadi (%s). Zaxira ko'rinish: %s %s°C. %ds dan keyin qayta urinaman.",
                            e, FALLBACK_ICON, FALLBACK_TEMP, config.WEATHER_RETRY_SECONDS)
            return False
        self.weather = w
        self.next_weather_at = time.time() + config.WEATHER_INTERVAL_SECONDS
        log.info("Ob-havo (%s): %s %s, %d°C", config.CITY_NAME, w.icon, w.desc, w.temp)
        return True

    # ----- iqtibos -----
    def pick_quote(self, now: datetime, room: int) -> str | None:
        """Bugungi iqtibos: kun bo'yicha aylanadi, faqat `room` belgiga sig'adiganlari orasidan."""
        quotes = runtime.quotes(self.quotes)  # /quote buyrug'i quotes.txt dan ustun
        if not quotes or room <= 0:
            return None
        fitting = [q for q in quotes if tg_len(q) <= room]
        if not fitting:
            return None
        return fitting[now.toordinal() % len(fitting)]

    # ----- matn -----
    def build_bio(self, now: datetime) -> str:
        w = self._fresh_weather()
        icon, temp = (w.icon, str(w.temp)) if w else (FALLBACK_ICON, FALLBACK_TEMP)
        hhmm = now.strftime("%H:%M")
        base = config.BIO_TEMPLATE.format(prefix=config.BIO_PREFIX, time=hhmm, icon=icon, temp=temp)

        # Iqtibos: asosiy matndan keyin qolgan joyga sig'adiganidan bittasi
        candidates = []
        room = self.max_len - tg_len(base + QUOTE_FORMAT.format(""))
        quote = self.pick_quote(now, room)
        if quote:
            candidates.append(base + QUOTE_FORMAT.format(quote))
        acceptable = len(candidates) + 1  # iqtibosli yoki iqtibossiz asosiy matn — normal holat
        candidates += [base, f"{config.BIO_PREFIX} | 🕒 {hhmm}", config.BIO_PREFIX]

        for i, text in enumerate(candidates):
            if tg_len(text) <= self.max_len:
                if i >= acceptable:
                    log.warning("Bio %d belgidan uzun — qisqartirilgan variant ishlatildi", self.max_len)
                return text
        # Juda uzun BIO_PREFIX: qat'iy kesiladi
        out = ""
        for ch in config.BIO_PREFIX:
            if tg_len(out + ch) > self.max_len:
                break
            out += ch
        return out

    # ----- bitta yangilash -----
    async def tick(self) -> None:
        weather_changed = await self.refresh_weather_if_due()
        now = datetime.now(self.tz)
        text = self.build_bio(now)
        last_name = self.build_last_name(now)  # None -> ismga tegilmaydi
        length = f"{tg_len(text)}/{self.max_len}"
        updated = False
        name_changed = last_name is not None and last_name != self.current_last
        name_info = f" | ism: {self.orig_first} {last_name}" if last_name is not None else ""

        if text == self.current_bio and not name_changed:
            log.info("Bio va ism o'zgarmagan, so'rov yuborilmadi: %s", text)
        elif self.dry_run:
            log.info("[DRY_RUN] Telegram'da o'zgartirilmadi. Yangi bio bo'lardi: %s (%s)%s",
                     text, length, name_info)
        elif not self.backup_ok and not await self.ensure_backup():
            log.error("Asl bio zaxirasiz yangilash o'tkazib yuborildi")
            return
        else:
            # Bio va ism bitta so'rovda — Telegram'ga qo'shimcha yuk yo'q
            await self.client(UpdateProfileRequest(about=text, last_name=last_name))
            self.current_bio = text
            if last_name is not None:
                self.current_last = last_name
            updated = True
            log.info("✅ Bio yangilandi: %s (%s)%s", text, length, name_info)

        if weather_changed and self.weather:
            await _safe_db(storage.save_bio_state(last_weather=asdict(self.weather)), "ob-havo")
        if updated:
            await _safe_db(storage.save_bio_state(last_bio=text, last_bio_at=time.time()), "oxirgi bio")

    def _seconds_to_next_slot(self) -> float:
        """Yangilashni daqiqa boshiga moslaydi (masalan 300s -> 14:30, 14:35, ...)."""
        step = config.BIO_UPDATE_INTERVAL_SECONDS
        now = time.time()
        return max(1.0, (now // step + 1) * step - now + 1)

    # ----- doimiy sikl -----
    async def run(self) -> None:
        """Hech qachon istisno chiqarmaydi (main.py dagi boshqa qismlarni to'xtatmaslik uchun)."""
        while True:
            try:
                await self.prepare()
                break
            except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError,
                    errors.UserDeactivatedError, errors.UserDeactivatedBanError):
                log.error("Telegram session bekor qilingan. %s.session ni o'chirib, qayta login qiling.",
                          config.USER_SESSION)
                return
            except errors.FloodWaitError as e:
                log.warning("Telegram FloodWait: %d soniya kutaman", e.seconds)
                await asyncio.sleep(e.seconds + 5)
            except (errors.RPCError, ConnectionError, OSError) as e:
                log.warning("Tayyorgarlik xatosi (%s). %ds dan keyin qayta urinaman.",
                            type(e).__name__, config.BIO_RETRY_SECONDS)
                await asyncio.sleep(config.BIO_RETRY_SECONDS)

        paused_logged = False
        while True:
            try:
                if not runtime.get("bio_enabled"):
                    if not paused_logged:
                        log.info("Bio yangilash to'xtatilgan (/bio off). Yoqish: /bio on")
                        paused_logged = True
                    await runtime.wait_changed(self._wake, 3600)
                    continue
                paused_logged = False
                await self.tick()
                delay = self._seconds_to_next_slot()
            except errors.FloodWaitError as e:
                delay = e.seconds + 5
                log.warning("Telegram FloodWait: %d soniya kutaman. Interval kattaroq bo'lishi kerak "
                            "(BIO_UPDATE_INTERVAL_SECONDS).", e.seconds)
            except errors.AboutTooLongError:
                self.max_len = BIO_LIMIT_DEFAULT if self.max_len > BIO_LIMIT_DEFAULT else self.max_len - 5
                delay = 5
                log.warning("Telegram bio'ni uzun deb qabul qilmadi — chegara %d ga tushirildi", self.max_len)
            except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError,
                    errors.UserDeactivatedError, errors.UserDeactivatedBanError):
                log.error("Telegram session bekor qilingan — bio yangilash to'xtatildi. "
                          "%s.session ni o'chirib, qayta login qiling.", config.USER_SESSION)
                return
            except errors.RPCError as e:
                delay = config.BIO_RETRY_SECONDS
                log.warning("Telegram xatosi: %s. %ds dan keyin qayta urinaman.", type(e).__name__, delay)
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                delay = config.BIO_RETRY_SECONDS
                log.warning("Internet/ulanish xatosi: %s. %ds dan keyin qayta urinaman.",
                            type(e).__name__, delay)
            except Exception:
                delay = config.BIO_RETRY_SECONDS
                log.exception("Kutilmagan xato. %ds dan keyin qayta urinaman.", delay)
            # /bio now, /quote, /bio on kabi buyruqlar kutishni to'xtatib, darhol yangilaydi
            await runtime.wait_changed(self._wake, delay)

    # ----- tiklash -----
    async def restore(self) -> None:
        original = await get_original_bio()
        if original is None:
            log.error("Asl bio zaxirasi topilmadi (%s ham, MongoDB ham bo'sh)", config.BIO_BACKUP_FILE)
            return
        local = _read_local_backup() or {}
        state = {} if "original_last_name" in local else (
            await _safe_db(storage.get_bio_state(), "ism zaxirasi") or {})
        orig_last = local.get("original_last_name", state.get("original_last_name"))
        if self.dry_run:
            log.info("[DRY_RUN] Telegram'da o'zgartirilmadi. Asl bio qaytarilardi: %r, familiya: %r",
                     original, orig_last)
            return
        await self.client(UpdateProfileRequest(about=original, last_name=orig_last))
        log.info("✅ Asl bio qaytarildi: %r%s", original,
                 f", familiya: {orig_last!r}" if orig_last is not None else "")


def validate_settings() -> None:
    try:
        ZoneInfo(config.TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        sys.exit(f"[config] TIMEZONE noto'g'ri: {config.TIMEZONE} ('pip install tzdata' kerak bo'lishi mumkin)")
    try:
        config.BIO_TEMPLATE.format(prefix="", time="", icon="", temp="")
    except (KeyError, IndexError, ValueError):
        sys.exit("[config] BIO_TEMPLATE da faqat {prefix} {time} {icon} {temp} ishlatish mumkin")
    try:
        config.NAME_TEMPLATE.format(last_name="", time="")
    except (KeyError, IndexError, ValueError):
        sys.exit("[config] NAME_TEMPLATE da faqat {last_name} {time} ishlatish mumkin")
    if not config.BIO_PREFIX:
        sys.exit("[config] BIO_PREFIX bo'sh bo'lmasligi kerak")
    if not (-90 <= config.LATITUDE <= 90 and -180 <= config.LONGITUDE <= 180):
        sys.exit("[config] LATITUDE/LONGITUDE noto'g'ri")


async def _main(args: argparse.Namespace) -> None:
    from tg_client import create_user_client, login

    config.require("API_ID", "API_HASH")
    validate_settings()
    if config.MONGODB_URI:
        if not await storage.init(retry_forever=False):
            log.warning("MongoDB hozircha ishlamayapti — faqat mahalliy %s zaxirasi ishlatiladi",
                        config.BIO_BACKUP_FILE)
    else:
        log.warning("MONGODB_URI yo'q — faqat mahalliy %s zaxirasi ishlatiladi", config.BIO_BACKUP_FILE)

    if args.show:  # Telegram'ga ulanish shart emas
        print(f"Asl bio zaxirasi: {await get_original_bio()!r}")
        return

    client = create_user_client()
    await login(client)
    updater = BioUpdater(client)
    try:
        if args.restore:
            await updater.restore()
        elif args.once:
            await updater.prepare()
            await updater.tick()
        else:
            log.info("Bio yangilash ishga tushdi. To'xtatish: Ctrl+C")
            tasks = [updater.run()]
            if config.KEEP_ONLINE:
                from presence import keep_online
                tasks.append(keep_online(client))
            await asyncio.gather(*tasks)
    finally:
        await client.disconnect()


def cli() -> None:
    from tg_client import setup_logging

    parser = argparse.ArgumentParser(description="Telegram bio'sini avtomatik yangilash")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="bir marta yangilab chiqish")
    group.add_argument("--restore", action="store_true", help="asl bio'ni qaytarish")
    group.add_argument("--show", action="store_true", help="zaxiradagi asl bio'ni ko'rsatish")
    args = parser.parse_args()

    setup_logging()
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        log.info("To'xtatildi (Ctrl+C). Bio oxirgi holatida qoldi; asl holatga: python bio_updater.py --restore")


if __name__ == "__main__":
    cli()
