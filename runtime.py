"""Nazorat botidan o'zgartiriladigan sozlamalar (/bio, /quote, /online).

.env dagi qiymatlar — standart. Botdan o'zgartirilgani MongoDB'da saqlanadi va
dastur qayta ishga tushganda ham saqlanib qoladi.
"""
import asyncio
import logging

import config
import storage

log = logging.getLogger("runtime")

DEFAULTS = {
    "bio_enabled": True,
    "quote_mode": "auto",   # auto — quotes.txt dan har kuni; fixed — quote_text; off — iqtibossiz
    "quote_text": "",
    "online": config.KEEP_ONLINE,
}

_values: dict = dict(DEFAULTS)
# Har bir kutuvchi sikl (bio, online) o'z Event'iga ega — biri tozalasa, boshqasi signalni yo'qotmaydi
_listeners: list[asyncio.Event] = []


async def load() -> None:
    try:
        saved = await storage.get_runtime()
    except Exception as e:
        log.warning("Bot sozlamalari o'qilmadi (%s) — .env qiymatlari ishlatiladi", type(e).__name__)
        return
    _values.update({k: v for k, v in saved.items() if k in DEFAULTS})


def get(key: str):
    return _values.get(key, DEFAULTS.get(key))


async def update(**values) -> None:
    unknown = [k for k in values if k not in DEFAULTS]
    if unknown:
        raise KeyError(", ".join(sorted(unknown)))
    _values.update(values)
    for ev in _listeners:
        ev.set()
    try:
        await storage.set_runtime(**values)
    except Exception as e:
        log.warning("Sozlama saqlanmadi (%s) — faqat shu ishga tushirish davomida amal qiladi",
                    type(e).__name__)


def quotes(file_quotes: list[str]) -> list[str]:
    """Hozirgi iqtiboslar ro'yxati: bot sozlamasi .env/quotes.txt dan ustun."""
    mode = get("quote_mode")
    if mode == "off":
        return []
    if mode == "fixed" and get("quote_text"):
        return [get("quote_text")]
    return file_quotes


def listener() -> asyncio.Event:
    """Sikl uchun alohida "sozlama o'zgardi" signali."""
    ev = asyncio.Event()
    _listeners.append(ev)
    return ev


async def wait_changed(ev: asyncio.Event, timeout: float) -> bool:
    """`timeout` soniya kutadi; sozlama o'zgarsa — oldinroq qaytadi (True)."""
    try:
        await asyncio.wait_for(ev.wait(), timeout=max(0.0, timeout))
        ev.clear()
        return True
    except asyncio.TimeoutError:
        return False
