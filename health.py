"""Holat nazorati: MongoDB, OpenAI va Telegram'ni muntazam tekshiradi.

Biror qism ishdan chiqsa yoki qayta tiklansa — nazorat botiga BIR MARTA xabar yuboradi
(har tekshiruvda takrorlamaydi).
"""
import asyncio
import logging

import ai
import config
import storage

log = logging.getLogger("health")

OPENAI_EVERY = 6  # OpenAI har 6-tekshiruvda (standart: 30 daqiqada) — keraksiz so'rov qilmaslik uchun


class Monitor:
    def __init__(self, user, alert) -> None:
        self.user = user
        self.alert = alert          # async alert(text)
        self.state: dict[str, bool] = {}
        self.round = 0

    async def _check_mongo(self) -> str | None:
        try:
            await asyncio.wait_for(storage.ping(), timeout=15)
            return None
        except Exception as e:
            return type(e).__name__

    async def _check_openai(self) -> str | None:
        try:
            return await asyncio.wait_for(ai.check_key(), timeout=30)
        except Exception as e:
            return type(e).__name__

    async def _check_telegram(self) -> str | None:
        return None if self.user.is_connected() else "ulanish uzilgan"

    async def check_once(self) -> dict[str, str | None]:
        self.round += 1
        results = {"MongoDB": await self._check_mongo(), "Telegram": await self._check_telegram()}
        if self.round == 1 or self.round % OPENAI_EVERY == 0:
            results["OpenAI"] = await self._check_openai()
        for name, problem in results.items():
            ok = problem is None
            before = self.state.get(name)
            self.state[name] = ok
            if before is None and ok:
                continue  # birinchi tekshiruv va hammasi joyida — xabar shart emas
            if before is not False and not ok:
                log.error("%s ishlamayapti: %s", name, problem)
                await self._safe_alert(f"❌ {name} ishlamayapti: {problem}")
            elif before is False and ok:
                log.info("%s tiklandi", name)
                await self._safe_alert(f"✅ {name} qayta ishlayapti")
        return results

    async def _safe_alert(self, text: str) -> None:
        try:
            await self.alert(text)
        except Exception:
            log.warning("Ogohlantirish yuborilmadi: %s", text)

    async def run(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                log.exception("Holat tekshiruvida xato")
            await asyncio.sleep(config.HEALTH_CHECK_SECONDS)

    def summary(self) -> str:
        if not self.state:
            return "hali tekshirilmadi"
        return ", ".join(f"{'✅' if ok else '❌'} {name}" for name, ok in self.state.items())
