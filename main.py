import asyncio
import logging

from telethon import TelegramClient

import control_bot
import userbot
from config import API_HASH, API_ID, BOT_SESSION, BOT_TOKEN, PHONE, USER_SESSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("main")


async def main() -> None:
    user = TelegramClient(USER_SESSION, API_ID, API_HASH)
    # Birinchi ishga tushishda telefoningizga kelgan kodni (va 2FA parolni) shu yerda kiritasiz
    await user.start(phone=PHONE)
    me = await user.get_me()
    log.info("Akkauntga kirildi: %s (id=%s)", me.first_name, me.id)

    bot = TelegramClient(BOT_SESSION, API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_me = await bot.get_me()
    log.info("Nazorat boti: @%s — botga /start bosishni unutmang", bot_me.username)

    control_bot.setup(bot, user, owner_id=me.id)
    userbot.setup(user, me_id=me.id)

    try:
        await bot.send_message(me.id, "🟢 Anna ishga tushdi. /help — buyruqlar.")
    except Exception:
        log.warning("Botdan xabar yuborib bo'lmadi — Telegram'da @%s ga /start bosing", bot_me.username)

    log.info("Ishlayapti. To'xtatish: Ctrl+C")
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
