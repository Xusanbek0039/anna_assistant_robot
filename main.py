import asyncio
import logging
import sys

from telethon import TelegramClient

import ai
import config
import control_bot
import finance
import health
import instance
import report
import runtime
import storage
import userbot
from bio_updater import BioUpdater, validate_settings
from presence import keep_online
from tg_client import create_user_client, login, setup_logging

log = logging.getLogger("main")


async def supervise(name: str, coro) -> None:
    """Fon vazifasi: kutilmagan xato bilan to'xtasa — logga yozadi va egasini ogohlantiradi,
    lekin qolgan qismlarni (kotiba, bot) to'xtatmaydi."""
    try:
        await coro
        log.warning("%s vazifasi tugadi", name)
        await control_bot.alert(f"⚠️ {name} to'xtadi. Logni tekshiring.")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("%s vazifasi xato bilan to'xtadi", name)
        await control_bot.alert(f"💥 {name} xato bilan to'xtadi: {type(e).__name__}. "
                                "Qolgan qismlar ishlashda davom etyapti.")


async def main() -> None:
    config.require("API_ID", "API_HASH", "BOT_TOKEN", "OPENAI_API_KEY", "MONGODB_URI")
    if config.BIO_ENABLED:
        validate_settings()
    await storage.init()
    if config.SINGLE_INSTANCE:
        await instance.wait_until_only()  # boshqa nusxa ishlayotgan bo'lsa, Telegram'ga ulanmasdan kutadi
    await runtime.load()

    problem = await ai.check_key()
    if problem:
        log.error("AI: %s. Kotiba hozircha faqat shablon matn bilan javob beradi.", problem)
    else:
        log.info("OpenAI kaliti ishlayapti (model: %s)", config.OPENAI_MODEL)

    user = create_user_client()
    # Birinchi ishga tushishda telefon raqam, Telegram kodi (va 2FA parol) terminalda so'raladi
    await login(user)
    me = await user.get_me()

    bot = TelegramClient(config.BOT_SESSION, config.API_ID, config.API_HASH)
    await bot.start(bot_token=config.BOT_TOKEN)
    bot_me = await bot.get_me()
    log.info("Nazorat boti: @%s — botga /start bosishni unutmang", bot_me.username)

    control_bot.setup(bot, user, owner_id=me.id)
    await control_bot.set_commands(bot, me.id)  # "/" bosilganda chiqadigan menyu
    userbot.setup(user, me_id=me.id)
    monitor = health.Monitor(user, control_bot.alert)
    control_bot.ctx.monitor = monitor

    try:
        await bot.send_message(me.id, "🟢 Anna ishga tushdi. /help — buyruqlar.")
    except Exception:
        log.warning("Botdan xabar yuborib bo'lmadi — Telegram'da @%s ga /start bosing", bot_me.username)

    # Online holat doim ishga tushadi: /online on|off uni botdan boshqaradi (standart — KEEP_ONLINE)
    background = [
        supervise("Online holat", keep_online(user)),
        supervise("Holat nazorati", monitor.run()),
    ]
    if config.SINGLE_INSTANCE:
        async def _lost() -> None:
            await control_bot.alert("⚠️ Anna'ning boshqa nusxasi ishga tushdi — bu nusxa to'xtadi "
                                    "(ikkalasi birga ishlasa, xabarlar ikki martadan kelardi).")
            await user.disconnect()
            await bot.disconnect()
        background.append(supervise("Nusxa nazorati", instance.heartbeat(_lost)))
    if config.BIO_ENABLED:
        background.append(supervise("Bio yangilash", BioUpdater(user).run()))
    else:
        log.info("Avtomatik bio o'chirilgan (BIO_ENABLED=false)")
    if config.REPORT_ENABLED:
        background.append(supervise("Kunlik hisobot", report.run(control_bot.send_html)))
    if config.FINANCE_ENABLED:
        background.append(supervise("Moliya hisoboti", finance.run_reports(control_bot.send_html)))

    log.info("Ishlayapti. To'xtatish: Ctrl+C")
    try:
        # Telegram klientlaridan biri uzilsa — dastur tugaydi (run.bat qayta ishga tushiradi)
        await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected(), *background)
    except asyncio.CancelledError:
        # Ctrl+C — egasini xabardor qilib to'xtaymiz
        try:
            await asyncio.wait_for(bot.send_message(me.id, "🔴 Anna to'xtatildi."), timeout=5)
        except Exception:
            pass
        raise
    except Exception as e:
        log.exception("Dastur xato bilan to'xtadi")
        try:
            await asyncio.wait_for(
                bot.send_message(me.id, f"💥 Anna xato bilan to'xtadi: {type(e).__name__}. "
                                        "Logni tekshiring (run.bat bo'lsa, o'zi qayta ishga tushadi)."),
                timeout=5)
        except Exception:
            pass
        raise
    finally:
        if config.SINGLE_INSTANCE:
            await instance.release()  # keyingi ishga tushish kutib qolmasligi uchun


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("To'xtatildi (Ctrl+C)")
    except SystemExit:
        raise
    except Exception:
        sys.exit(1)
