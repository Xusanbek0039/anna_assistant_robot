"""Bitta nusxa kafolati: Anna bir vaqtda faqat bitta joyda ishlaydi.

Dastur ikki joyda (masalan, serverda va kompyuterda, yoki qayta joylashda eski va yangi nusxa) birga ishlasa,
bot har bir xabarga ikki marta javob beradi. Shuning uchun MongoDB'da umumiy "qulf" saqlanadi:
- ishlayotgan nusxa har HEARTBEAT soniyada qulfni yangilab turadi;
- yangi nusxa qulf band bo'lsa kutib turadi va eskisi to'xtagach (qulf TTL soniya yangilanmasa) ishni oladi.
"""
import asyncio
import logging
import os
import socket
import uuid

import storage

log = logging.getLogger("instance")

TTL = 45          # qulf shuncha soniya yangilanmasa — egasi o'lgan deb hisoblanadi
HEARTBEAT = 15

INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


async def wait_until_only() -> None:
    """Qulfni oladi. Boshqa nusxa ishlab turgan bo'lsa — u to'xtaguncha kutadi."""
    waited = 0
    while not await storage.acquire_lock(INSTANCE_ID, TTL):
        if waited % 60 == 0:
            log.warning("Anna'ning boshqa nusxasi ishlab turibdi — u to'xtaguncha kutaman "
                        "(ikki nusxa birga ishlasa, bot har xabarni ikki marta yuboradi)")
        await asyncio.sleep(HEARTBEAT)
        waited += HEARTBEAT
    log.info("Yagona nusxa sifatida ishga tushyapman (%s)", INSTANCE_ID)


async def heartbeat(on_lost) -> None:
    """Qulfni yangilab turadi. Qulfni boshqa nusxa olib qo'ygan bo'lsa, on_lost() chaqiriladi va tugaydi."""
    while True:
        await asyncio.sleep(HEARTBEAT)
        try:
            ok = await storage.acquire_lock(INSTANCE_ID, TTL)
        except Exception as e:
            log.warning("Nusxa qulfi yangilanmadi (%s) — keyinroq qayta urinaman", type(e).__name__)
            continue
        if not ok:
            log.error("Boshqa nusxa ishni o'z qo'liga oldi — bu nusxa to'xtaydi")
            await on_lost()
            return


async def release() -> None:
    try:
        await asyncio.wait_for(storage.release_lock(INSTANCE_ID), timeout=5)
    except Exception:
        pass  # qulf TTL dan keyin o'zi bo'shaydi
