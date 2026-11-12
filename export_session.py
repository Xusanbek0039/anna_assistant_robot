"""Server (Render) uchun SESSION_STRING yaratadi.

Kompyuterda ishga tushiring: `python export_session.py`
- Agar anna.session allaqachon bor bo'lsa — kod so'ramaydi, shundan matn yasaydi.
- Bo'lmasa — telefon, Telegram kodi (va 2FA parol) shu terminalda so'raladi.
Chiqqan matnni Render -> Environment -> SESSION_STRING ga qo'ying. Hech kimga bermang!
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

import config


async def main() -> None:
    config.require("API_ID", "API_HASH")
    client = TelegramClient(config.USER_SESSION, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE or (lambda: input("Telefon (+998...): ").strip()))
    me = await client.get_me()
    print(f"\nAkkaunt: {me.first_name} (id={me.id})")
    print("\nSESSION_STRING (MAXFIY!):\n")
    print(StringSession.save(client.session))
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
