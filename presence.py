"""Dastur ishlab turganda akkauntni Telegram'da "online" ko'rsatib turish."""
import asyncio
import logging
import time

from telethon import TelegramClient, errors, events
from telethon.tl.functions.account import GetPrivacyRequest, UpdateStatusRequest
from telethon.tl.types import (InputPrivacyKeyStatusTimestamp, PrivacyValueAllowAll,
                               PrivacyValueAllowContacts, UserStatusOffline)

import config
import runtime

log = logging.getLogger("presence")

REPING_MIN_GAP = 10  # telefon "offline" yuborsa, ko'pi bilan har 10 soniyada qayta "online"


async def check_privacy(client: TelegramClient) -> None:
    """Boshqalar "online" holatini ko'ra oladimi — maxfiylik sozlamasini tekshiradi."""
    try:
        res = await client(GetPrivacyRequest(InputPrivacyKeyStatusTimestamp()))
    except Exception as e:
        log.warning("Maxfiylik sozlamasini tekshirib bo'lmadi (%s)", type(e).__name__)
        return
    rules = res.rules
    if any(isinstance(r, PrivacyValueAllowAll) for r in rules):
        log.info("Maxfiylik: 'So'nggi faollik' — Hamma. Online holat hammaga ko'rinadi")
    elif any(isinstance(r, PrivacyValueAllowContacts) for r in rules):
        log.warning("Maxfiylik: 'So'nggi faollik' — faqat KONTAKTLAR. Boshqalar 'online' ni ko'rmaydi. "
                    "Telegram -> Sozlamalar -> Maxfiylik -> So'nggi faollik va online -> Hamma")
    else:
        log.warning("Maxfiylik: 'So'nggi faollik' — HECH KIM. Hech kim 'online' ni ko'rmaydi "
                    "('yaqinda bo'lgan' ko'rinadi). Telegram -> Sozlamalar -> Maxfiylik -> "
                    "So'nggi faollik va online -> Hamma")


async def keep_online(client: TelegramClient) -> None:
    """Har ONLINE_INTERVAL_SECONDS da "online" signalini yuboradi.

    Telefondagi Telegram yopilib "offline" yuborsa — darhol qayta "online" qiladi.
    To'xtatilganda (Ctrl+C) akkauntni "offline" holatiga qaytaradi.
    Hech qachon istisno chiqarmaydi — boshqa qismlarni to'xtatmaslik uchun.
    """
    interval = config.ONLINE_INTERVAL_SECONDS
    me = await client.get_me()
    # Bitta signal: telefon "offline" yuborganda YOKI /online buyrug'i berilganda uyg'onadi
    wake = runtime.listener()
    last_ping = 0.0
    phone_offline = False

    async def on_self_status(event):
        # Boshqa qurilma (telefon) bizni offline qildi -> kutmasdan qayta online
        nonlocal phone_offline
        if event.user_id == me.id and isinstance(event.status, UserStatusOffline):
            phone_offline = True
            wake.set()

    client.add_event_handler(on_self_status, events.UserUpdate)
    await check_privacy(client)
    log.info("Akkaunt online ko'rinib turadi (har %ds yangilanadi)", interval)

    was_ok, first, paused = True, True, False
    try:
        while True:
            if not runtime.get("online"):
                if not paused:
                    paused = True
                    log.info("Online holat o'chirildi (/online off)")
                    try:
                        await client(UpdateStatusRequest(offline=True))
                    except Exception:
                        pass
                wake.clear()
                await runtime.wait_changed(wake, 3600)
                continue
            if paused:
                paused = False
                log.info("Online holat yoqildi (/online on)")
            try:
                await client(UpdateStatusRequest(offline=False))
                last_ping = time.time()
                if first:
                    log.info("✅ Online signal yuborildi")
                    first = False
                elif not was_ok:
                    log.info("Online holat tiklandi")
                was_ok = True
                delay = interval
            except errors.FloodWaitError as e:
                log.warning("Online holat: FloodWait, %d soniya kutaman", e.seconds)
                delay = e.seconds + 5
            except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError):
                log.error("Session bekor qilingan — online holat to'xtatildi")
                return
            except (errors.RPCError, ConnectionError, OSError) as e:
                if was_ok:  # bir xil xatoni har daqiqada takrorlamaslik uchun
                    log.warning("Online holat yuborilmadi (%s), qayta urinaman", type(e).__name__)
                was_ok = False
                delay = min(interval, 30)

            wake.clear()
            if await runtime.wait_changed(wake, delay) and phone_offline:
                phone_offline = False
                # Telefon offline qildi — juda tez-tez emas, kamida REPING_MIN_GAP oraliq bilan
                gap = REPING_MIN_GAP - (time.time() - last_ping)
                if gap > 0:
                    await asyncio.sleep(gap)
                log.info("Boshqa qurilma 'offline' qildi — qayta online")
    except asyncio.CancelledError:
        client.remove_event_handler(on_self_status)
        try:
            await asyncio.wait_for(client(UpdateStatusRequest(offline=True)), timeout=5)
            log.info("Akkaunt offline holatiga qaytarildi")
        except Exception:
            pass
        raise
