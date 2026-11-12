"""Dastur ishlab turganda akkauntni Telegram'da "online" ko'rsatib turish."""
import asyncio
import logging

from telethon import TelegramClient, errors
from telethon.tl.functions.account import UpdateStatusRequest

import config

log = logging.getLogger("presence")


async def keep_online(client: TelegramClient) -> None:
    """Har ONLINE_INTERVAL_SECONDS da "online" signalini yuboradi.

    To'xtatilganda (Ctrl+C) akkauntni "offline" holatiga qaytaradi.
    Hech qachon istisno chiqarmaydi — boshqa qismlarni to'xtatmaslik uchun.
    """
    interval = config.ONLINE_INTERVAL_SECONDS
    log.info("Akkaunt online ko'rinib turadi (har %ds yangilanadi)", interval)
    was_ok = True
    try:
        while True:
            try:
                await client(UpdateStatusRequest(offline=False))
                if not was_ok:
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
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(client(UpdateStatusRequest(offline=True)), timeout=5)
            log.info("Akkaunt offline holatiga qaytarildi")
        except Exception:
            pass
        raise
