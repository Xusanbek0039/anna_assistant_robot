import asyncio
import logging

from telethon import TelegramClient

import ai
import config
import control_bot
import storage
import userbot
from bio_updater import BioUpdater, validate_settings
from presence import keep_online
from tg_client import create_user_client, login, setup_logging

log = logging.getLogger("main")


async def main() -> None:
    config.require("API_ID", "API_HASH", "BOT_TOKEN", "OPENAI_API_KEY", "MONGODB_URI")
    if config.BIO_ENABLED:
        validate_settings()
    await storage.init()

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
    userbot.setup(user, me_id=me.id)

    try:
        await bot.send_message(me.id, "🟢 Anna ishga tushdi. /help — buyruqlar.")
    except Exception:
        log.warning("Botdan xabar yuborib bo'lmadi — Telegram'da @%s ga /start bosing", bot_me.username)

    tasks = [user.run_until_disconnected(), bot.run_until_disconnected()]
    if config.BIO_ENABLED:
        tasks.append(BioUpdater(user).run())
    else:
        log.info("Avtomatik bio o'chirilgan (BIO_ENABLED=false)")
    if config.KEEP_ONLINE:
        tasks.append(keep_online(user))

    log.info("Ishlayapti. To'xtatish: Ctrl+C")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("To'xtatildi (Ctrl+C)")
