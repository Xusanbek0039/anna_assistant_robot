"""Holat nazorati: MongoDB, OpenAI, VoiceLab va Telegram'ni muntazam tekshiradi.

Biror qism ishdan chiqsa yoki qayta tiklansa — nazorat botiga BIR MARTA xabar yuboradi
(har tekshiruvda takrorlamaydi).
"""
import asyncio
import logging

import ai
import config
import storage
import voicelab

log = logging.getLogger("health")

OPENAI_EVERY = 6  # OpenAI har 6-tekshiruvda (standart: 30 daqiqada) — keraksiz so'rov qilmaslik uchun
MEMORY_WARN_MB = 400       # serverlarda (Render bepul tarifi — 512 MB) shundan oshsa ogohlantiriladi
MEMORY_ALERT_EVERY = 3600  # bir xil ogohlantirish shuncha soniyada bir marta


def memory_mb() -> float:
    """Dastur ishlatayotgan xotira (MB). Linux'da /proc dan, aniqlanmasa 0.
    Render'da dastur xotira chegarasidan oshsa, servis logga xato yozmasdan qayta ishga tushadi —
    shuning uchun buni o'zimiz kuzatamiz."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1024          # Linux: kilobayt
    except Exception:
        return 0.0


def memory_note() -> str:
    mb = memory_mb()
    return f"{mb:.0f} MB" if mb else "aniqlanmadi"


class Monitor:
    def __init__(self, user, alert) -> None:
        self.user = user
        self.alert = alert          # async alert(text)
        self.state: dict[str, bool] = {}
        self.round = 0
        self.memory = 0.0           # oxirgi o'lchangan xotira (MB)
        self._mem_alert = 0.0

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

    async def _check_voicelab(self) -> str | None:
        try:
            return await asyncio.wait_for(voicelab.check_key(), timeout=30)
        except Exception as e:
            return type(e).__name__

    async def _check_telegram(self) -> str | None:
        return None if self.user.is_connected() else "ulanish uzilgan"

    async def _check_memory(self) -> None:
        """Xotira chegaraga yaqinlashsa ogohlantiradi: server dasturni jim o'ldirib qayta ishga tushiradi."""
        import time
        self.memory = memory_mb()
        if self.memory < MEMORY_WARN_MB or time.time() - self._mem_alert < MEMORY_ALERT_EVERY:
            return
        self._mem_alert = time.time()
        log.warning("Xotira sarfi yuqori: %.0f MB", self.memory)
        await self._safe_alert(
            f"🧠 Xotira sarfi yuqori: {self.memory:.0f} MB.\n"
            "Server chegarasi (masalan Render bepul tarifi — 512 MB) oshsa, dastur logga xato yozmasdan "
            "qayta ishga tushadi. Yengillashtirish: .env da IG_SYNC_LIMIT ni kamaytiring (masalan 300), "
            "ARCHIVE_SYNC_LIMIT ni kamaytiring yoki INSTAGRAM_ENABLED=false qiling.")

    async def check_once(self) -> dict[str, str | None]:
        self.round += 1
        await self._check_memory()
        results = {"MongoDB": await self._check_mongo(), "Telegram": await self._check_telegram()}
        if self.round == 1 or self.round % OPENAI_EVERY == 0:
            results["OpenAI"] = await self._check_openai()
            if voicelab.enabled():
                results["VoiceLab"] = await self._check_voicelab()
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
