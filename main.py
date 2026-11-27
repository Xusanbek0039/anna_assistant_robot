import asyncio
import logging
import sys


import ai
import archive
import backup
import channels
import config
import control_bot
import deleted_accounts
import finance
import gdrive
import groups
import health
import instagram
import instance
import report
import runtime
import storage
import tg_login
import userbot
import voicelab
import watchers
import weekly
from bio_updater import BioUpdater, validate_settings
from presence import keep_online
from tg_client import login, setup_logging, start_bot

log = logging.getLogger("main")


async def supervise(name: str, coro) -> None:
    """Fon vazifasi: kutilmagan xato bilan to'xtasa — logga yozadi va egasini ogohlantiradi,
    lekin qolgan qismlarni (kotiba, bot) to'xtatmaydi."""
    log.info("▶️ %s", name)   # bot jim qolsa, logdan qaysi qismlar ishga tushganini ko'rish uchun
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


async def supervise_once(name: str, coro) -> None:
    """Bir martalik fon vazifasi: tugashi normal; xato bo'lsa — logga, qolganlar ishlashda davom etadi."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("%s bo'lmadi", name)
        await control_bot.alert(f"⚠️ {name} bo'lmadi: {type(e).__name__}. Asosiy akkaunt ishlayapti.")


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
    log.info("1/6 Sozlamalar tekshirilyapti (xotira %s)", health.memory_note())
    config.require("API_ID", "API_HASH", "BOT_TOKEN", "OPENAI_API_KEY", "MONGODB_URI")
    if config.BIO_ENABLED:
        validate_settings()
    log.info("2/6 MongoDB'ga ulanyapti — Atlas'da Network Access -> 0.0.0.0/0 ruxsati kerak (xotira %s)",
             health.memory_note())
    await storage.init()
    if config.SINGLE_INSTANCE:
        log.info("3/6 Nusxa qulfi olinyapti (xotira %s)", health.memory_note())
        await instance.wait_until_only()  # boshqa nusxa ishlayotgan bo'lsa, Telegram'ga ulanmasdan kutadi
    else:
        log.warning("3/6 SINGLE_INSTANCE=false — nusxa qulfi O'CHIQ. Deploy paytida eski va yangi nusxa (yoki "
                    "kompyuter va server) bir vaqtda bitta Telegram sessiyasini ishlatsa, Telegram uni bloklaydi va "
                    "bot qotib qoladi. Tavsiya: SINGLE_INSTANCE=true")
    await runtime.load()
    await channels.load()       # 📢 arxivlanadigan kanallar (/kanallar)
    await gdrive.load_token()   # botdan (/drive) qayta ulangan bo'lsa — yangi token bazada

    problem = await ai.check_key()
    if problem:
        log.error("AI: %s. Kotiba hozircha faqat shablon matn bilan javob beradi.", problem)
    else:
        log.info("OpenAI kaliti ishlayapti (model: %s)", config.OPENAI_MODEL)
    log.info("Ovoz sozlamalari: %s", voicelab.describe())
    if voicelab.audio_tools_problem():
        log.error(voicelab.audio_tools_problem())

    log.info("4/6 Shaxsiy akkauntga (userbot) ulanyapti (xotira %s)", health.memory_note())
    # Sessiya: SESSION_STRING -> anna.session -> bazadagi (bot orqali kirilgan) sessiya
    user = await tg_login.open_client()
    try:
        # Birinchi ishga tushishda telefon raqam, Telegram kodi (va 2FA parol) terminalda so'raladi;
        # terminal yo'q bo'lsa (server) — nazorat botida so'raladi
        await login(user, via_bot=True)
        bot = None
    except tg_login.NeedsBotLogin:
        log.info("5/6 Nazorat botiga ulanyapti — Telegram akkaunt bot orqali ulanadi (xotira %s)",
                 health.memory_note())
        bot, bot_me = await start_bot()
        await tg_login.via_bot(bot, user)
        await login(user)
    me = await user.get_me()
    log.info("Akkaunt: %s (id %s)", me.first_name, me.id)
    await tg_login.remember_owner(me.id)

    if bot is None:
        log.info("5/6 Nazorat botiga ulanyapti (BOT_TOKEN) (xotira %s)", health.memory_note())

        async def _tell_owner(text: str) -> None:   # bot hali yo'q — «Saqlangan xabarlar»ga yoziladi
            await user.send_message("me", text)

        bot, bot_me = await start_bot(notify=_tell_owner)
    log.info("Nazorat boti: @%s — botga /start bosishni unutmang", bot_me.username)

    control_bot.setup(bot, user, owner_id=me.id)
    await control_bot.set_commands(bot, me.id)  # "/" bosilganda chiqadigan menyu
    userbot.setup(user, me_id=me.id)
    # o'chirilgan / tahrirlangan yozishmalar
    archive.setup(user, me.id, control_bot.deleted_alert, control_bot.edited_alert)
    if config.WATCH_ENABLED:  # 🔔 eslatmalar: raqam Telegram'da paydo bo'lsa / odam online bo'lsa
        watchers.setup(user, control_bot.watch_alert)
    # 📸 Instagram: bir martalik media, avtojavob, arxiv
    instagram.setup(control_bot.alert, control_bot.send_ig_media, control_bot.ig_notify)
    # 👥 tanlangan guruhlarga kelgan xabarlar botga nusxalanadi (/guruhlar)
    groups.setup(user, control_bot.group_clone_text, control_bot.send_media)
    gdrive.set_auth_hook(control_bot.drive_auth_alert)  # token o'lsa — bitta xabar + qayta ulash tugmasi
    monitor = health.Monitor(user, control_bot.alert)
    control_bot.ctx.monitor = monitor

    try:
        await bot.send_message(me.id, "🟢 Anna ishga tushdi. Pastdagi tugmalardan foydalaning.",
                               buttons=control_bot.main_keyboard())
    except Exception:
        log.warning("Botdan xabar yuborib bo'lmadi — Telegram'da @%s ga /start bosing", bot_me.username)

    # Online holat doim ishga tushadi: /online on|off uni botdan boshqaradi (standart — KEEP_ONLINE)
    background = [
        # 📱 2-akkaunt fonda ulanadi — u qotib qolsa ham asosiy akkaunt va bot ishlayveradi
        supervise_once("2-akkauntni ulash", control_bot.restore_second()),
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
        *([supervise("Eslatmalar", watchers.run(user))] if config.WATCH_ENABLED else []),
        *([supervise("Instagram", instagram.run()),
           supervise("Instagram arxivi", instagram.backfill_loop(control_bot.alert)),
           supervise("Instagram media", instagram.media_loop()),
           *([supervise("Instagram Drive", instagram.drive_loop(control_bot.alert))] if gdrive.enabled() else []),
           ] if config.INSTAGRAM_ENABLED else []),
        supervise("Guruh xabarlarini klonlash", groups.run()),
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
