import asyncio
import logging
import sys

from telethon import TelegramClient

import ai
import archive
import backup
import config
import control_bot
import deleted_accounts
import finance
import gdrive
import health
import instagram
import instance
import profiles
import promises
import report
import runtime
import storage
import userbot
import viewers
import voicelab
import watchers
import weekly
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


async def _archive_sync(user) -> None:
    """Mavjud chatlardagi yozishmalarni arxivga nusxalash (bir martalik fon vazifasi)."""
    await asyncio.sleep(20)  # avval kotiba va bot to'liq ishga tushib olsin
    try:
        chats, added = await archive.sync_existing(user, config.ARCHIVE_SYNC_LIMIT)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("Mavjud chatlar arxivga nusxalanmadi")
        await control_bot.alert(f"⚠️ Mavjud chatlarni arxivga nusxalab bo'lmadi: {type(e).__name__}")
        return
    if added:
        await control_bot.alert(f"🗂 Arxiv: {chats} ta chatdan {added} ta xabar nusxalandi. "
                                "Endi ular o'chirilsa ham HTML qilib olasiz.")
    try:  # akkaunti o'chirilgan odamlar bilan yozishmalar — to'liq arxivga va taxminiy ism bilan
        found, gone_added, _ = await deleted_accounts.refresh(user)
        if gone_added:
            await control_bot.alert(f"👻 {found} ta o'chirilgan akkaunt bilan yozishmadan {gone_added} ta xabar "
                                    "arxivga olindi. Ro'yxat: /deleted")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("O'chirilgan akkauntlar arxivlanmadi")


async def main() -> None:
    # Ishga tushish bosqichlari logga yoziladi: dastur biror joyda kutib qolsa (MongoDB, boshqa nusxa,
    # Telegram), oxirgi qator qayerda to'xtaganini ko'rsatadi
    log.info("1/6 Sozlamalar tekshirilyapti")
    config.require("API_ID", "API_HASH", "BOT_TOKEN", "OPENAI_API_KEY", "MONGODB_URI")
    if config.BIO_ENABLED:
        validate_settings()
    log.info("2/6 MongoDB'ga ulanyapti (Atlas'da Network Access -> 0.0.0.0/0 ruxsati borligini tekshiring)")
    await storage.init()
    if config.SINGLE_INSTANCE:
        log.info("3/6 Nusxa qulfi olinyapti")
        await instance.wait_until_only()  # boshqa nusxa ishlayotgan bo'lsa, Telegram'ga ulanmasdan kutadi
    await runtime.load()

    problem = await ai.check_key()
    if problem:
        log.error("AI: %s. Kotiba hozircha faqat shablon matn bilan javob beradi.", problem)
    else:
        log.info("OpenAI kaliti ishlayapti (model: %s)", config.OPENAI_MODEL)
    log.info("Ovoz sozlamalari: %s", voicelab.describe())
    if voicelab.audio_tools_problem():
        log.error(voicelab.audio_tools_problem())

    log.info("4/6 Shaxsiy akkauntga (userbot) ulanyapti")
    user = create_user_client()
    # Birinchi ishga tushishda telefon raqam, Telegram kodi (va 2FA parol) terminalda so'raladi
    await login(user)
    me = await user.get_me()
    log.info("Akkaunt: %s (id %s)", me.first_name, me.id)

    log.info("5/6 Nazorat botiga ulanyapti (BOT_TOKEN)")
    bot = TelegramClient(config.BOT_SESSION, config.API_ID, config.API_HASH)
    await bot.start(bot_token=config.BOT_TOKEN)
    bot_me = await bot.get_me()
    log.info("Nazorat boti: @%s — botga /start bosishni unutmang", bot_me.username)

    control_bot.setup(bot, user, owner_id=me.id)
    control_bot.ctx.bot_username = bot_me.username  # 👀 profildagi havola: t.me/<bot>?start=profil
    await control_bot.set_commands(bot, me.id)  # "/" bosilganda chiqadigan menyu
    userbot.setup(user, me_id=me.id)
    # o'chirilgan / tahrirlangan yozishmalar
    archive.setup(user, me.id, control_bot.deleted_alert, control_bot.edited_alert)
    profiles.setup(user, control_bot.profile_alert)  # ism, username, bio, rasm o'zgarishlari
    if config.WATCH_ENABLED:  # 🔔 eslatmalar: raqam Telegram'da paydo bo'lsa / odam online bo'lsa
        watchers.setup(user, control_bot.watch_alert)
    # 📸 Instagram: bir martalik media, avtojavob, arxiv; 👀 storis ko'ruvchilari va havola orqali kelganlar
    instagram.setup(control_bot.alert, control_bot.send_ig_media, control_bot.ig_notify)
    viewers.setup(control_bot.watch_alert)
    monitor = health.Monitor(user, control_bot.alert)
    control_bot.ctx.monitor = monitor

    try:
        await bot.send_message(me.id, "🟢 Anna ishga tushdi. Pastdagi tugmalardan foydalaning.",
                               buttons=control_bot.main_keyboard())
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
    if config.ARCHIVE_SYNC_LIMIT > 0:
        background.append(_archive_sync(user))
    if gdrive.enabled():
        background.append(supervise("Google Drive arxivi", archive.drive_loop(control_bot.alert)))
        background.append(supervise("Google Drive media", archive.drive_media_loop(user, control_bot.alert)))
        background.append(supervise("Bazani zaxiralash", backup.run(control_bot.alert)))
    else:
        log.info("Google Drive arxivi o'chiq - .env da yo'q: %s", ", ".join(gdrive.missing()))
    background += [
        supervise("Profil kuzatuvi", profiles.run(user)),
        *([supervise("Eslatmalar", watchers.run(user))] if config.WATCH_ENABLED else []),
        *([supervise("Instagram", instagram.run()),
           supervise("Instagram arxivi", instagram.backfill_loop(control_bot.alert)),
           *([supervise("Instagram Drive", instagram.drive_loop(control_bot.alert))] if gdrive.enabled() else []),
           ] if config.INSTAGRAM_ENABLED else []),
        *([supervise("Storis ko'ruvchilari", viewers.run(user))] if config.VIEWERS_ENABLED else []),
        supervise("Va'dalar tekshiruvi", promises.run_scan(control_bot.send_promises)),
        supervise("Va'dalar eslatmasi", promises.run_remind(control_bot.send_promises)),
        supervise("Haftalik chat hisoboti", weekly.run(control_bot.send_weekly)),
    ]
    if config.REPORT_ENABLED:
        background.append(supervise("Kunlik hisobot", report.run(control_bot.send_html)))
    if config.FINANCE_ENABLED:
        background.append(supervise("Moliya hisoboti", finance.run_reports(control_bot.send_html)))

    log.info("6/6 Hammasi ishga tushdi: %d fon vazifasi, xotira %s. To'xtatish: Ctrl+C",
             len(background), health.memory_note())
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
