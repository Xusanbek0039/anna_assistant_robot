"""Nazorat boti: egasiga xulosalar yuboradi va buyruqlarni qabul qiladi."""
import asyncio
import html
import io
import itertools
import logging
import re
import time

from telethon import Button, TelegramClient, errors, events, utils
from telethon.tl import types

import ai
import archive
import availability
import backup
import bio_updater
import config
import deleted_accounts
import finance
import finder
import gdrive
import groups
import health
import instagram
import progress
import promises
import report
import runtime
import saver
import storage
import tg_login
import voicelab
import watchers
import weekly
from config import OWNER_TAKEOVER_MINUTES

log = logging.getLogger("bot")

HELP = (
    "<b>Anna — avtojavob kotiba</b>\n\n"
    "/auto — aqlli rejim: faqat band bo'lganingizda javob beradi\n"
    "/on — doim javob beradi\n"
    "/off — avtojavobni o'chirish\n"
    "/status — holat\n"
    "/tag &lt;id yoki @username&gt; &lt;oila|mijoz|dost|clear&gt; — kontakt turi\n"
    "/tags — turlar ro'yxati\n"
    "/unmute &lt;id yoki @username&gt; — shu chatda kotibani qayta yoqish (o'zingiz yozganingizdan keyin "
    "u o'sha chatda vaqtincha jim turadi)\n\n"
    "<b>Hisobot va nazorat</b>\n"
    "/report — bugungi hisobot (har kuni kechqurun avtomatik ham keladi)\n"
    "/usage — bugungi AI sarfi va limitlar\n"
    "/health — MongoDB, OpenAI, Telegram holati\n\n"
    "<b>Saqlash</b>\n"
    "/save &lt;havola&gt; — kanal/guruh xabaridagi mediani Saqlangan xabarlaringizga joylaydi. "
    "Havolaning o'zini yuborsangiz ham bo'ladi (akkauntingiz o'sha kanal a'zosi bo'lishi kerak)\n\n"
    "<b>🔎 Chatlardan qidirish</b>\n"
    "Botga oddiy yozing yoki ayting — istalgan narsani topadi: «Ali menga tashlagan usernameni top», "
    "«Vali yuborgan manzilni topib ber», «Sardor kecha tashlagan pdf faylni qidir», "
    "«Ali qachon uchrashamiz degandi, top», «wifi parolini qidir» (ism aytilmasa — barcha chatlardan).\n"
    "/find &lt;so'rov&gt; — buyruq bilan\n\n"
    "<b>🗂 Chat arxivi</b>\n"
    "Barcha shaxsiy chatlar (istisnodagilar ham) saqlanib boriladi. Kimdir xabar yoki butun chatni "
    "o'chirsa — sizga xabar keladi va yozishmani <b>HTML fayl</b> qilib yuklab olasiz.\n"
    "/arxiv — arxiv paneli, /arxiv &lt;ism&gt; — chatni nomi bo'yicha topish\n"
    "Kimdir xabarini tahrirlasa yoki ism/username/bio/rasmini o'zgartirsa — xabar beradi. "
    "Qidiruv o'chirilgan va tahrirlangan xabarlar ichidan ham topadi.\n"
    "/deleted — akkauntini o'chirgan odamlar (taxminiy ismi bilan) va ular bilan yozishmalar. "
    "Oddiy yozsangiz ham bo'ladi: «o'chirilgan akkauntlarni ko'rsat», «Aziz aka bilan chatni top»\n"
    "/xulosa &lt;ism&gt; — yozishmaning AI xulosasi, va'dalar va ochiq savollar\n"
    "/vadalar — bergan va'dalaringiz (muddati kelganda eslatadi)\n"
    "Har yakshanba — haftalik chat hisoboti, har kuni — bazaning Drive'ga zaxirasi. "
    "Sozlash: 🗂 Chat arxivi → ⚙️ Kuzatuv sozlamalari\n\n"
    "<b>🔔 Eslatmalar</b>\n"
    "Botga oddiy yozing yoki ayting — hodisa haqiqatan ro'y berganda xabar beraman:\n"
    "• «95 446 68 86 raqami tg ga qo'shilsa / akkaunt ochsa / yashirindan chiqarsa xabar ber»\n"
    "• «@ali online bo'lsa xabar ber», «Vali onlayn bo'lsa ayt»\n"
    "• «Ali har safar online bo'lganda xabar ber» — har gal (aks holda bir marta)\n"
    "Bildirishnomaga reply qilib «shu user online bo'lsa menga xabar ber» deyish ham mumkin.\n"
    "/eslatma — ro'yxat va o'chirish; /eslatma @user yoki /eslatma raqam — tez qo'yish\n\n"
    "<b>📸 Instagram</b>\n"
    "/ig — Instagram paneli: 🔥 bir marta ko'riladigan rasm/videolarni saqlash, 🤖 avtojavob (yoqish/o'chirish), "
    "🗂 chat arxivi (qaytarib olingan xabarlar bilan), 📥 Direct xabarlari botga\n"
    "/iglogin &lt;username&gt; &lt;parol&gt; — ulash (xabar darhol o'chiriladi), /igsession &lt;sessionid&gt; — "
    "cookie bilan ulash, /iglogout — chiqish\n"
    "/iglogin2, /igsession2, /iglogout2 — 2-Instagram akkaunt (ikkalasi bir vaqtda ishlaydi)\n"
    "Instagram bildirishnomasiga reply qilib yozsangiz — javob Instagram'ga ketadi.\n"
    "📥 <b>Havoladan yuklash:</b> post, reels yoki storis havolasini botga tashlang — rasm/videoni yuklab "
    "beradi (albom bo'lsa hammasini). Buyruq bilan: /igyukla &lt;havola&gt;\n\n"
    "<b>☁️ Google Drive</b>\n"
    "/drive — holati; Google ruxsati eskirsa «🔑 Qayta ulash» (terminal kerak emas)\n\n"
    "<b>👥 Guruhlar</b>\n"
    "/guruhlar — akkauntingizdagi guruhlar ro'yxati. Guruhni yoqsangiz, unga kelgan har bir xabar "
    "(matn, rasm, video, ovozli xabar, fayl) botga nusxalanib keladi.\n\n"
    "<b>Profil</b>\n"
    "/bio — joriy bio va holat\n"
    "/bio on | off | now | restore — yoqish, to'xtatish, hozir yangilash, asl bio'ni qaytarish\n"
    "/quote &lt;matn&gt; — doimiy iqtibos, /quote auto — har kuni almashadi, /quote off — iqtibossiz\n"
    "/online on | off — akkauntni online ko'rsatish\n"
    "/tglogin — ulangan Telegram akkaunt; /tglogout — chiqish (keyin botda yangi akkaunt ulanadi)\n"
    "/tglogin2 — 2-Telegram akkauntni ham ulash (kotiba va guruhlar ikkalasida); /tglogout2 — undan chiqish\n"
    "/yangixabar on | off — «📩 Yangi xabar» bildirishnomalari (SHOSHILINCH va chaqiruv doim keladi)\n\n"
    "<b>💰 Shaxsiy buxgalter</b>\n"
    "Botga oddiy yozing yoki <b>ovozli xabar</b> yuboring: «bugun nonga 25 ming, zapravkaga 30 ming "
    "ishlatdim», «Alidan 200 ming oldim», «Valiga 100 ming qarz berdim» — kirim/chiqim avtomatik yoziladi.\n"
    "/fin — moliya paneli\n"
    "/today, /days3, /week, /month — bugungi, 3 kunlik, haftalik, oylik hisobot\n"
    "/balance — umumiy balans va qarzlar\n"
    "/history — oxirgi yozuvlar (o'chirish tugmasi bilan)\n"
    "/undo — oxirgi yozuvni bekor qilish\n"
    "/budget &lt;kategoriya|jami&gt; &lt;summa&gt; — oylik byudjet, /budget — holati\n"
    "/export [7|30|month|all] — Excel (CSV) fayl\n\n"
    "<b>Boshqa</b>\n"
    "/inbox — javob berilmagan xabarlar\n"
    "/lichka &lt;id yoki @username&gt; — o'sha odamning lichkasiga o'tish tugmasi. "
    "Botga faqat ID raqamini (6+ xona) yuborsangiz ham bo'ladi\n"
    "/reply &lt;chat_id&gt; &lt;matn&gt; — sizning nomingizdan javob yozish\n"
    "/ignore &lt;id yoki @username&gt; — bu odamga avtojavob bermaslik\n"
    "/unignore &lt;id&gt; — istisnodan chiqarish\n"
    "/ignored — istisnolar ro'yxati\n\n"
    "Xulosa xabariga <i>reply</i> qilib yozsangiz, matn o'sha odamga sizning nomingizdan ketadi.\n\n"
    "💡 Buyruqlarni eslab qolish shart emas — pastdagi <b>tugmali menyu</b>dan foydalaning (/menu)."
)

# ---------- doimiy tugmali menyu (buyruqlarni eslab qolmaslik uchun) ----------
MENU_PANEL = "⚙️ Boshqaruv"
MENU_INBOX = "📬 Inbox"
MENU_ARCHIVE = "🗂 Chat arxivi"
MENU_FIN = "💰 Moliya"
MENU_FIND = "🔎 Qidirish"
MENU_REPORT = "📈 Hisobot"
MENU_HELP = "❓ Yordam"
MENU_PROMISES = "📌 Va'dalar"
MENU_WATCH = "🔔 Eslatmalar"
MENU_IG = "📸 Instagram"
MENU_GROUPS = "👥 Guruhlar"
MENU_TEXTS = {MENU_PANEL, MENU_INBOX, MENU_ARCHIVE, MENU_FIN, MENU_FIND, MENU_REPORT, MENU_HELP, MENU_PROMISES,
              MENU_WATCH, MENU_IG, MENU_GROUPS}


def main_keyboard() -> list:
    return [
        [Button.text(MENU_PANEL, resize=True, persistent=True), Button.text(MENU_INBOX), Button.text(MENU_WATCH)],
        [Button.text(MENU_ARCHIVE), Button.text(MENU_PROMISES), Button.text(MENU_FIN)],
        [Button.text(MENU_FIND), Button.text(MENU_REPORT), Button.text(MENU_HELP)],
        [Button.text(MENU_IG), Button.text(MENU_GROUPS)],
    ]

MODE_NAMES = {"on": "🟢 doim yoqiq (/on)", "off": "⚪️ o'chirilgan (/off)", "auto": "🤖 aqlli (/auto)"}
TAGS = {
    "oila": "👨‍👩‍👧 oila — kotiba javob bermaydi, faqat sizga xabar beradi",
    "mijoz": "💼 mijoz — rasmiy ohang",
    "dost": "🤝 do'st — samimiy ohang",
}
TAG_ALIASES = {"do'st": "dost", "dost": "dost", "family": "oila", "client": "mijoz", "friend": "dost"}

# begona odam botga yozsa, "Bu shaxsiy bot." javobi soatiga ko'pi bilan bir marta
_stranger_replied: dict[int, float] = {}
STRANGER_REPLY_COOLDOWN = 3600

# notification message id -> chat_id  (reply orqali javob berish uchun)
_notif_map: dict[int, int] = {}
# bildirishnoma id -> akkaunt (faqat 2-akkaunt; yo'q bo'lsa — 1-akkaunt)
_notif_slot: dict[int, int] = {}
_awaiting_slot: dict[str, int] = {}   # «✍️ Javob berish» bosilgan chat qaysi akkauntdan
# egasi "Javob berish" tugmasini bosgandan keyin kutilayotgan chat
_awaiting_reply: dict[str, int] = {}
# chatlardan qidirish: tanlov kutayotgan so'rovlar va topilgan xabarlar (tugmalar uchun)
_find_pending: dict[str, tuple] = {}
_find_saved: dict[str, list[tuple[int, int]]] = {}
_find_seq = itertools.count(1)
# «Buni eslatma deb tushundim» tasdiqlashi: xabar id -> egasining matni
_watch_pending: dict[str, str] = {}
# Instagram bildirishnomasi id -> Instagram chat (thread) id — reply qilib yozilsa javob Instagram'ga ketadi
_ig_notif_map: dict[int, str] = {}
# «📥 Yuklab olish» tugmasi: kalit -> ulashilgan post/reels havolasi (callback data 64 bayt bilan cheklangan)
_ig_shares: dict[str, str] = {}


class Ctx:
    bot: TelegramClient
    user: TelegramClient
    owner_id: int
    monitor = None  # health.Monitor (main.py o'rnatadi)
    users: dict = {}   # akkaunt raqami -> TelegramClient (1 — asosiy, 2 — ikkinchi)
    names: dict = {}   # akkaunt raqami -> ko'rinadigan nom


ctx = Ctx()


TG_LIMIT = 4096        # Telegram bitta xabar cheklovi (UTF-16 birlikda: emoji = 2)
CHUNK_LIMIT = 3900     # zaxira bilan (HTML teglar ham hisoblanadi)
_TAG_RE = re.compile(r"<(/?)(\w+)[^>]*>")


tg_len = progress.tg_len   # UTF-16 birlikda uzunlik (Telegram belgilarni shunday hisoblaydi)


def _tags_balanced(text: str) -> bool:
    """Bo'lakdagi HTML teglar ochilib-yopilganmi (bo'lak chegarasida teg ikkiga bo'linmasligi uchun)."""
    opened: list[str] = []
    for m in _TAG_RE.finditer(text):
        if m.group(1):
            if not opened or opened.pop() != m.group(2):
                return False
        else:
            opened.append(m.group(2))
    return not opened


def _hard_split(text: str, limit: int) -> list[str]:
    """Bitta bo'lim ham sig'masa — qatorlab, kerak bo'lsa so'zlab bo'ladi."""
    out, cur = [], ""
    for piece in text.splitlines(keepends=True) or [text]:
        while tg_len(piece) > limit:                       # juda uzun bitta qator
            cut = limit // 2                               # emoji 2 birlik — xavfsiz boshlang'ich
            while cut < len(piece) and tg_len(piece[:cut + 1]) <= limit:
                cut += 1
            space = piece.rfind(" ", 1, cut)
            cut = space if space > cut // 2 else cut
            out.append((cur + piece[:cut]).strip("\n"))
            cur, piece = "", piece[cut:]
        if tg_len(cur + piece) > limit:
            out.append(cur.strip("\n"))
            cur = ""
        cur += piece
    if cur.strip():
        out.append(cur.strip("\n"))
    return out


def split_message(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Uzun matnni Telegram limitiga mos bo'laklarga bo'ladi (MessageTooLongError bo'lmasligi uchun).
    Avval bo'limlar (bo'sh qator), keyin qatorlar bo'yicha; HTML teglar bo'lak ichida yopiq qoladi."""
    if tg_len(text) <= limit:
        return [text]
    parts: list[str] = []
    cur = ""
    for block in text.split("\n\n"):
        merged = f"{cur}\n\n{block}" if cur else block
        if tg_len(merged) <= limit:
            cur = merged
            continue
        if cur:
            parts.append(cur)
        cur = ""
        if tg_len(block) <= limit:
            cur = block
        else:
            chunks = _hard_split(block, limit)
            parts += chunks[:-1]
            cur = chunks[-1] if chunks else ""
    if cur:
        parts.append(cur)
    # Teg bo'lak chegarasida ochiq qolgan bo'lsa — keyingisi bilan qo'shib yuboramiz (sig'sa)
    fixed: list[str] = []
    for part in parts:
        if fixed and not _tags_balanced(fixed[-1]) and tg_len(f"{fixed[-1]}\n\n{part}") <= TG_LIMIT:
            fixed[-1] = f"{fixed[-1]}\n\n{part}"
        else:
            fixed.append(part)
    return fixed


async def send_long(send, text: str, *, buttons=None, **kwargs) -> None:
    """Uzun matnni bo'laklab yuboradi (tugmalar — oxirgi bo'lakka). send: event.respond yoki bot.send_message
    kabi chaqiriladigan funksiya. HTML xato bersa — oddiy matn sifatida qayta yuboriladi."""
    parts = split_message(text)
    for i, part in enumerate(parts):
        extra = {"buttons": buttons} if buttons is not None and i == len(parts) - 1 else {}
        try:
            await send(part, **kwargs, **extra)
        except errors.MessageTooLongError:
            log.warning("Xabar bo'lagi baribir uzun (%d birlik) — qisqartirib yuboriladi", tg_len(part))
            await send(part[:2000] + "\n…", **kwargs, **extra)
        except errors.RPCError as e:
            if "ENTITIES" not in type(e).__name__.upper() and "entit" not in str(e).lower():
                raise
            log.warning("HTML teglar buzilgan (%s) — oddiy matn sifatida yuboriladi", type(e).__name__)
            await send(re.sub(r"<[^>]+>", "", part), **{**kwargs, "parse_mode": None}, **extra)


async def send_html(text: str) -> None:
    """Egasiga HTML xabar (kunlik hisobot uchun). Uzun bo'lsa bo'laklab yuboriladi."""
    await send_long(lambda t, **kw: ctx.bot.send_message(ctx.owner_id, t, **kw), text, parse_mode="html")


def _who(name: str, username: str | None, chat_id: int) -> str:
    who = f"<b>{html.escape(name or 'Nomaʼlum')}</b>"
    if username:
        who += f" (@{html.escape(username)})"
    return who + f"  <code>{chat_id}</code>"


def _buttons(chat_id: int, slot: int = 1):
    """slot 2 — 2-akkaunt: tugma ma'lumotiga «:2» qo'shiladi (javob o'sha akkauntdan ketadi)."""
    sfx = "" if slot == 1 else f":{slot}"
    rows = [
        [Button.inline("✍️ Javob berish", f"reply:{chat_id}{sfx}".encode()),
         Button.inline("✅ Ko'rildi", f"done:{chat_id}{sfx}".encode())],
        [Button.inline("🔇 Bu chatda AI jim", f"mute:{chat_id}{sfx}".encode()),
         Button.inline("🚫 Istisnoga qo'shish", f"ignore:{chat_id}{sfx}".encode())],
    ]
    if slot == 1:   # chat arxivi faqat 1-akkauntda
        rows.append([Button.inline("📄 Yozishmani yuklab olish (HTML)", f"arc:html:{chat_id}:a".encode())])
    return rows


def _notify_buttons(chat_id: int, slot: int = 1):
    """«📩 Yangi xabar» tugmalari: odatdagilar + bildirishnomani o'chirish."""
    return _buttons(chat_id, slot) + [[Button.inline("🔕 «Yangi xabar» kelmasin", b"newmsg:off")]]


def _acc_head(slot: int) -> str:
    """2-akkauntdan kelgan bildirishnoma boshida qaysi akkaunt ekani."""
    if slot == 1:
        return ""
    return f"📱 <b>{slot}-akkaunt</b>: {html.escape(ctx.names.get(slot, ''))}\n"


def _remember_notif(msg_id: int, chat_id: int, slot: int) -> None:
    _notif_map[msg_id] = chat_id
    if slot != 1:
        _notif_slot[msg_id] = slot


def new_msg_panel() -> tuple[str, list]:
    if runtime.get("new_msg_alerts"):
        return ("📩 «Yangi xabar» bildirishnomalari: 🟢 yoqiq",
                [[Button.inline("🔕 O'chirish", b"newmsg:off")]])
    return ("📩 «Yangi xabar» bildirishnomalari: ⚪️ o'chiq\n"
            "Kotiba javob berishda davom etadi; 🔴 SHOSHILINCH, 📞 chaqiruv va media baribir keladi. "
            "Javob kutayotganlar: /inbox",
            [[Button.inline("🔔 Yoqish", b"newmsg:on")]])


# ---------- 📱 2-akkaunt ----------
_login2_task: asyncio.Task | None = None


def _second_status() -> str:
    if 2 in ctx.users:
        return f"📱 2-akkaunt: <b>{html.escape(ctx.names.get(2, ''))}</b> — ulangan (chiqish: /tglogout2)"
    return "📱 2-akkaunt ulanmagan — ulash: /tglogin2"


async def connect_second(client, slot: int = 2) -> str | None:
    """Kirilgan 2-akkauntni ishga tushiradi: kotiba (avtojavob, bildirishnomalar) va guruh klonlash.
    Xato bo'lsa — sababi (matn), bo'lmasa None."""
    import userbot   # userbot control_bot'ni import qiladi — aylanma importdan qochish uchun shu yerda
    me = await client.get_me()
    if me is None or getattr(me, "bot", False):
        return "bu shaxsiy akkaunt emas"
    if me.id == ctx.owner_id or any(getattr(c, "_anna_me_id", None) == me.id
                                    for s, c in ctx.users.items() if s != slot):
        await client.disconnect()
        return "bu akkaunt allaqachon ulangan (asosiy akkaunt)"
    client._anna_me_id = me.id
    ctx.users[slot] = client
    ctx.names[slot] = " ".join(p for p in (me.first_name, me.last_name) if p) + (
        f" (@{me.username})" if me.username else "")
    userbot.setup(client, me.id, slot=slot)
    groups.setup(client, group_clone_text, send_media, slot=slot)
    log.info("%d-akkaunt ulandi: %s (id %s)", slot, me.first_name, me.id)
    return None


async def restore_second() -> None:
    """Ishga tushishda bazadagi 2-akkaunt sessiyasi bilan ulanadi (login so'ralmaydi)."""
    try:
        had = await storage.tg_get_session(2)
    except Exception:
        had = None
    if not had:
        return
    try:
        client = await tg_login.connect_saved(2)
    except Exception as e:
        log.warning("2-akkauntga ulanib bo'lmadi (%s)", type(e).__name__)
        return
    if client is None:
        await alert("⚠️ 2-akkaunt sessiyasi bekor qilingan (Telegram → Qurilmalar'dan chiqarilgan bo'lishi "
                    "mumkin). Qayta ulash: /tglogin2")
        return
    problem = await connect_second(client)
    if problem:
        await alert(f"⚠️ 2-akkaunt ulanmadi: {problem}")


async def _login_second(bot) -> None:
    """/tglogin2: telefon -> kod -> parol shu botda; kirilgach darhol ishga tushadi (qayta ishga tushirmasdan)."""
    from telethon.sessions import StringSession
    from tg_client import create_user_client
    client = create_user_client(StringSession())
    try:
        await client.connect()
        await tg_login.via_bot(bot, client, slot=2, owner=ctx.owner_id)
    except tg_login.LoginCancelled:
        await client.disconnect()
        await alert("❌ 2-akkaunt ulanishi bekor qilindi.")
        return
    except Exception as e:
        log.exception("2-akkaunt loginida xato")
        await client.disconnect()
        await alert(f"❌ 2-akkaunt ulanmadi: {type(e).__name__}")
        return
    problem = await connect_second(client)
    if problem:
        try:
            await storage.tg_delete_session(2)
        except Exception:
            pass
        await alert(f"❌ 2-akkaunt ulanmadi: {problem}")
        return
    await alert(f"✅ 2-akkaunt ulandi: {ctx.names.get(2, '')}\n"
                "Endi unga yozganlarga ham kotiba javob beradi, bildirishnomalar «📱 2-akkaunt» belgisi bilan "
                "keladi, «✍️ Javob berish» shu akkauntdan yuboradi. Guruhlari: 👥 Guruhlar.")


async def logout_second(slot: int = 2) -> str:
    client = ctx.users.pop(slot, None)
    name = ctx.names.pop(slot, "")
    groups.forget_client(slot)
    try:
        await storage.tg_delete_session(slot)
    except Exception as e:
        log.warning("2-akkaunt sessiyasi bazadan o'chirilmadi (%s)", type(e).__name__)
    if client is not None:
        try:
            await client.log_out()
        except Exception as e:
            log.warning("2-akkauntdan chiqishda xato (%s)", type(e).__name__)
    return f"✅ 2-akkauntdan chiqildi{': ' + name if name else ''}. Qayta ulash: /tglogin2"


async def tg_logout(exit_after: float = 2.0) -> None:
    """Telegram akkauntdan chiqadi (sessiya bekor), bazadagi sessiyani o'chiradi va dasturni to'xtatadi —
    run.bat / server qayta ishga tushiradi va yangi akkaunt uchun login so'raladi."""
    import os
    import instance
    try:
        await storage.tg_delete_session()
    except Exception as e:
        log.warning("Bazadagi Telegram sessiyasi o'chirilmadi (%s)", type(e).__name__)
    try:
        await ctx.user.log_out()   # fayl sessiyasini ham o'chiradi
    except Exception as e:
        log.warning("Telegram'dan chiqishda xato (%s)", type(e).__name__)
    if config.SESSION_STRING:
        log.warning("SESSION_STRING endi yaroqsiz — server sozlamalaridan o'chirib qo'ying")
    try:
        await ctx.bot.send_message(
            ctx.owner_id, "✅ Akkauntdan chiqildi. Anna qayta ishga tushgach, shu botda yangi akkaunt uchun "
                          "telefon raqam so'raladi." + ("\n⚠️ SESSION_STRING endi yaroqsiz — serverdagi sozlamadan "
                                                       "o'chirib qo'ying." if config.SESSION_STRING else ""))
    except Exception:
        pass
    log.warning("Telegram akkauntdan chiqildi — dastur qayta ishga tushish uchun to'xtaydi")
    if config.SINGLE_INSTANCE:
        await instance.release()
    asyncio.get_running_loop().call_later(exit_after, os._exit, 0)


async def notify(chat_id: int, name: str, username: str | None, text: str,
                 summary: str, urgent: bool, ai_reply: str | None, reply_to: int | None = None,
                 slot: int = 1) -> None:
    """reply_to: shu xabar bilan kelgan media (bot xabari) id si — bildirishnoma uning tagiga bog'lanadi."""
    if not urgent and not runtime.get("new_msg_alerts"):
        return  # egasi «📩 Yangi xabar»ni o'chirgan (/yangixabar); xabar /inbox ga baribir yoziladi
    head ="🔴 <b>SHOSHILINCH</b>\n" if urgent else "📩 <b>Yangi xabar</b>\n"
    body = (
        f"{head}{_acc_head(slot)}👤 {_who(name, username, chat_id)}\n\n"
        f"💬 <i>{html.escape(text[:1000])}</i>\n"
    )
    if summary:
        body += f"\n📝 <b>Xulosa:</b> {html.escape(summary)}\n"
    if ai_reply:
        body += f"\n🤖 <b>Anna javobi:</b> {html.escape(ai_reply)}"
    try:
        msg = await ctx.bot.send_message(ctx.owner_id, body, parse_mode="html",
                                         buttons=_buttons(chat_id, slot) if urgent
                                         else _notify_buttons(chat_id, slot), reply_to=reply_to)
        _remember_notif(msg.id, chat_id, slot)
    except Exception:
        log.exception("Egasiga xabar yuborilmadi (botga /start bosganmisiz?)")


async def call_owner(chat_id: int, name: str, username: str | None, text: str, summary: str,
                     slot: int = 1) -> None:
    """Suhbatdosh egasini chaqirishni so'radi — alohida, ko'zga tashlanadigan xabar."""
    body = (
        "📞 <b>SIZNI CHAQIRISHYAPTI!</b>\n" + _acc_head(slot) +
        f"👤 {_who(name, username, chat_id)}\n\n"
        f"💬 <i>{html.escape(text[:1000])}</i>\n"
    )
    if summary:
        body += f"\n📝 <b>Xulosa:</b> {html.escape(summary)}"
    try:
        msg = await ctx.bot.send_message(ctx.owner_id, body, parse_mode="html",
                                         buttons=_buttons(chat_id, slot))
        _remember_notif(msg.id, chat_id, slot)
    except Exception:
        log.exception("Chaqiruv xabari yuborilmadi (botga /start bosganmisiz?)")


def _inline(rows: list | None) -> list | None:
    """[[(matn, data), ...], ...] -> Telethon inline tugmalari."""
    return [[Button.inline(t, d.encode()) for t, d in row] for row in rows] if rows else None


async def watch_alert(text: str, buttons: list | None = None) -> None:
    """🔔 Eslatma bajarildi (watchers.py) — egasiga tugmalar bilan xabar."""
    parts = split_message(text)
    try:
        for part in parts[:-1]:   # uzun ro'yxat (masalan storis ko'ruvchilari) bo'laklab yuboriladi
            await ctx.bot.send_message(ctx.owner_id, part, parse_mode="html")
        msg = await ctx.bot.send_message(ctx.owner_id, parts[-1], parse_mode="html",
                                         buttons=_inline(buttons))
    except Exception:
        log.exception("Eslatma xabari botga yuborilmadi")
        return
    # boshqa bildirishnomalar kabi: reply qilib yozilsa — o'sha odamga ketadi (yoki unga yangi eslatma)
    uid = next((d.split(":")[2] for row in buttons or [] for _, d in row if d.startswith("wch:dm:")), None)
    if uid and uid.isdigit():
        _notif_map[msg.id] = int(uid)


async def _watch_request(event, req: "watchers.Request", note: str = "") -> None:
    """Egasi aytgan eslatmani qo'yadi va natijani yozadi."""
    try:
        text, buttons = await watchers.handle(ctx.user, req)
    except Exception as e:
        log.exception("Eslatma qo'yilmadi")
        await event.respond(f"❌ Eslatma qo'yilmadi: {type(e).__name__}")
        return
    await event.respond(text + note, parse_mode="html", buttons=_inline(buttons), link_preview=False)


async def alert(text: str) -> None:
    """Egasiga tizim ogohlantirishi (masalan, AI kaliti ishlamayapti)."""
    try:
        await send_long(lambda t, **kw: ctx.bot.send_message(ctx.owner_id, t, **kw), text)
    except Exception:
        log.warning("Ogohlantirish botga yuborilmadi")


_MEDIA_ICONS = {"rasm": "📸", "video": "🎬", "ovozli xabar": "🎤", "video-xabar": "📹", "audio": "🎵", "fayl": "📎"}


async def send_media(chat_id: int, name: str, data: bytes, filename: str | None = None, *,
                     kind: str = "media", username: str | None = None, caption: str = "",
                     view_once: bool = False, mime: str = "", attributes=None,
                     thumb: bytes | None = None, link_reply: bool = True, slot: int = 1) -> int | None:
    """Suhbatdosh yuborgan mediani egasiga ASL ko'rinishida yuboradi: rasm — rasm, video — video,
    ovozli xabar — ovozli xabar. Bir marta ko'riladigan media ham oddiy (saqlasa bo'ladigan) holda keladi.
    Yuborilgan xabar id sini qaytaradi (bildirishnoma shunga reply bo'lib keladi)."""
    cap = _acc_head(slot) + f"{_MEDIA_ICONS.get(kind, '📎')} {_who(name, username, chat_id)} — {html.escape(kind)}"
    if view_once:
        cap += "\n🔥 <b>Bir marta ko'riladigan (o'chib ketadigan) media</b> — siz uchun saqlab olindi"
    if caption:
        cap += f"\n\n💬 <i>{html.escape(caption[:700])}</i>"

    def _file() -> io.BytesIO:
        f = io.BytesIO(data)
        f.name = filename or "file.bin"  # kengaytma bo'lmasa Telegram uni nomsiz fayl qilib ko'rsatadi
        return f

    is_round = kind == "video-xabar"
    try:
        try:
            msg = await ctx.bot.send_file(
                ctx.owner_id, _file(),
                caption=None if is_round else cap, parse_mode="html",
                force_document=kind == "fayl" and mime.startswith("image/"),  # fayl qilib yuborilgan rasm sifati saqlansin
                voice_note=kind == "ovozli xabar",
                video_note=is_round,
                supports_streaming=kind in ("video", "video-xabar"),
                attributes=attributes, thumb=thumb,
            )
        except Exception:
            log.exception("Media asl ko'rinishida yuborilmadi — fayl sifatida yuboriladi")
            msg = await ctx.bot.send_file(ctx.owner_id, _file(), caption=None if is_round else cap,
                                          parse_mode="html", force_document=True)
        if link_reply:  # guruh nusxasiga reply qilinsa, javob guruhga ketmasin (link_reply=False)
            _remember_notif(msg.id, chat_id, slot)
        if is_round:  # dumaloq videoga izoh yozib bo'lmaydi — alohida xabar
            note = await ctx.bot.send_message(ctx.owner_id, cap, parse_mode="html", reply_to=msg.id)
            if link_reply:
                _remember_notif(note.id, chat_id, slot)
        return msg.id
    except Exception:
        log.exception("Media botga yuborilmadi")
        return None


# ---------- 📸 Instagram ----------
async def send_ig_media(data: bytes, filename: str, caption: str, video: dict | None = None,
                        as_file: bool = False) -> bool:
    """Instagram'dagi bir martalik rasm/video — egasiga oddiy (saqlasa bo'ladigan) holda. Yuborilsa True."""
    def _file() -> io.BytesIO:
        f = io.BytesIO(data)
        f.name = filename
        return f

    attrs = None
    if video:
        attrs = [types.DocumentAttributeVideo(duration=float(video.get("duration") or 0),
                                              w=int(video.get("w") or 0) or 720, h=int(video.get("h") or 0) or 1280,
                                              supports_streaming=True)]
    try:
        try:
            await ctx.bot.send_file(ctx.owner_id, _file(), caption=caption, parse_mode="html",
                                    force_document=as_file, supports_streaming=bool(video), attributes=attrs)
        except Exception:
            log.exception("Instagram media asl ko'rinishida yuborilmadi — fayl sifatida yuboriladi")
            await ctx.bot.send_file(ctx.owner_id, _file(), caption=caption, parse_mode="html", force_document=True)
        return True
    except Exception:
        log.exception("Instagram media botga yuborilmadi")
        return False


def _ig_buttons(thread_id: str, share: str = "") -> list:
    rows = [[Button.inline("✍️ Javob berish", f"ig:reply:{thread_id}".encode()),
             Button.inline("🔇 Bu chatda AI jim", f"ig:mute:{thread_id}".encode())]]
    if share:
        # Ulashilgan post/reels: bir bosishda yuklab olish yoki Instagram'da ochish
        key = _remember(_ig_shares, share)
        rows.append([Button.inline("📥 Yuklab olish", f"ig:dl:{key}".encode()),
                     Button.url("🔗 Ochish", share)])
    rows.append([Button.inline("💬 Yozishmani ko'rish", f"ig:view:{thread_id}:0".encode()),
                 Button.inline("📄 HTML", f"ig:html:{thread_id}".encode())])
    return rows


async def ig_notify(thread_id: str, text: str, share: str = "") -> None:
    """Instagram xabari / qaytarib olingan xabar — egasiga. Reply qilib yozilsa javob Instagram'ga ketadi.
    share — ulashilgan post/reels havolasi bo'lsa, «📥 Yuklab olish» tugmasi qo'shiladi."""
    parts = split_message(text)
    try:
        for part in parts[:-1]:
            await ctx.bot.send_message(ctx.owner_id, part, parse_mode="html", link_preview=False)
        msg = await ctx.bot.send_message(ctx.owner_id, parts[-1], parse_mode="html",
                                         buttons=_ig_buttons(thread_id, share), link_preview=False)
        _ig_notif_map[msg.id] = thread_id
    except Exception:
        log.exception("Instagram bildirishnomasi botga yuborilmadi")


async def _download_ig_links(event, links: list) -> None:
    """Instagram havolalaridagi mediani yuklab egasiga yuboradi (jonli ko'rsatkich bilan)."""
    bar = await progress.open_progress(ctx.bot, ctx.owner_id, event, "Instagram'dan olinmoqda...")
    async with bar:
        result = await instagram.download_links(links, bar)
        await bar.finish(result, event, send_long)


async def _edit_or_send(event, text: str, rows: list) -> None:
    """Tugmali ko'rinishni o'rniga yangilaydi; sig'masa yoki tahrirlanmasa — yangi xabar(lar) qilib yuboradi."""
    try:
        await event.edit(text, parse_mode="html", buttons=_inline(rows), link_preview=False)
    except Exception:
        await send_long(event.respond, text, parse_mode="html", buttons=_inline(rows), link_preview=False)


async def _send_ig_panel(event, extra: str = "", edit: bool = False) -> None:
    text, rows = await instagram.panel()
    if extra:
        text += f"\n\n{extra}"
    if edit:
        try:
            await event.edit(text, parse_mode="html", buttons=_inline(rows), link_preview=False)
            return
        except errors.MessageTooLongError:
            pass  # panel uzun — pastda yangi xabar(lar) bo'lib yuboriladi
        except Exception:
            pass  # o'zgarmagan bo'lsa Telegram xato qaytaradi
    await send_long(event.respond, text, parse_mode="html", buttons=_inline(rows), link_preview=False)


async def _send_groups_panel(event, page: int = 0, edit: bool = False, refresh: bool = False) -> None:
    try:
        text, rows = groups.panel(await groups.my_groups(refresh=refresh), page)
    except Exception as e:
        log.exception("Guruhlar ro'yxati olinmadi")
        text, rows = f"❌ Guruhlar ro'yxatini olib bo'lmadi: {type(e).__name__}", None
    if edit:
        try:
            await event.edit(text, parse_mode="html", buttons=_inline(rows))
            return
        except errors.MessageNotModifiedError:
            return
        except Exception:
            pass
    await event.respond(text, parse_mode="html", buttons=_inline(rows))


# ---------- ☁️ Google Drive'ni botdan qayta ulash ----------
_drive_state: dict[str, float] = {}   # OAuth «state» -> yaratilgan vaqt (begona havolani qabul qilmaslik)
DRIVE_AUTH_TTL = 30 * 60

DRIVE_AUTH_BUTTON = [[Button.inline("🔑 Google Drive'ni qayta ulash", b"gd:auth")]]


async def drive_auth_alert() -> None:
    """gdrive: refresh token o'ldi — arxiv, media, Instagram va zaxira uchun BITTA xabar."""
    await ctx.bot.send_message(
        ctx.owner_id,
        "☁️❌ <b>Google Drive ulanishi uzildi</b> — Google ruxsati (refresh token) eskirgan.\n"
        "Chat arxivi, rasm/videolar, Instagram arxivi va zaxira Drive'ga yuklanmayapti "
        "(bazada saqlanib turibdi, ulangach hammasi yuklanadi).\n\n"
        "Pastdagi tugmani bosing — 1 daqiqalik ish.",
        parse_mode="html", buttons=DRIVE_AUTH_BUTTON)


async def _drive_panel(event) -> None:
    problem = await gdrive.check() if gdrive.enabled() else "ulanmagan"
    text = "☁️ <b>Google Drive</b>: " + ("🟢 ishlayapti" if not problem else f"❌ {html.escape(problem)}")
    buttons = [[Button.inline("🔑 Qayta ulash", b"gd:auth"), Button.inline("🔄 Tekshirish", b"gd:check")]]
    if not gdrive.can_authorize():
        text += ("\n\n.env da GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET va GOOGLE_DRIVE_ROOT_FOLDER_ID "
                 "to'ldirilishi kerak (README → Google Drive).")
        buttons = None
    await event.respond(text, parse_mode="html", buttons=buttons)


async def _drive_start_auth(event) -> None:
    if not gdrive.can_authorize():
        await event.respond("❌ .env da GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / "
                            "GOOGLE_DRIVE_ROOT_FOLDER_ID yo'q.")
        return
    import secrets
    now = time.time()
    for k in [k for k, t in _drive_state.items() if now - t > DRIVE_AUTH_TTL]:
        _drive_state.pop(k, None)
    state = secrets.token_urlsafe(16)
    _drive_state[state] = now
    await event.respond(
        "🔑 <b>Google Drive'ni qayta ulash</b>\n\n"
        "1️⃣ Pastdagi tugmani bosing va Drive ulangan Google akkauntni tanlab <b>ruxsat bering</b> "
        "(«Google hasn't verified this app» chiqsa: <i>Advanced → Go to ... (unsafe)</i>).\n"
        "2️⃣ Oxirida brauzer <b>«Sahifa ochilmadi / This site can't be reached»</b> deydi — bu normal.\n"
        "3️⃣ Brauzerning <b>manzil qatoridagi butun havolani</b> (<code>http://127.0.0.1:8765/?state=…&amp;code=…</code>) "
        "nusxalab shu yerga yuboring.\n\n"
        "⚠️ Token 7 kunda yana o'chmasligi uchun: Google Cloud → <i>OAuth consent screen</i> → "
        "Publishing status → <b>In production</b>.",
        parse_mode="html", link_preview=False,
        buttons=[[Button.url("🔐 Google'da ruxsat berish", gdrive.auth_url(state))]])


async def _drive_finish(event) -> None:
    state, code = gdrive.parse_redirect(event.text)
    try:
        await event.delete()   # havolada bir martalik kod bor
    except Exception:
        pass
    if state not in _drive_state or time.time() - _drive_state[state] > DRIVE_AUTH_TTL:
        await event.respond("❌ Bu havola eskirgan yoki boshqa so'rovdan. Qaytadan bosing:",
                            buttons=DRIVE_AUTH_BUTTON)
        return
    _drive_state.pop(state, None)
    status = await event.respond("⏳ Google bilan tekshirilmoqda...")
    was_enabled = gdrive.enabled()
    try:
        await gdrive.authorize(code)
        problem = await gdrive.check()
    except Exception as e:
        await status.edit(f"❌ Ulanmadi: {gdrive.explain(e)}", buttons=DRIVE_AUTH_BUTTON)
        return
    if problem:
        await status.edit(f"⚠️ Token olindi, lekin: {problem}")
        return
    archive.drive_wake()
    archive.media_wake()
    instagram.drive_wake()
    instagram.media_wake()
    if was_enabled:
        await status.edit("✅ Google Drive qayta ulandi — arxiv, media va zaxira yana yuklanmoqda.")
    else:   # Drive siklari ishga tushganda yoqilmagan edi — qayta ishga tushirib yoqamiz
        import os
        await status.edit("✅ Google Drive ulandi. Anna qayta ishga tushyapti (Drive qismlari yoqiladi)...")
        if config.SINGLE_INSTANCE:
            import instance
            await instance.release()
        asyncio.get_running_loop().call_later(2, os._exit, 0)


async def group_clone_text(text: str, link: str | None) -> None:
    """👥 Guruhdan nusxalangan matnli xabar (groups.py)."""
    buttons = [[Button.url("🔗 Guruhda ochish", link)]] if link else None
    parts = split_message(text)
    for part in parts[:-1]:
        await ctx.bot.send_message(ctx.owner_id, part, parse_mode="html", link_preview=False)
    await ctx.bot.send_message(ctx.owner_id, parts[-1], parse_mode="html", buttons=buttons, link_preview=False)


USER_REF_RE = re.compile(r"(?<![\w@.])@([A-Za-z][A-Za-z0-9_]{3,31})\b|(?<![\w@/])\+?\d[\d \-().]{5,22}\d(?!\w)")
# Matn faqat telefon raqami yoki @username (boshqa so'zsiz) — bu eslatma, kirim-chiqim emas
ONLY_REF_RE = re.compile(r"^\s*(?:@[A-Za-z][A-Za-z0-9_]{3,31}|\+?[\d\s\-().]{7,25})\s*$")


async def _dont_understand(event) -> None:
    """Hech bir amalga mos kelmagan matn. Raqam yoki @username bo'lsa — eslatma taklif qilamiz,
    aks holda nima qilish mumkinligini ko'rsatamiz (oldin faqat «Kimga javob yozmoqchisiz?» chiqardi)."""
    text = (event.raw_text or "").strip()
    if USER_REF_RE.search(text) and (req := watchers.parse(text, strict=False)) is not None:
        _watch_pending[str(event.id)] = text
        await event.respond(
            "🤔 Buni eslatma deb tushundim. Qo'yaymi?\n\n" + watchers.describe_request(req),
            parse_mode="html",
            buttons=[[Button.inline("🔔 Ha, eslatma qo'y", f"wch:ok:{event.id}".encode()),
                      Button.inline("❌ Yo'q", b"wch:no")]])
        return
    await event.respond(
        "🤔 Tushunmadim. Men quyidagilarni qila olaman:\n\n"
        "🔔 <b>Eslatma</b> — «@ali online bo'lsa xabar ber», «95 446 68 86 raqami tg ga qo'shilsa ayt» "
        "(yoki 🔔 Eslatmalar → ➕ Yangi eslatma)\n"
        "📥 <b>Instagram'dan yuklash</b> — post, reels yoki storis havolasini yuboring\n"
        "💾 <b>Telegram'dan saqlash</b> — kanal xabarining havolasini yuboring\n"
        "🔎 <b>Qidirish</b> — «Ali yuborgan manzilni top», «wifi parolini qidir»\n"
        "💰 <b>Buxgalter</b> — «nonga 25 ming ishlatdim», «Alidan 200 ming oldim»\n"
        "✍️ <b>Javob yozish</b> — xulosa xabariga <i>reply</i> qiling yoki «✍️ Javob berish» tugmasi\n\n"
        "Yoki pastdagi tugmali menyudan foydalaning (/menu).", parse_mode="html")


async def _send_as_owner(chat_id: int, text: str, event, slot: int = 1) -> None:
    client = ctx.users.get(slot) or (ctx.user if slot == 1 else None)
    if client is None:
        await event.respond(f"❌ {slot}-akkaunt ulanmagan — /tglogin{slot}")
        return
    try:
        # Egasi nomidan yuboriladi; userbot buni "egasi o'zi yozdi" deb ko'radi va AI jim bo'ladi
        await client.send_message(chat_id, text)
        await event.respond(f"✅ Yuborildi. Bu chatda AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi.")
    except Exception as e:
        await event.respond(f"❌ Yuborilmadi: {e}")


async def _save_links(event, links: list) -> None:
    """Havolalardagi mediani egasining Saqlangan xabarlariga joylaydi (jonli ko'rsatkich bilan)."""
    many = len(links) > 1
    done: list[str] = []
    bar = await progress.open_progress(ctx.bot, ctx.owner_id, event, "Saqlanmoqda...")
    async with bar:
        for i, link in enumerate(links, 1):
            prefix = f"{i}/{len(links)} " if many else ""

            async def show(text: str, fraction: float | None = None, got: int = 0,
                           size: int = 0, p=prefix) -> None:
                bar.set(f"{p}{text}", fraction, done=got, total=size)

            await show("Saqlanmoqda...")
            result = await saver.save_link(ctx.user, link, show)
            done.append(f"{prefix}{result}" + (f" — {link.url}" if many else ""))
        await bar.finish("\n".join(done), event, send_long)


async def _resolve_user_id(arg: str) -> int | None:
    arg = arg.strip()
    if arg.lstrip("-").isdigit():
        return int(arg)
    try:
        ent = await ctx.user.get_entity(arg)
        return ent.id
    except Exception:
        return None


# Botga faqat raqam yuborilsa — bu Telegram ID deb qabul qilinadi (summa bilan adashmaslik uchun 6+ xona)
USER_ID_RE = re.compile(r"^\s*(?:id\s*[:=]?\s*)?(\d{6,15})\s*$", re.IGNORECASE)


async def _bot_input_user(uid: int) -> types.InputUser | None:
    """Bot bu odamni taniydimi: InputUser yoki None (bot hech qachon ko'rmagan bo'lsa)."""
    try:
        return utils.get_input_user(await ctx.bot.get_input_entity(uid))
    except Exception:
        return None


def _display_name(ent) -> str | None:
    parts = (getattr(ent, "first_name", None), getattr(ent, "last_name", None))
    return " ".join(p for p in parts if p) or getattr(ent, "title", None)


async def _mention_in_saved(uid: int) -> bool:
    """O'z akkauntingizdan «Saqlangan xabarlar»ga shu odamni mention qilib yozadi.
    Telegram odam ma'lumotini xabar bilan birga barcha qurilmalarga yuboradi — ism ustiga
    bosilsa profili ochiladi (Telegram Desktop'da ham, bot tugmasi ishlamaganda ham)."""
    try:
        ent = await ctx.user.get_entity(uid)  # akkaunt bu odamni taniydimi (aks holda mention o'chib ketadi)
        await ctx.user.send_message(
            "me", f"👤 <a href=\"tg://user?id={uid}\">{html.escape(_display_name(ent) or str(uid))}</a> "
                  f"— ism ustiga bosing, lichkasi ochiladi\n🆔 <code>{uid}</code>",
            parse_mode="html")
        return True
    except Exception:
        log.exception("Saqlangan xabarlarga mention yuborib bo'lmadi: %s", uid)
        return False


async def _open_dm(event, arg: str) -> None:
    """ID yoki @username bo'yicha o'sha odamning lichkasiga o'tadigan tugma yuboradi."""
    arg = arg.strip()
    uid = await _resolve_user_id(arg)
    if uid is None:
        await event.respond("❌ Foydalanuvchi topilmadi. ID raqam yoki @username yuboring.")
        return
    name, username, known = None, None, False
    try:
        ent = await ctx.user.get_entity(uid)
        known = True
        name = _display_name(ent)
        username = getattr(ent, "username", None)
    except Exception:
        pass  # akkaunt bu ID ni hali "tanimaydi" — baribir tugma bilan urinib ko'ramiz

    if username:
        link = f"https://t.me/{username}"
        variants = [Button.url("💬 Lichkaga o'tish", link)]
    else:
        # Oddiy «tg://user?id=» URL tugmasi faqat qurilma bu odamni oldindan «tanisa» ochiladi.
        # Profil tugmasida Telegram odam ma'lumotini xabarga qo'shib yuboradi — lekin buning
        # uchun botning o'zi odamni tanishi kerak. Avval profil tugmasi, bo'lmasa oddiy havola.
        link = f"tg://user?id={uid}"
        variants = []
        if (bot_user := await _bot_input_user(uid)) is not None:
            variants.append(types.KeyboardInlineButton(
                "💬 Lichkaga o'tish", types.InputInlineButtonTypeUserProfile(bot_user)))
        variants.append(Button.url("💬 Lichkaga o'tish", link))
    # Telegram Desktop tugmani faqat odam kompyuter keshida bo'lsa ochadi —
    # shunda o'z akkauntdan «Saqlangan xabarlar»ga mention yuborish yo'li qoladi
    extra = ([[Button.inline("🖥 Kompyuterda ochilmasa", f"dmsave:{uid}".encode())]]
             if not username and known else [])
    text = (f"👤 <a href=\"{link}\">{html.escape(name or 'Foydalanuvchi')}</a>"
            + (f" (@{html.escape(username)})" if username else "")
            + f"\n🆔 <code>{uid}</code>")
    for button in variants:
        try:
            await event.respond(text, parse_mode="html", buttons=[[button]] + extra)
            return
        except Exception as e:
            # Masalan, odam maxfiylik sozlamasida havolani yopgan bo'lsa Telegram tugmani rad etadi
            log.warning("Lichka tugmasini yaratib bo'lmadi (%s): %s", uid, e)
    if known and await _mention_in_saved(uid):
        await event.respond(
            text + "\n\n⚠️ Bot tugma yarata olmadi. "
                   "«Saqlangan xabarlar»ga havola yubordim — o'sha yerda ism ustiga bosing.",
            parse_mode="html")
    else:
        await event.respond(
            text + "\n\n⚠️ Tugma yaratib bo'lmadi: bu odam maxfiylik sozlamasida profilini yopgan "
                   "yoki akkauntingiz u bilan hech qachon yozishmagan.",
            parse_mode="html")


# ---------- "/" menyusi ----------
# (buyruq, tavsif) — Telegram'da "/" bosilganda shu ro'yxat chiqadi
BOT_COMMANDS = [
    ("status", "📊 Holat va boshqaruv paneli"),
    ("auto", "🤖 Aqlli rejim — faqat band bo'lganda javob"),
    ("on", "🟢 Kotiba doim javob beradi"),
    ("off", "⚪️ Avtojavobni o'chirish"),
    ("inbox", "📬 Javob kutayotgan xabarlar"),
    ("report", "📈 Bugungi hisobot"),
    ("fin", "💰 Moliya paneli: kirim-chiqim hisobotlari"),
    ("today", "📒 Bugungi kirim-chiqim"),
    ("days3", "📒 3 kunlik moliya hisoboti"),
    ("week", "📒 Haftalik moliya hisoboti"),
    ("month", "📒 Oylik moliya hisoboti"),
    ("balance", "💼 Umumiy balans va qarzlar"),
    ("history", "🧾 Oxirgi kirim-chiqim yozuvlari"),
    ("undo", "↩️ Oxirgi moliya yozuvini bekor qilish"),
    ("budget", "🎯 Oylik byudjet: /budget oziq-ovqat 2000000"),
    ("export", "📤 Kirim-chiqimni Excel (CSV) faylga"),
    ("unmute", "🔊 Chatda kotibani qayta yoqish: /unmute id"),
    ("menu", "📋 Tugmali menyuni ochish"),
    ("arxiv", "🗂 Chat arxivi: o'chirilgan yozishmani HTML qilib olish"),
    ("deleted", "👻 Akkauntini o'chirganlar — taxminiy ismi bilan"),
    ("xulosa", "🧠 Yozishma xulosasi va va'dalar: /xulosa Ali"),
    ("vadalar", "📌 Bergan va'dalaringiz ro'yxati"),
    ("eslatma", "🔔 Eslatmalar: raqam tg ga qo'shilsa / odam online bo'lsa"),
    ("ig", "📸 Instagram: bir martalik media, avtojavob, arxiv"),
    ("tglogin", "🔑 Telegram akkaunt: holati va boshqasini ulash"),
    ("tglogin2", "📱 2-Telegram akkauntni ham ulash"),
    ("iglogin", "🔑 Instagram'ni ulash: /iglogin username parol"),
    ("drive", "☁️ Google Drive holati va qayta ulash"),
    ("guruhlar","👥 Guruhlar: tanlangan guruh xabarlarini botga nusxalash"),
    ("igyukla", "📥 Instagram post/reels/storis mediasini yuklash"),
    ("find", "🔎 Chatlardan istalgan narsani topish"),
    ("save", "💾 Havoladagi mediani Saqlangan xabarlarga joylash"),
    ("bio", "🕒 Bio, iqtibos va online — tugmali panel"),
    ("quote", "💬 Iqtibos: /quote matn | auto | off"),
    ("online", "🟢 Online ko'rinish: on | off"),
    ("yangixabar", "📩 «Yangi xabar» bildirishnomalari: on | off"),
    ("usage", "💸 Bugungi AI sarfi va limitlar"),
    ("health", "🩺 MongoDB, OpenAI, Telegram holati"),
    ("tags", "🏷 Kontakt turlari ro'yxati"),
    ("tag", "🏷 Tur berish: /tag @user oila|mijoz|dost"),
    ("reply", "✍️ Javob: /reply chat_id matn"),
    ("lichka", "👤 Lichkaga o'tish: /lichka id yoki @username"),
    ("ignored", "🚫 Istisnolar ro'yxati"),
    ("ignore", "🚫 Istisno: /ignore @user"),
    ("unignore", "↩️ Istisnodan chiqarish: /unignore id"),
    ("help", "❓ Barcha buyruqlar"),
]


async def set_commands(bot: TelegramClient, owner_id: int) -> None:
    """Buyruqlar menyusini o'rnatadi. Menyu faqat egasiga ko'rinadi (begonalar uchun bo'sh)."""
    from telethon.tl.functions.bots import SetBotCommandsRequest
    from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer

    commands = [BotCommand(command=c, description=d) for c, d in BOT_COMMANDS]
    try:
        peer = await bot.get_input_entity(owner_id)
        await bot(SetBotCommandsRequest(scope=BotCommandScopePeer(peer=peer), lang_code="", commands=commands))
        await bot(SetBotCommandsRequest(scope=BotCommandScopeDefault(), lang_code="", commands=[]))
        log.info("Bot menyusi o'rnatildi (%d ta buyruq, faqat egasiga ko'rinadi)", len(commands))
    except Exception as e:
        # Egasi hali botga /start bosmagan bo'lsa — hamma uchun o'rnatamiz (begonalar baribir rad etiladi)
        log.info("Menyuni faqat egasiga o'rnatib bo'lmadi (%s) — umumiy menyu o'rnatiladi", type(e).__name__)
        try:
            await bot(SetBotCommandsRequest(scope=BotCommandScopeDefault(), lang_code="", commands=commands))
        except Exception as e2:
            log.warning("Bot menyusi o'rnatilmadi (%s)", type(e2).__name__)


# ---------- boshqaruv paneli (tugmalar) ----------
async def _do_action(action: str) -> str:
    """Panel tugmasi yoki buyruq bajaradigan amal. Natija matnini qaytaradi."""
    upd = bio_updater.INSTANCE
    if action.startswith("mode_"):
        mode = action[5:]
        if mode in storage.MODES:
            await storage.set_mode(mode)
            return {"auto": "🤖 Aqlli rejim yoqildi", "on": "🟢 Kotiba doim javob beradi",
                    "off": "⚪️ Avtojavob o'chirildi"}[mode]
    if action == "bio_on":
        await runtime.update(bio_enabled=True)
        return "🟢 Bio yangilash yoqildi"
    if action == "bio_off":
        await runtime.update(bio_enabled=False)
        return "⏸ Bio yangilash to'xtatildi (bio oxirgi holatida qoladi)"
    if action in ("bio_now", "bio_restore") and upd is None:
        return "Bio moduli ishlamayapti (BIO_ENABLED=false)"
    if action == "bio_now":
        await runtime.update()  # siklni uyg'otadi
        return "🔄 Bio hozir yangilanadi"
    if action == "bio_restore":
        await runtime.update(bio_enabled=False)
        await upd.restore()
        original = await bio_updater.get_original_bio()
        return f"↩️ Asl bio qaytarildi: {original!r}. Avtomatik yangilash to'xtatildi"
    if action == "quote_auto":
        await runtime.update(quote_mode="auto")
        return "🔁 Iqtibos har kuni quotes.txt dan almashadi"
    if action == "quote_off":
        await runtime.update(quote_mode="off")
        return "🚫 Bio iqtibossiz chiqadi"
    if action in ("online_on", "online_off"):
        on = action == "online_on"
        await runtime.update(online=on)
        return "🟢 Akkaunt online ko'rinadi" if on else "⚪️ Online ko'rsatish o'chirildi"
    if action == "refresh":
        return "🔄 Yangilandi"
    return "Noma'lum amal"


async def _panel() -> tuple[str, list]:
    mode = await storage.get_mode()
    st = await availability.get_status()
    upd = bio_updater.INSTANCE
    bio_on, online = runtime.get("bio_enabled"), runtime.get("online")
    qmode = runtime.get("quote_mode")
    qtext = {"auto": "har kuni almashadi", "off": "o'chirilgan",
             "fixed": f"“{html.escape(runtime.get('quote_text') or '')}”"}.get(qmode, qmode)
    current = html.escape(upd.current_bio) if upd and upd.current_bio else "—"
    text = (
        "⚙️ <b>Boshqaruv paneli</b>\n\n"
        f"Kotiba: {MODE_NAMES[mode]}\n"
        f"Hozir: {'🟢 javob beradi' if st.reply else '⚪️ jim'} — {html.escape(st.reason)}\n\n"
        f"Bio: {'🟢 yangilanmoqda' if bio_on else '⏸ to‘xtatilgan'}"
        f"{' (DRY_RUN — Telegram’da o‘zgarmaydi)' if config.DRY_RUN else ''}\n"
        f"<code>{current}</code>\n"
        f"Iqtibos: {qtext}\n"
        f"Online: {'🟢 yoqiq' if online else '⚪️ o‘chiq'}"
    )
    tick = lambda cond, label: ("✅ " if cond else "") + label  # noqa: E731
    buttons = [
        [Button.inline(tick(mode == "auto", "🤖 Aqlli"), b"cfg:mode_auto"),
         Button.inline(tick(mode == "on", "🟢 Doim"), b"cfg:mode_on"),
         Button.inline(tick(mode == "off", "⚪️ O'chiq"), b"cfg:mode_off")],
        [Button.inline("⏸ Bio to'xtatish" if bio_on else "▶️ Bio yoqish",
                       b"cfg:bio_off" if bio_on else b"cfg:bio_on"),
         Button.inline("🔄 Hozir yangilash", b"cfg:bio_now")],
        [Button.inline(tick(qmode == "auto", "🔁 Iqtibos: har kuni"), b"cfg:quote_auto"),
         Button.inline(tick(qmode == "off", "🚫 Iqtibossiz"), b"cfg:quote_off")],
        [Button.inline("⚪️ Online o'chirish" if online else "🟢 Online yoqish",
                       b"cfg:online_off" if online else b"cfg:online_on"),
         Button.inline("↩️ Asl bio", b"cfg:bio_restore")],
        [Button.inline("🔃 Yangilash", b"cfg:refresh")],
    ]
    return text, buttons


async def _send_panel(event, extra: str = "") -> None:
    text, buttons = await _panel()
    if extra:
        text += f"\n\n{extra}"
    await event.respond(text, parse_mode="html", buttons=buttons)


# ---------- chat arxivi ----------
ARCHIVE_PAGE = 8
ARCHIVE_PREVIEW = 6


def _when(ts: float | None) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo(config.TIMEZONE)).strftime("%d.%m %H:%M") if ts else ""


def _html_buttons(chat_id: int) -> list:
    return [[Button.inline("📄 Butun yozishma (HTML)", f"arc:html:{chat_id}:a".encode()),
             Button.inline("🗑 Faqat o'chirilganlar", f"arc:html:{chat_id}:d".encode())]]


async def deleted_alert(chat_id: int, docs: list[dict]) -> None:
    """Arxivdagi xabarlar o'chirildi — egasiga nechta/nima o'chganini va yuklab olish tugmalarini yuboradi."""
    info = await storage.archive_chat(chat_id) or {}
    theirs = sum(1 for d in docs if not d.get("out"))
    mine = len(docs) - theirs
    head = "🗑 <b>Yozishma o'chirildi</b>" if len(docs) >= 10 else "🗑 <b>Xabar o'chirildi</b>"
    lines = [head, f"👤 {_who(archive.chat_label({'chat_id': chat_id, **info}), info.get('username'), chat_id)}",
             f"{len(docs)} ta xabar (uniki: {theirs}, sizniki: {mine})", ""]
    for d in docs[-ARCHIVE_PREVIEW:]:
        body = (d.get("text") or "").strip() or f"[{d.get('media') or 'xabar'}]"
        if d.get("media") and d.get("text"):
            body = f"[{d['media']}] {body}"
        arrow = "📤" if d.get("out") else "📥"
        link = d.get("drive_media_link")
        drive = f' <a href="{html.escape(link)}">☁️ Drive</a>' if link else ""
        lines.append(f"{arrow} <i>{_when(d.get('date'))}</i> {html.escape(body[:300])}{drive}")
    if len(docs) > ARCHIVE_PREVIEW:
        lines.append(f"… yana {len(docs) - ARCHIVE_PREVIEW} ta — HTML faylda")
    lines.append("\n💾 Hammasi arxivda saqlangan.")
    try:
        msg = await ctx.bot.send_message(ctx.owner_id, "\n".join(lines), parse_mode="html",
                                         buttons=_html_buttons(chat_id))
    except Exception:
        log.exception("O'chirish haqida ogohlantirish yuborilmadi")
        return
    if not gdrive.enabled():
        return
    try:  # Drive'dagi nusxani darhol yangilab, havolasini tugma qilib qo'shamiz
        link = await archive.drive_sync_chat(chat_id)
        await msg.edit(buttons=_html_buttons(chat_id) + [[Button.url("☁️ Google Drive'da ochish", link)]])
    except Exception as e:
        log.warning("O'chirilgan chat Drive'ga yuklanmadi (%s)", gdrive.explain(e))


async def _archive_panel() -> tuple[str, list]:
    try:
        st = await storage.archive_stats()
    except Exception as e:
        log.warning("Arxiv statistikasi olinmadi (%s)", type(e).__name__)
        st = {}
    queue = ""
    if gdrive.enabled():
        try:
            mc = await storage.archive_media_counts()
            waiting = mc.get("pending", 0) + mc.get("history", 0)
            skipped = mc.get("too_big", 0) + mc.get("failed", 0) + mc.get("lost", 0)
            queue = (f"\n🖼 Drive'dagi media: {mc.get('done', 0)} ta yuklangan, {waiting} ta navbatda"
                     + (f", {skipped} ta yuklanmadi (katta/o'chirilgan/xato)" if skipped else ""))
        except Exception as e:
            log.warning("Media navbati olinmadi (%s)", type(e).__name__)
    on, groups, alerts = (runtime.get("archive_enabled"), runtime.get("archive_groups"),
                          runtime.get("archive_alerts"))
    text = (
        "🗂 <b>Chat arxivi</b>\n\n"
        f"Arxiv: {'🟢 yoqiq' if on else '⚪️ o‘chiq'} — barcha shaxsiy chatlar (istisnodagilar ham)\n"
        f"Guruhlar: {'🟢 arxivlanadi' if groups else '⚪️ arxivlanmaydi'}\n"
        f"O'chirilganda xabar berish: {'🟢 yoqiq' if alerts else '⚪️ o‘chiq'}\n\n"
        f"💾 Saqlangan: {st.get('messages', '?')} ta xabar, {st.get('chats', '?')} ta chat\n"
        f"🗑 O'chirilganlari: {st.get('deleted', '?')} ta\n"
        f"{_drive_line()}{queue}\n\n"
        "Chatni tanlang — yozishmani HTML fayl qilib yuboraman. Nomi bo'yicha topish: "
        "<code>/arxiv Ali</code>"
    )
    buttons = [
        [Button.inline("💬 Chatlar", b"arc:list:0"), Button.inline("🗑 O'chirilgan bor chatlar", b"arc:dlist:0")],
        [Button.inline("👻 O'chirilgan akkauntlar", b"arc:gone:0")],
        [Button.inline("⏸ Arxivni to'xtatish" if on else "▶️ Arxivni yoqish", b"arc:set:archive_enabled"),
         Button.inline("🔕 Xabar bermasin" if alerts else "🔔 Xabar bersin", b"arc:set:archive_alerts")],
        [Button.inline("⚪️ Guruhlarsiz" if groups else "👥 Guruhlar ham", b"arc:set:archive_groups")],
    ]
    if gdrive.enabled():
        buttons[-1].append(Button.inline("☁️ Drive'ga hozir yuklash", b"arc:drive"))
        media_on = runtime.get("drive_media")
        buttons.append([Button.inline("⏸ Rasm/video Drive'ga — to'xtatish" if media_on
                                      else "🖼 Rasm/videoni Drive'ga yuklash", b"arc:set:drive_media")])
    buttons.append([Button.inline("⚙️ Kuzatuv sozlamalari", b"arc:watch"),
                    Button.inline("📌 Va'dalar", b"arc:promises")])
    return text, buttons


def _drive_line() -> str:
    if not gdrive.enabled():
        return "☁️ Google Drive: ⚪️ sozlanmagan (" + ", ".join(gdrive.missing()) + ")"
    st = archive.drive_state
    if st["ok"] is False:
        return f"☁️ Google Drive: ❌ {html.escape(st['problem'])}"
    when = f", oxirgi: {_when(st['last'])}" if st["last"] else ""
    line = f"☁️ Google Drive: 🟢 har {config.DRIVE_SYNC_MINUTES} daqiqada yuklanadi{when}"
    ms = archive.media_state
    if not runtime.get("drive_media"):
        line += "\n🖼 Rasm/video Drive'ga: ⚪️ o'chiq"
    elif ms["ok"] is False:
        line += f"\n🖼 Rasm/video Drive'ga: ❌ {html.escape(ms['problem'])}"
    else:
        line += f"\n🖼 Rasm/video Drive'ga: 🟢 kelishi bilan yuklanadi (shu ishga tushishda: {ms['done']} ta)"
    return line


async def _archive_list(page: int, only_deleted: bool, name: str = "") -> tuple[str, list]:
    rows = await storage.archive_chats(page * ARCHIVE_PAGE, ARCHIVE_PAGE + 1, only_deleted, name)
    more, rows = len(rows) > ARCHIVE_PAGE, rows[:ARCHIVE_PAGE]
    back = [Button.inline("⬅️ Arxiv", b"arc:panel")]
    if not rows:
        if name:
            return f"🤷 Arxivda «{html.escape(name)}» nomli chat topilmadi.", [back]
        return ("🗑 Hali hech kim xabar o'chirmagan." if only_deleted else
                "💬 Arxiv hozircha bo'sh — yangi xabarlar kelgan sari to'ladi."), [back]
    buttons = []
    for r in rows:
        label = ("👻 " if r.get("account_deleted") else "") + archive.chat_label(r)[:28]
        label += f" · 🗑{r['deleted']}" if r.get("deleted") else f" · {r.get('count', 0)}"
        buttons.append([Button.inline(label, f"arc:chat:{r['chat_id']}".encode())])
    kind = "dlist" if only_deleted else "list"
    nav = []
    if page > 0 and not name:
        nav.append(Button.inline("◀️", f"arc:{kind}:{page - 1}".encode()))
    nav += back
    if more and not name:
        nav.append(Button.inline("▶️", f"arc:{kind}:{page + 1}".encode()))
    buttons.append(nav)
    title = "🗑 <b>O'chirilgan xabarlari bor chatlar</b>" if only_deleted else "💬 <b>Arxivdagi chatlar</b>"
    return f"{title}\nChatni tanlang:", buttons


async def _archive_chat_view(chat_id: int) -> tuple[str, list]:
    info = await storage.archive_chat(chat_id) or {}
    gone = ""
    if info.get("account_deleted"):
        gone = (f"👻 <b>Akkaunt o'chirilgan</b> ({_when(info.get('account_deleted_at')) or '—'} da aniqlandi)\n"
                f"Ism: {html.escape(deleted_accounts.describe({'chat_id': chat_id, **info}))}\n")
    text = (f"💬 {_who(archive.chat_label({'chat_id': chat_id, **info}), info.get('username'), chat_id)}\n{gone}"
            f"💾 Arxivda: {info.get('count', 0)} ta xabar · 🗑 o'chirilgan: {info.get('deleted', 0)}\n"
            f"Oxirgi xabar: {_when(info.get('last')) or '—'}\n\n"
            "«Butun yozishma» ga Telegram'da hali turgan xabarlar ham qo'shiladi.")
    buttons = _html_buttons(chat_id) + _summary_buttons(chat_id)
    if info.get("drive_link"):
        buttons.append([Button.url("☁️ Google Drive'da ochish", info["drive_link"])])
    return text, buttons + [[Button.inline("⬅️ Chatlar", b"arc:list:0")]]


GONE_PAGE = 15


async def _gone_filter(rows: list[dict], text: str) -> tuple[list[dict], str]:
    """So'rovda ism bo'lsa — ismi mos kelganlari; bo'lmasa so'zlar yozishmada uchraganlari («parol haqida
    gaplashgan o'chirilgan akkaunt»). Hech biri topilmasa — hammasi. (qatorlar, izoh)."""
    name = deleted_accounts.name_words(text)
    if not name or not rows:
        return rows, ""
    hits = [r for r in rows if max(finder.score_name(name, r.get("title") or ""),
                                   finder.score_name(name, r.get("guess_title") or "")) > 0]
    if hits:
        return hits, f"«{name}» ismiga mos"
    words = [finder.stem(w) for w in name.split()]
    try:
        counts = await storage.archive_word_hits([r["chat_id"] for r in rows], words)
    except Exception as e:
        log.warning("O'chirilgan akkauntlar yozishmasidan qidirilmadi (%s)", type(e).__name__)
        counts = {}
    if counts:
        found = sorted((r for r in rows if r["chat_id"] in counts), key=lambda r: -counts[r["chat_id"]])
        return found, f"yozishmasida «{name}» uchraydiganlar"
    return rows, f"«{name}» bo'yicha mos kelgani yo'q — hammasi"


async def _gone_list(page: int = 0, text: str = "") -> tuple[str, list]:
    rows, note = await _gone_filter(await storage.archive_deleted_accounts(), text)
    back = [Button.inline("🔄 Qayta tekshirish", b"arc:gonescan"), Button.inline("⬅️ Arxiv", b"arc:panel")]
    if not rows:
        return ("👻 Akkauntini o'chirgan suhbatdosh topilmadi.\n"
                "<i>«🔄 Qayta tekshirish» — barcha chatlar Telegram'dan qaytadan ko'rib chiqiladi.</i>"), [back]
    chunk = rows[page * GONE_PAGE:(page + 1) * GONE_PAGE]
    lines = [f"👻 <b>O'chirilgan akkauntlar</b> — {len(rows)} ta"
             + (f" <i>({html.escape(note)})</i>" if note else ""), ""]
    buttons = []
    for i, r in enumerate(chunk, page * GONE_PAGE + 1):
        about = deleted_accounts.describe(r)
        lines.append(f"{i}. <b>{html.escape(archive.chat_label(r))}</b>  <code>{r['chat_id']}</code>\n"
                     f"   {html.escape(about[:140])}\n"
                     f"   💾 {r.get('count', 0)} ta xabar · oxirgi: {_when(r.get('last')) or '—'}")
        buttons.append([Button.inline(f"{i}. {archive.chat_label(r)[:34]}", f"arc:chat:{r['chat_id']}".encode())])
    lines.append("\n<i>≈ — ism yozishmadan taxmin qilingan (siz uni qanday chaqirganingizga qarab). "
                 "Chatni tanlang — yozishmani HTML qilib olasiz.</i>")
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️", f"arc:gone:{page - 1}".encode()))
    if (page + 1) * GONE_PAGE < len(rows):
        nav.append(Button.inline("▶️", f"arc:gone:{page + 1}".encode()))
    if nav:
        buttons.append(nav)
    buttons.append(back)
    return "\n".join(lines), buttons


async def _show_gone(event, text: str = "") -> None:
    body, buttons = await _gone_list(0, text)
    await event.respond(body, parse_mode="html", buttons=buttons)


async def _gone_scan() -> None:
    status = await ctx.bot.send_message(ctx.owner_id, "🔎 Chatlar ko'rib chiqilyapti — o'chirilgan akkauntlar "
                                                      "qidirilmoqda, yozishmalar arxivga olinmoqda...")
    try:
        found, added, named = await deleted_accounts.refresh(ctx.user, await _ai_ok())
    except Exception as e:
        log.exception("O'chirilgan akkauntlar tekshirilmadi")
        await status.edit(f"❌ Tekshirib bo'lmadi: {type(e).__name__}")
        return
    body, buttons = await _gone_list(0)
    head = (f"✅ Tekshirildi: {found} ta o'chirilgan akkaunt, arxivga {added} ta yangi xabar"
            + (f", {named} tasiga ism topildi" if named else "") + ".\n\n")
    await status.edit(head + body, parse_mode="html", buttons=buttons)


async def _send_archive_html(chat_id: int, only_deleted: bool) -> None:
    status = await ctx.bot.send_message(ctx.owner_id, "⏳ Yozishma tayyorlanmoqda...")
    try:
        data, fname, n = await archive.export_html(ctx.user, chat_id, only_deleted)
        if not n:
            await status.edit("🗑 Bu chatda o'chirilgan xabar topilmadi." if only_deleted else
                              "🤷 Bu chat bo'yicha arxivda ham, Telegram'da ham xabar topilmadi.")
            return
        f = io.BytesIO(data)
        f.name = fname
        what = "🗑 O'chirilgan xabarlar" if only_deleted else "📄 Yozishma"
        await ctx.bot.send_file(ctx.owner_id, f, force_document=True,
                                caption=f"{what}: {n} ta xabar\nFaylni oching — brauzerda chat ko'rinishida chiqadi.")
        await status.delete()
    except Exception as e:
        log.exception("Arxiv HTML tayyorlanmadi")
        await status.edit(f"❌ Tayyorlab bo'lmadi: {type(e).__name__}")


# ---------- kuzatuv: tahrir ----------
async def edited_alert(chat_id: int, old: dict, new_text: str) -> None:
    """Suhbatdosh xabarini tahrirladi — avvalgi va yangi matni bilan."""
    info = await storage.archive_chat(chat_id) or {}
    before = (old.get("text") or "").strip() or "(bo'sh)"
    after = new_text.strip() or "(bo'sh — matnini o'chirdi)"
    body = (f"✏️ <b>Xabar tahrirlandi</b>\n👤 {_who(info.get('title') or '', info.get('username'), chat_id)}\n"
            f"🕒 Yozilgan: {_when(old.get('date'))}\n\n"
            f"<b>Avval:</b> <i>{html.escape(before[:600])}</i>\n"
            f"<b>Endi:</b> <i>{html.escape(after[:600])}</i>")
    try:
        await ctx.bot.send_message(ctx.owner_id, body, parse_mode="html", buttons=_html_buttons(chat_id)[:1])
    except Exception:
        log.exception("Tahrir haqida ogohlantirish yuborilmadi")


# ---------- AI chat xulosasi ----------
SUMMARY_DAYS = {"7": 7, "30": 30, "0": None}


def _summary_text(info: dict, days: int | None, n: int, res: dict, added: list[dict]) -> str:
    period = f"oxirgi {days} kun" if days else "butun arxiv"
    lines = [f"🧠 <b>Xulosa</b> — {_who(info.get('title') or '', info.get('username'), info.get('chat_id', 0))}",
             f"<i>{period}, {n} ta xabar</i>", "", html.escape(res.get("summary") or "—")]
    if res.get("topics"):
        lines += ["", "🏷 <b>Mavzular:</b> " + html.escape(", ".join(res["topics"]))]
    mine = [p for p in res.get("promises") or [] if p["who"] == "men"]
    theirs = [p for p in res.get("promises") or [] if p["who"] != "men"]
    if mine:
        lines += ["", "📌 <b>Sizning va'dalaringiz:</b>"]
        lines += [f"• {html.escape(p['text'])}" + (f" — <b>{p['due']}</b>" if p.get("due") else "") for p in mine]
    if theirs:
        lines += ["", "🤝 <b>Uning va'dalari:</b>"]
        lines += [f"• {html.escape(p['text'])}" + (f" — <b>{p['due']}</b>" if p.get("due") else "") for p in theirs]
    if res.get("open_questions"):
        lines += ["", "❓ <b>Ochiq qolgan masalalar:</b>"]
        lines += [f"• {html.escape(q)}" for q in res["open_questions"]]
    if added:
        lines += ["", f"✅ {len(added)} ta va'dangiz ro'yxatga qo'shildi (/vadalar) — muddati kelganda eslataman."]
    return "\n".join(lines)


async def _send_summary(chat_id: int, days: int | None) -> None:
    status = await ctx.bot.send_message(ctx.owner_id, "🧠 Yozishmani o'qiyapman...")
    try:
        res, info, n = await promises.summarize(chat_id, days)
        if not res:
            await status.edit("🤷 Bu davrda arxivda yozishma yo'q.")
            return
        added = await promises.save_promises(chat_id, info.get("title") or "", res["promises"], "summary")
        await status.edit(_summary_text({**info, "chat_id": chat_id}, days, n, res, added), parse_mode="html",
                          buttons=_html_buttons(chat_id)[:1])
    except promises.NoAI as e:
        await status.edit(f"⚠️ AI hozir ishlamaydi: {e}")
    except Exception as e:
        log.exception("Xulosa tayyorlanmadi")
        await status.edit(f"❌ Xulosa tayyorlanmadi: {type(e).__name__}")


def _summary_buttons(chat_id: int) -> list:
    return [[Button.inline("🧠 Xulosa: 7 kun", f"arc:sum:{chat_id}:7".encode()),
             Button.inline("🧠 30 kun", f"arc:sum:{chat_id}:30".encode()),
             Button.inline("🧠 Hammasi", f"arc:sum:{chat_id}:0".encode())]]


# ---------- va'dalar ----------
def _promise_line(p: dict, today: str) -> str:
    due = p.get("due")
    mark = ""
    if due:
        mark = " 🔴 <b>muddati o'tgan</b>" if due < today else (" 🟠 <b>bugun</b>" if due == today else "")
        due = f" — {due}"
    return f"📌 <b>{html.escape(p.get('title') or '')}</b>: {html.escape(p['text'])}{due or ''}{mark}"


def _promise_buttons(pid: str) -> list:
    return [[Button.inline("✅ Bajarildi", f"prm:done:{pid}".encode()),
             Button.inline("⏰ Ertaga", f"prm:later:{pid}".encode()),
             Button.inline("🗑", f"prm:del:{pid}".encode())]]


async def send_promises(rows: list[dict], header: str) -> None:
    """Va'dalar ro'yxati: har biri alohida xabar (o'z tugmalari bilan)."""
    today = promises.today()
    await ctx.bot.send_message(ctx.owner_id, header, parse_mode="html")
    for p in rows[:20]:
        await ctx.bot.send_message(ctx.owner_id, _promise_line(p, today), parse_mode="html",
                                   buttons=_promise_buttons(p["id"]))


async def _show_promises(event) -> None:
    rows = await storage.promise_list()
    if not rows:
        await event.respond("📌 Ochiq va'dalar yo'q.\nChat xulosasini oling (🗂 Chat arxivi → chat → 🧠 Xulosa) — "
                            "u yerdagi va'dalaringiz shu ro'yxatga tushadi. Har kechqurun bugungi yozishmalar "
                            "ham avtomatik tekshiriladi.")
        return
    await send_promises(rows, f"📌 <b>Ochiq va'dalaringiz</b> ({len(rows)} ta)")


# ---------- haftalik hisobot ----------
async def send_weekly(text: str, chat_ids: list[int]) -> None:
    buttons = [[Button.inline(f"✍️ Javob: {i + 1}", f"reply:{cid}".encode())] for i, cid in enumerate(chat_ids)]
    await ctx.bot.send_message(ctx.owner_id, text, parse_mode="html", buttons=buttons or None)


# ---------- kuzatuv sozlamalari ----------
WATCH_KEYS = {
    "edit_alerts": "✏️ Tahrir ogohlantirishi",
    "promise_scan": "📌 Va'dalarni har kech topish (AI)",
    "weekly_report": "📊 Haftalik chat hisoboti",
    "drive_backup": "💾 Bazani har kuni Drive'ga zaxiralash",
}


def _watch_panel() -> tuple[str, list]:
    lines = ["⚙️ <b>Kuzatuv sozlamalari</b>", ""]
    buttons = []
    for key, label in WATCH_KEYS.items():
        on = bool(runtime.get(key))
        lines.append(f"{'🟢' if on else '⚪️'} {label}")
        buttons.append([Button.inline(("✅ " if on else "▫️ ") + label, f"arc:wset:{key}".encode())])
    if not gdrive.enabled():
        lines += ["", "<i>Profil rasmlari va zaxira Google Drive ulanganda ishlaydi.</i>"]
    buttons.append([Button.inline("💾 Hozir zaxiralash", b"arc:backup"),
                    Button.inline("📊 Hisobot hozir", b"arc:weekly")])
    buttons.append([Button.inline("⬅️ Arxiv", b"arc:panel")])
    return "\n".join(lines), buttons


async def _backup_now() -> None:
    status = await ctx.bot.send_message(ctx.owner_id, "💾 Baza Drive'ga zaxiralanmoqda...")
    try:
        res = await backup.backup_now()
        await status.edit(backup.describe(res), buttons=[[Button.url("☁️ Drive'da ochish", res["link"])]])
    except Exception as e:
        log.exception("Zaxira olinmadi")
        await status.edit(f"❌ Zaxira olinmadi: {gdrive.explain(e)}")


# ---------- shaxsiy buxgalter ----------
FIN_PERIOD_CMDS = {"today": "today", "days3": "3", "week": "7", "month": "month"}


async def _fin_parse(text: str) -> dict:
    """AI bilan tahlil; kunlik AI limiti tugagan yoki AI ishlamasa — taxminiy (qoidalar bilan)."""
    try:
        st = await storage.get_stats()
        if config.AI_DAILY_LIMIT and st.get("ai_requests", 0) >= config.AI_DAILY_LIMIT:
            return finance.local_parse(text)
    except Exception:
        pass
    try:
        res = await finance.parse(text)
    except Exception as e:
        log.warning("Moliya: AI tahlil qila olmadi (%s) — taxminiy tahlil", type(e).__name__)
        return finance.local_parse(text)
    try:
        await storage.stat_inc(ai_requests=1, ai_finance=1, ai_tokens=res.get("tokens", 0))
    except Exception:
        pass
    return res


def _fin_confirm(docs: list[dict], ids: list[str], batch: str, today_rows: list[dict], ai_used: bool,
                 alerts: list[str]) -> tuple[str, list]:
    lines = ["✅ <b>Yozildi:</b>"]
    today = finance.day_key(finance.now().date())
    for d in docs:
        when = "" if d["day"] == today else f" <i>({d['day']})</i>"
        cat = f" · {d['category']}" if d["type"] in ("expense", "income") else ""
        lines.append(f"{finance.row_line(d)}{cat}{when}")
    s = finance.summarize(today_rows)
    lines += ["", f"📒 Bugun: 🔻 {finance.fmt_sum(s['expense'])} | 🟢 {finance.fmt_sum(s['income'])}"]
    if not ai_used:
        lines.append("⚠️ AI'siz taxminiy tahlil — tekshirib qo'ying (✏️ / ↩️).")
    lines += alerts
    edit = [Button.inline(f"✏️ {i + 1}" if len(ids) > 1 else "✏️ Kategoriya", f"fin:cat:{tx}".encode())
            for i, (tx, d) in enumerate(zip(ids, docs)) if d["type"] in ("expense", "income")][:6]
    buttons = [[Button.inline("↩️ Bekor qilish", f"fin:undo:{batch}".encode()),
                Button.inline("📒 Bugun", b"fin:rep:today")]]
    if edit:
        buttons.insert(0, edit)
    return "\n".join(lines), buttons


async def _fin_record(event, text: str, source: str) -> bool:
    """Matnni kirim-chiqim sifatida yozishga urinadi. Moliyaga oid bo'lmasa — False."""
    if not finance.looks_financial(text):
        return False
    res = await _fin_parse(text)
    if not res["finance"]:
        return False
    if not res["items"]:
        await event.respond("🤔 Pul haqida ekanini tushundim, lekin summani aniqlay olmadim. "
                            "Masalan: «nonga 25 ming ishlatdim».")
        return True
    docs = finance.make_docs(res["items"], source, text)
    today = finance.now().date()
    month_start = finance.day_key(today.replace(day=1))
    budget = await storage.get_budget()
    before = await storage.list_transactions(month_start) if budget else []
    batch, ids = await storage.add_transactions(docs)
    alerts = []
    if budget:
        alerts = finance.budget_alerts(budget, before, await storage.list_transactions(month_start))
    today_rows = await storage.list_transactions(finance.day_key(today), finance.day_key(today))
    body, buttons = _fin_confirm(docs, ids, batch, today_rows, res.get("ai", True), alerts)
    await event.respond(body, parse_mode="html", buttons=buttons)
    return True


def _fin_panel_buttons() -> list:
    return [
        [Button.inline("📒 Bugun", b"fin:rep:today"), Button.inline("3 kun", b"fin:rep:3"),
         Button.inline("Hafta", b"fin:rep:7"), Button.inline("Oy", b"fin:rep:month")],
        [Button.inline("💼 Balans va qarzlar", b"fin:balance"), Button.inline("🧾 Tarix", b"fin:history")],
        [Button.inline("📤 Excel (oy)", b"fin:export:month"), Button.inline("🎯 Byudjet", b"fin:budget")],
    ]


async def _fin_history() -> tuple[str, list | None]:
    rows = await storage.last_transactions(15)
    if not rows:
        return "🧾 Hali hech narsa yozilmagan. Masalan: «nonga 25 ming ishlatdim»", None
    lines = ["🧾 <b>Oxirgi yozuvlar</b> (🗑 — o'chirish)", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. <i>{r['day'][5:]}</i> {finance.row_line(r)}")
    btns = [Button.inline(f"🗑 {i}", f"fin:del:{r['id']}".encode()) for i, r in enumerate(rows, 1)]
    return "\n".join(lines), [btns[i:i + 5] for i in range(0, len(btns), 5)]


async def _fin_budget_text() -> str:
    budget = await storage.get_budget()
    if not budget:
        return ("🎯 Byudjet qo'yilmagan.\nMasalan: <code>/budget oziq-ovqat 2000000</code> yoki "
                "<code>/budget jami 8000000</code>\nO'chirish: <code>/budget oziq-ovqat off</code>\n"
                f"Kategoriyalar: {', '.join(finance.EXPENSE_CATS)}")
    today = finance.now().date()
    rows = await storage.list_transactions(finance.day_key(today.replace(day=1)), finance.day_key(today))
    return "🎯 <b>Oylik byudjet</b>\n\n" + "\n".join(finance.budget_lines(budget, rows))


async def _fin_export(name: str) -> tuple[bytes, str]:
    today = finance.now().date()
    if name == "all":
        rows = await storage.list_transactions()
        fname = "moliya_hammasi.csv"
    else:
        start, end, _, _ = finance.period_range(name, today)
        rows = await storage.list_transactions(finance.day_key(start), finance.day_key(end))
        fname = f"moliya_{finance.day_key(start)}_{finance.day_key(end)}.csv"
    return finance.to_csv(rows), fname


async def _send_export(name: str) -> None:
    data, fname = await _fin_export(name)
    f = io.BytesIO(data)
    f.name = fname
    await ctx.bot.send_file(ctx.owner_id, f, caption="📤 Kirim-chiqim (Excel'da ochiladi)", force_document=True)


# ---------- chatlardan qidirish ----------
def _remember(store: dict, value) -> str:
    key = str(next(_find_seq))
    store[key] = value
    while len(store) > 50:  # xotira cheksiz o'smasin
        store.pop(next(iter(store)))
    return key


async def _ai_ok() -> bool:
    """AI ishlatsa bo'ladimi (kalit bor va kunlik limit tugamagan)."""
    if not config.OPENAI_API_KEY:
        return False
    try:
        st = await storage.get_stats()
        return not (config.AI_DAILY_LIMIT and st.get("ai_requests", 0) >= config.AI_DAILY_LIMIT)
    except Exception:
        return True


async def _ai_count(tokens: int) -> None:
    try:
        await storage.stat_inc(ai_requests=1, ai_find=1, ai_tokens=tokens)
    except Exception:
        pass


async def _archive_hits(chat_id: int | None, q: "finder.Query", keywords: list[str]) -> list:
    """Arxivdagi o'chirilgan/tahrirlangan xabarlardan mos kelganlari (xato bo'lsa — bo'sh)."""
    try:
        everything = False
        if chat_id is not None:  # akkaunt o'chirilgan bo'lsa — arxivdagi butun yozishmadan
            everything = bool((await storage.archive_chat(chat_id) or {}).get("account_deleted"))
        docs = await storage.archive_search(chat_id, keywords or q.keywords or q.words, q.days, q.only_them,
                                            everything=everything)
        titles: dict[int, str] = {}
        if chat_id is None:
            for cid in {d["chat_id"] for d in docs}:
                info = await storage.archive_chat(cid) or {}
                titles[cid] = archive.chat_label({"chat_id": cid, **info})
        return [finder.from_archive(d, titles.get(d["chat_id"], "")) for d in docs]
    except Exception as e:
        log.warning("Arxivdan qidirilmadi (%s)", type(e).__name__)
        return []


async def _fill_titles(items: list) -> None:
    """O'chirilgan akkauntning chati Telegram'da nomsiz — arxivdagi (yoki taxminiy) ismi qo'yiladi."""
    cache: dict[int, str] = {}
    for f in items:
        if f.chat_title or not f.chat_id:
            continue
        if f.chat_id not in cache:
            try:
                info = await storage.archive_chat(f.chat_id)
            except Exception:
                info = None
            cache[f.chat_id] = archive.chat_label(info) if info else ""
        f.chat_title = cache[f.chat_id]


async def _archive_candidates(name: str, known: set[int]) -> list:
    """Arxivdagi chatlar (akkaunti o'chirilganlar ham — eski yoki taxminiy ismi bo'yicha)."""
    try:
        rows = await storage.archive_chats(0, 10, False, name)
    except Exception as e:
        log.warning("Arxivdan chat qidirilmadi (%s)", type(e).__name__)
        return []
    out = []
    for r in rows:
        if r["chat_id"] in known:
            continue
        score = max(finder.score_name(name, r.get("title") or "", r.get("username")),
                    finder.score_name(name, r.get("guess_title") or "")) or 1
        if r.get("kind") == "user":
            score += 0.5
        out.append(finder.Candidate(id=r["chat_id"], title=archive.chat_label(r), username=r.get("username"),
                                    score=score))
    return out


async def _find_in(event, chat_id: int | None, title: str, username: str | None, q: "finder.Query") -> None:
    """Bitta chatdan (chat_id=None — barcha chatlardan) qidiradi va natijani bitta xabarda ko'rsatadi."""
    where = f"«{title}» chatidan" if chat_id is not None else "barcha chatlardan"
    status = await event.respond(f"🔎 {where} qidiryapman...")
    use_ai = await _ai_ok()
    try:
        # 1) username / raqam / havola / email / karta — xabarlardan aniq ajratib olinadi
        if chat_id is not None and q.kind in finder.KINDS:
            async def progress(n: int) -> None:
                await status.edit(f"🔎 {where} qidiryapman... {n:,} ta xabar ko'rildi".replace(",", " "))

            # butun tarix bir marta o'qiladi; «menga tashlagan» bo'lsa keyin ajratiladi
            hits, scanned = await finder.search_chat(ctx.user, chat_id, q.kind, progress=progress)
            hits, only_them = finder.only_from_them(hits, q.only_them)
            if hits or not use_ai:
                await status.edit(finder.format_hits(title, hits, q.kind, scanned, only_them), parse_mode="html")
                return
        # 2) istalgan narsa: Telegram qidiruvi nomzodlarni yig'adi, AI javobni tanlaydi
        keywords = finder.keywords_for(q, title if chat_id is not None else "", username)
        if chat_id is not None and not keywords and not q.media and finder.wants_chat(q.text):
            # «Aziz bilan chatni top» — xabar emas, yozishmaning o'zi kerak
            text, buttons = await _archive_chat_view(chat_id)
            await status.edit(text, parse_mode="html", buttons=buttons)
            return
        if chat_id is None and not keywords and not q.media:
            await status.edit("Nimani qidiray? Masalan: «Ali yuborgan manzilni top» yoki «wifi parolini qidir».")
            return
        items = await finder.collect(ctx.user, chat_id, q, keywords)
        if not items and q.only_them:
            q.only_them = False
            items = await finder.collect(ctx.user, chat_id, q, keywords)
        # Arxivdan: Telegram'da endi yo'q (o'chirilgan) va tahrirlangan xabarlar ham
        items = finder.merge_archive(items, await _archive_hits(chat_id, q, keywords))
        if chat_id is None:
            await _fill_titles(items)
        answer, picked, ai_done = "", [], False
        if items and use_ai:
            try:
                answer, picked, tokens = await finder.ai_pick(q.text, items)
                ai_done = True
                await _ai_count(tokens)
            except Exception as e:
                log.warning("AI natijani tanlay olmadi (%s) — oddiy saralash", type(e).__name__)
        if items and not picked and not answer and (not ai_done or q.media):
            picked = finder.local_pick(items, keywords)  # AI rasm/faylni ko'rmaydi — eng mos/yangilari
        text = finder.format_found(title if chat_id is not None else "", q, answer, picked, len(items))
        buttons = None
        live = finder.forwardable(picked)  # o'chirilganlarini Telegram forward qila olmaydi — matni yuqorida
        if live:
            key = _remember(_find_saved, [(f.chat_id, f.id) for f in live])
            buttons = [[Button.inline("📥 Xabarlarni Saqlangan xabarlarga yuborish", f"fsave:{key}".encode())]]
        await status.edit(text, parse_mode="html", buttons=buttons, link_preview=False)
    except Exception as e:
        log.exception("Qidiruvda xato")
        await status.edit(f"❌ Qidirib bo'lmadi: {type(e).__name__}")


async def _run_find(event, text: str) -> None:
    q = finder.parse_query(text)
    if await _ai_ok():
        try:
            q, tokens = await finder.ai_parse(text)
            await _ai_count(tokens)
        except Exception as e:
            log.warning("So'rovni AI tahlil qila olmadi (%s) — oddiy tahlil", type(e).__name__)
    if not q.name:  # kim ekani aytilmagan — barcha chatlardan
        await _find_in(event, None, "", None, q)
        return
    try:
        cands = await finder.find_chats(ctx.user, q.name)
    except Exception as e:
        log.warning("Chatlar ro'yxati olinmadi (%s)", type(e).__name__)
        await event.respond(f"❌ Chatlar ro'yxatini olib bo'lmadi: {type(e).__name__}")
        return
    # Akkaunti o'chirilganlar Telegram'da nomsiz — arxivdagi eski/taxminiy ismi bo'yicha qo'shamiz
    cands = sorted(cands + await _archive_candidates(q.name, {c.id for c in cands}), key=lambda c: -c.score)
    if not cands:
        await event.respond(f"🤷 «{html.escape(q.name)}» nomli chat topilmadi — barcha chatlardan qidiraman.",
                            parse_mode="html")
        if q.ai and q.name not in q.keywords:
            q.keywords = [q.name] + q.keywords
        await _find_in(event, None, "", None, q)
        return
    best = cands[0]
    if len(cands) == 1 or best.score >= cands[1].score + 1:
        await _find_in(event, best.id, best.title, best.username, q)
        return
    key = _remember(_find_pending, (q, cands[:6]))
    buttons = [[Button.inline(c.title[:40] + (f" (@{c.username})" if c.username else ""),
                              f"find:{key}:{i}".encode())] for i, c in enumerate(cands[:6])]
    await event.respond("Qaysi chatdan qidiray?", buttons=buttons)


def _parse_budget_amount(raw: str) -> int:
    """"2000000", "2mln", "500ming", "2 000 000" -> butun son (noto'g'ri bo'lsa 0)."""
    parsed = finance.local_parse(raw + (" so'm" if raw.replace(" ", "").isdigit() else ""))
    return int(parsed["items"][0]["amount"]) if parsed["items"] else 0


def setup(bot: TelegramClient, user: TelegramClient, owner_id: int) -> None:
    ctx.bot, ctx.user, ctx.owner_id = bot, user, owner_id
    ctx.users = {1: user}
    ctx.names = {1: ""}
    is_owner = lambda e: e.sender_id == owner_id  # noqa: E731

    @bot.on(events.NewMessage(pattern=r"^/(start|help)", func=is_owner))
    async def _help(event):
        if event.raw_text.startswith("/start"):
            await set_commands(bot, owner_id)  # birinchi /start dan keyin menyu faqat egasiga o'rnatiladi
        # HELP Telegram limitidan (4096) uzun — bo'laklab yuboriladi
        await send_long(event.respond, HELP, parse_mode="html", buttons=main_keyboard())

    @bot.on(events.NewMessage(pattern=r"^/menu$", func=is_owner))
    async def _menu(event):
        await event.respond("📋 Menyu pastda — kerakli tugmani bosing.", buttons=main_keyboard())

    # Pastdagi doimiy tugmalar (oddiy matn bo'lib keladi)
    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and (e.raw_text or "").strip() in MENU_TEXTS))
    async def _menu_button(event):
        choice = event.raw_text.strip()
        if choice == MENU_PANEL:
            await _status(event)
        elif choice == MENU_INBOX:
            await _inbox(event)
        elif choice == MENU_ARCHIVE:
            text, buttons = await _archive_panel()
            await event.respond(text, parse_mode="html", buttons=buttons)
        elif choice == MENU_FIN:
            await event.respond(await finance.make_report("today"), parse_mode="html",
                                buttons=_fin_panel_buttons())
        elif choice == MENU_FIND:
            await event.respond("🔎 Nimani topay? Shunchaki yozing yoki ovozli ayting:\n"
                                "«Ali yuborgan manzilni top», «Vali tashlagan pdf faylni qidir», "
                                "«wifi parolini qidir».")
        elif choice == MENU_REPORT:
            await event.respond(await report.make_report(), parse_mode="html")
        elif choice == MENU_PROMISES:
            await _show_promises(event)
        elif choice == MENU_WATCH:
            text, buttons = await watchers.list_view()
            await event.respond(text, parse_mode="html", buttons=_inline(buttons))
        elif choice == MENU_IG:
            await _send_ig_panel(event)
        elif choice == MENU_GROUPS:
            await _send_groups_panel(event)
        elif choice == MENU_HELP:
            await send_long(event.respond, HELP, parse_mode="html", buttons=main_keyboard())

    @bot.on(events.NewMessage(pattern=r"^/arxiv(?:\s+([\s\S]+))?$", func=is_owner))
    async def _arxiv(event):
        name = (event.pattern_match.group(1) or "").strip()
        text, buttons = await (_archive_list(0, False, name) if name else _archive_panel())
        await event.respond(text, parse_mode="html", buttons=buttons)

    @bot.on(events.NewMessage(pattern=r"^/xulosa(?:\s+([\s\S]+))?$", func=is_owner))
    async def _xulosa(event):
        name = (event.pattern_match.group(1) or "").strip()
        if not name:
            await event.respond("Kimning yozishmasi? Masalan: <code>/xulosa Ali</code>\n"
                                "Yoki: 🗂 Chat arxivi → chatni tanlang → 🧠 Xulosa", parse_mode="html")
            return
        rows = await storage.archive_chats(0, 6, False, name)
        if not rows:
            await event.respond(f"🤷 Arxivda «{html.escape(name)}» nomli chat topilmadi.", parse_mode="html")
            return
        if len(rows) == 1:
            await _send_summary(rows[0]["chat_id"], 7)
            return
        await event.respond("Qaysi chat?", buttons=[
            [Button.inline((r.get("title") or str(r["chat_id"]))[:40], f"arc:sum:{r['chat_id']}:7".encode())]
            for r in rows])

    @bot.on(events.NewMessage(pattern=r"^/(?:deleted|ochirilganlar)(?:\s+([\s\S]+))?$", func=is_owner))
    async def _deleted_cmd(event):
        await _show_gone(event, event.pattern_match.group(1) or "")

    @bot.on(events.NewMessage(pattern=r"^/eslatma(?:lar)?(?:@\w+)?(?:\s+([\s\S]+))?$", func=is_owner))
    async def _eslatma(event):
        arg = (event.pattern_match.group(1) or "").strip()
        req = watchers.parse(arg, strict=False) if arg else None
        if req is None:
            text, buttons = await watchers.list_view()
            await event.respond(text, parse_mode="html", buttons=_inline(buttons))
            return
        await _watch_request(event, req)

    # ---------- 📸 Instagram ----------
    @bot.on(events.NewMessage(pattern=r"^/ig(?:@\w+)?$", func=is_owner))
    async def _ig_cmd(event):
        await _send_ig_panel(event)

    @bot.on(events.NewMessage(pattern=r"^/iglogin(2?)(?:@\w+)?(?:\s+([\s\S]*))?$", func=is_owner))
    async def _ig_login(event):
        slot = event.pattern_match.group(1) or "1"     # /iglogin2 — 2-akkaunt
        args = (event.pattern_match.group(2) or "").strip().split(maxsplit=1)
        username, password = (args + ["", ""])[:2]
        try:
            await event.delete()  # parol chatda qolmasin
        except Exception:
            log.warning("Parolli xabar o'chirilmadi — uni o'zingiz o'chiring")
        if not username or not password:
            await event.respond(instagram.login_help(slot), parse_mode="html")
            return
        status = await event.respond("⏳ Instagram'ga kirilmoqda... (parolli xabaringiz o'chirildi)")

        async def say(text: str) -> None:
            await bot.send_message(owner_id, text)

        result = await instagram.login(username, password.strip(), say, slot)
        try:
            await status.edit(result)
        except Exception:
            await event.respond(result)
        if instagram.connected(slot):
            await _send_ig_panel(event)

    @bot.on(events.NewMessage(pattern=r"^/igsession(2?)(?:@\w+)?(?:\s+([\s\S]+))?$", func=is_owner))
    async def _ig_session(event):
        slot = event.pattern_match.group(1) or "1"     # /igsession2 — 2-akkaunt
        raw = event.pattern_match.group(2)
        try:
            await event.delete()  # sessionid — parol bilan barobar
        except Exception:
            pass
        if not raw:
            await event.respond(instagram.login_help(slot), parse_mode="html")
            return
        status = await event.respond("⏳ Sessiya tekshirilmoqda... (xabaringiz o'chirildi)")
        result = await instagram.login_session(raw, slot)
        try:
            await status.edit(result, parse_mode="html")
        except Exception:
            await event.respond(result, parse_mode="html")
        if instagram.connected(slot):
            await _send_ig_panel(event)

    @bot.on(events.NewMessage(pattern=r"^/igyukla(?:@\w+)?(?:\s+([\s\S]+))?$", func=is_owner))
    async def _ig_download(event):
        links = instagram.parse_media_links(event.pattern_match.group(1) or "")
        if not links:
            await event.respond(
                "📥 <b>Instagram'dan yuklash</b>\n\nHavolani yuboring — buyruq shart emas, shunchaki "
                "tashlang:\n"
                "• <code>https://www.instagram.com/p/XXXX/</code> — post (albom bo'lsa hammasi)\n"
                "• <code>https://www.instagram.com/reel/XXXX/</code> — reels\n"
                "• <code>https://www.instagram.com/stories/user/123456/</code> — storis\n\n"
                "Yopiq akkauntdan olish uchun ulangan Instagram akkauntingiz unga obuna bo'lishi kerak.",
                parse_mode="html")
            return
        await _download_ig_links(event, links)

    @bot.on(events.NewMessage(pattern=r"^/iglogout(2?)(?:@\w+)?$", func=is_owner))
    async def _ig_logout(event):
        await event.respond(await instagram.logout(event.pattern_match.group(1) or "1"))

    @bot.on(events.CallbackQuery(pattern=rb"^ig:", func=is_owner))
    async def _ig_callback(event):
        parts = event.data.decode().split(":")
        action, arg = parts[1], parts[2] if len(parts) > 2 else ""
        if action == "set":
            await event.answer((await instagram.toggle(arg))[:190])
            await _send_ig_panel(event, edit=True)
        elif action == "panel":
            await event.answer()
            await _send_ig_panel(event, edit=True)
        elif action == "check":
            await event.answer("🔄 Tekshirilmoqda...")
            await _send_ig_panel(event, extra=await instagram.check_now(), edit=True)
        elif action == "help":
            await event.answer()
            await event.respond(instagram.login_help(arg or "1"), parse_mode="html")
        elif action == "net":
            await event.answer("🌐 Tekshirilmoqda (15 soniyagacha)...")
            await event.respond(await instagram.net_check(), parse_mode="html", link_preview=False)
        elif action == "logout":
            slot = arg or "1"
            which = "2-akkauntdan" if slot == "2" else "akkauntidan"
            await event.answer()
            await event.respond(f"🚪 Instagram {which} chiqaymi? Bir martalik media saqlanmay qoladi.",
                                buttons=[[Button.inline("✅ Ha, chiqish", f"ig:logout_ok:{slot}".encode()),
                                          Button.inline("↩️ Yo'q", b"ig:panel")]])
        elif action == "logout_ok":
            await event.answer()
            await event.edit(await instagram.logout(arg or "1"))
        elif action == "chats":
            await event.answer()
            text, rows = await instagram.chats_view(int(arg) if arg.isdigit() else 0)
            await _edit_or_send(event, text, rows)
        elif action == "view" and arg:
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await event.answer()
            text, rows = await instagram.chat_view(arg, page)
            await _edit_or_send(event, text, rows)
        elif action == "dl":
            share = _ig_shares.get(arg)
            links = instagram.parse_media_links(share) if share else []
            if not links:
                await event.answer("Havola eskirgan — uni botga qaytadan tashlang", alert=True)
                return
            await event.answer("📥 Yuklanmoqda...")
            await _download_ig_links(event, links)
        elif action == "drive":
            await event.answer("☁️ Yuklanmoqda...")
            await _send_ig_panel(event, extra=await instagram.drive_now(), edit=True)
        elif action == "html" and arg:
            await event.answer("📄 Tayyorlanmoqda...")
            try:
                data, fname, n = await instagram.export_html(arg)
            except Exception as e:
                log.exception("Instagram yozishmasi HTML qilinmadi")
                await event.respond(f"❌ Tayyorlanmadi: {type(e).__name__}")
                return
            f = io.BytesIO(data)
            f.name = fname
            await bot.send_file(owner_id, f, caption=f"📄 Instagram yozishmasi — {n} ta xabar "
                                                     "(qaytarib olinganlari qizil bilan)", force_document=True)
        elif action == "reply" and arg:
            _awaiting_reply.pop("chat", None)
            _awaiting_reply["ig"] = arg
            await event.answer()
            await event.respond("✍️ Instagram'ga yuboriladigan javob matnini yozing:")
        elif action == "mute" and arg:
            instagram.mute(arg)
            await event.answer(f"Bu Instagram chatida AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi")
        else:
            await event.answer()

    # ---------- ☁️ Google Drive: holat va botdan qayta ulash ----------
    @bot.on(events.NewMessage(pattern=r"^/drive(?:@\w+)?$", func=is_owner))
    async def _drive_cmd(event):
        await _drive_panel(event)

    @bot.on(events.CallbackQuery(pattern=rb"^gd:(auth|check)$", func=is_owner))
    async def _drive_cb(event):
        await event.answer()
        if event.data == b"gd:auth":
            await _drive_start_auth(event)
        else:
            await _drive_panel(event)

    # ---------- 👥 Guruhlar: tanlangan guruh xabarlarini klonlash ----------
    @bot.on(events.NewMessage(pattern=r"^/guruhlar(?:@\w+)?$", func=is_owner))
    async def _groups_cmd(event):
        await _send_groups_panel(event)

    @bot.on(events.CallbackQuery(pattern=rb"^grp:", func=is_owner))
    async def _groups_callback(event):
        parts = event.data.decode().split(":")
        action = parts[1]
        if action == "t" and len(parts) == 4:
            chat_id, page = int(parts[2]), int(parts[3])
            title = next((g["title"] for g in await groups.my_groups() if g["id"] == chat_id), "")
            on = await groups.toggle(chat_id, title)
            await event.answer(("✅ Klonlash yoqildi: " if on else "⏸ Klonlash o'chirildi: ") + title[:150])
            await _send_groups_panel(event, page, edit=True)
        elif action == "p":
            await event.answer()
            await _send_groups_panel(event, int(parts[2]), edit=True)
        elif action == "r":
            await event.answer("🔄 Yangilanyapti...")
            await _send_groups_panel(event, int(parts[2]), edit=True, refresh=True)
        else:
            await event.answer()

    @bot.on(events.CallbackQuery(pattern=rb"^wch:", func=is_owner))
    async def _watch_callback(event):
        parts = event.data.decode().split(":")
        action, arg = parts[1], parts[2] if len(parts) > 2 else ""
        if action == "dm" and arg.isdigit():
            await event.answer()
            await _open_dm(event, arg)
        elif action == "del":
            ok = await watchers.remove(arg)
            await event.answer("🗑 Eslatma o'chirildi" if ok else "Allaqachon o'chirilgan")
            text, buttons = await watchers.list_view()
            try:
                await event.edit(text, parse_mode="html", buttons=_inline(buttons))
            except Exception:
                pass
        elif action == "list":
            await event.answer()
            text, buttons = await watchers.list_view()
            await event.respond(text, parse_mode="html", buttons=_inline(buttons))
        elif action == "ok":
            text = _watch_pending.pop(arg, None)
            req = watchers.parse(text, strict=False) if text else None
            if req is None:
                await event.answer("So'rov eskirgan — qaytadan yozing")
                return
            await event.answer("⏳")
            await _watch_request(event, req)
        elif action == "no":
            _watch_pending.clear()
            await event.answer("Bekor qilindi")
            try:
                await event.edit("❌ Eslatma qo'yilmadi.")
            except Exception:
                pass
        elif action == "add":
            _awaiting_reply.pop("chat", None)
            _awaiting_reply.pop("ig", None)
            _awaiting_reply["watch"] = 1
            await event.answer()
            await event.respond(watchers.ADD_HELP, parse_mode="html")
        elif action == "on" and arg.isdigit():
            await event.answer("⏳")
            await _watch_request(event, watchers.Request("online", user_ref=arg))
        elif action == "ph" and arg.isdigit():
            await event.answer("⏳")
            await _watch_request(event, watchers.Request("phone", phone=arg, number=arg))
        elif action == "pick" and len(parts) > 3 and parts[3].isdigit():
            res = await watchers.pick(ctx.user, arg, int(parts[3]))
            if res is None:
                await event.answer("So'rov eskirgan — qaytadan yozing")
                return
            await event.answer()
            await event.respond(res[0], parse_mode="html", buttons=_inline(res[1]))
        else:
            await event.answer()

    @bot.on(events.NewMessage(pattern=r"^/vadalar$", func=is_owner))
    async def _vadalar(event):
        await _show_promises(event)

    @bot.on(events.CallbackQuery(pattern=rb"^prm:", func=is_owner))
    async def _prm_callback(event):
        _, action, pid = event.data.decode().split(":", 2)
        if action == "done":
            ok = await storage.promise_update(pid, done=True, done_at=time.time())
            await event.answer("✅ Bajarildi" if ok else "Topilmadi")
            if ok:
                try:
                    msg = await event.get_message()
                    await event.edit("✅ <s>" + html.escape(msg.raw_text or "") + "</s>", parse_mode="html")
                except Exception:
                    pass
        elif action == "later":
            ok = await storage.promise_update(pid, due=promises.tomorrow())
            await event.answer("⏰ Ertaga eslataman" if ok else "Topilmadi")
        elif action == "del":
            ok = await storage.promise_delete(pid)
            await event.answer("🗑 O'chirildi" if ok else "Topilmadi")
            if ok:
                try:
                    await event.delete()
                except Exception:
                    pass
        else:
            await event.answer()

    @bot.on(events.CallbackQuery(pattern=rb"^arc:", func=is_owner))
    async def _arc_callback(event):
        parts = event.data.decode().split(":")
        action, arg = parts[1], parts[2] if len(parts) > 2 else ""
        if action == "html":
            await event.answer("Tayyorlanmoqda...")
            await _send_archive_html(int(arg), len(parts) > 3 and parts[3] == "d")
            return
        if action == "drive":
            await storage.archive_drive_mark_all(only_missing=True)
            archive.drive_wake()
            await event.answer("☁️ Yuklash boshlandi — o'zgargan chatlar Drive'ga ketadi", alert=True)
            return
        if action == "sum" and len(parts) > 3 and parts[3] in SUMMARY_DAYS:
            await event.answer("🧠 Tayyorlanmoqda...")
            await _send_summary(int(arg), SUMMARY_DAYS[parts[3]])
            return
        if action == "backup":
            if not gdrive.enabled():
                await event.answer("Google Drive sozlanmagan", alert=True)
                return
            await event.answer("💾 Boshlandi")
            await _backup_now()
            return
        if action == "weekly":
            await event.answer()
            await send_weekly(*await weekly.make())
            return
        if action == "promises":
            await event.answer()
            await _show_promises(event)
            return
        if action == "gonescan":
            await event.answer("🔎 Tekshirilmoqda...")
            await _gone_scan()
            return
        await event.answer()
        if action == "set" and arg in ("archive_enabled", "archive_alerts", "archive_groups", "drive_media"):
            await runtime.update(**{arg: not runtime.get(arg)})
            text, buttons = await _archive_panel()
        elif action == "wset" and arg in WATCH_KEYS:
            await runtime.update(**{arg: not runtime.get(arg)})
            text, buttons = _watch_panel()
        elif action == "watch":
            text, buttons = _watch_panel()
        elif action in ("list", "dlist"):
            text, buttons = await _archive_list(int(arg or 0), action == "dlist")
        elif action == "chat":
            text, buttons = await _archive_chat_view(int(arg))
        elif action == "gone":
            text, buttons = await _gone_list(int(arg or 0))
        else:
            text, buttons = await _archive_panel()
        try:
            await event.edit(text, parse_mode="html", buttons=buttons)
        except Exception:
            pass  # o'zgarmagan bo'lsa Telegram xato beradi

    # incoming=True SHART: busiz botning o'z xabarlari ham (yuborgan media, bildirishnoma) shu yerga tushib,
    # "Bu shaxsiy bot." javobi o'ziga-o'zi cheksiz javob qaytaradi.
    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.sender_id != owner_id and e.is_private))
    async def _stranger(event):
        now = time.time()
        if now - _stranger_replied.get(event.sender_id, 0) < STRANGER_REPLY_COOLDOWN:
            return  # bir odamga (yoki boshqa botga) qayta-qayta yozmaymiz
        _stranger_replied[event.sender_id] = now
        await event.respond("Bu shaxsiy bot.")

    @bot.on(events.NewMessage(pattern=r"^/on$", func=is_owner))
    async def _on(event):
        await storage.set_mode("on")
        await event.respond("🟢 Avtojavob doim yoqiq.")

    @bot.on(events.NewMessage(pattern=r"^/off$", func=is_owner))
    async def _off(event):
        await storage.set_mode("off")
        await event.respond("⚪️ Avtojavob o'chirildi (xabarlar baribir sizga yetkaziladi).")

    @bot.on(events.NewMessage(pattern=r"^/auto$", func=is_owner))
    async def _auto(event):
        await storage.set_mode("auto")
        st = await availability.get_status()
        await event.respond(
            "🤖 Aqlli rejim: kotiba faqat siz band bo'lganda javob beradi "
            "(kalendarda uchrashuv, ish vaqti yoki siz oxirgi daqiqalarda yozmagan bo'lsangiz).\n"
            f"Hozir: {'javob beradi' if st.reply else 'jim turadi'} — {st.reason}"
        )

    @bot.on(events.NewMessage(pattern=r"^/status$", func=is_owner))
    async def _status(event):
        await _send_panel(event, extra=(
            f"📨 Oxirgi 24 soatda: {await storage.today_count()} ta xabar\n"
            f"📬 Javob kutayotganlar: {len(await storage.pending_inbox())} (/inbox)"
        ))

    @bot.on(events.NewMessage(pattern=r"^/tag\s+(\S+)\s+(\S+)$", func=is_owner))
    async def _tag(event):
        arg, tag = event.pattern_match.group(1), event.pattern_match.group(2).lower()
        tag = TAG_ALIASES.get(tag, tag)
        uid = await _resolve_user_id(arg)
        if uid is None:
            await event.respond("❌ Foydalanuvchi topilmadi.")
            return
        if tag in ("clear", "tozalash", "-"):
            ok = await storage.remove_tag(uid)
            await event.respond("✅ Turi olib tashlandi." if ok else "Bu odamga tur berilmagan edi.")
            return
        if tag not in TAGS:
            await event.respond("Tur: oila, mijoz yoki dost. Olib tashlash: /tag <id> clear")
            return
        await storage.set_tag(uid, tag, arg)
        await event.respond(f"✅ {arg} — {TAGS[tag]}")

    @bot.on(events.NewMessage(pattern=r"^/tags$", func=is_owner))
    async def _tags(event):
        rows = await storage.list_tags()
        if not rows:
            await event.respond("Hali hech kimga tur berilmagan. Masalan: /tag @username oila")
            return
        await event.respond("\n".join(f"• {TAGS.get(r['tag'], r['tag'])}: {html.escape(r['label'])} "
                                      f"<code>{r['user_id']}</code>" for r in rows), parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"^/inbox$", func=is_owner))
    async def _inbox(event):
        rows = await storage.pending_inbox()
        if not rows:
            await event.respond("📭 Javob kutayotgan xabar yo'q.")
            return
        for r in rows[:15]:
            mark = "🔴 " if r["urgent"] else ""
            await event.respond(
                f"{mark}👤 {_who(r['name'], r['username'], r['chat_id'])}\n"
                f"📝 {html.escape(r['summary'] or r['last_text'] or '')}",
                parse_mode="html",
                buttons=_buttons(r["chat_id"]),
            )

    @bot.on(events.NewMessage(pattern=r"^/reply\s+(-?\d+)\s+([\s\S]+)", func=is_owner))
    async def _reply_cmd(event):
        chat_id = int(event.pattern_match.group(1))
        await _send_as_owner(chat_id, event.pattern_match.group(2), event)

    @bot.on(events.NewMessage(pattern=r"^/lichka(?:@\w+)?(?:\s+(\S+))?\s*$", func=is_owner))
    async def _lichka(event):
        arg = event.pattern_match.group(1)
        if not arg:
            await event.respond("Foydalanish: /lichka 123456789 yoki /lichka @username\n"
                                "Yoki botga shunchaki ID raqamini yuboring.")
            return
        await _open_dm(event, arg)

    @bot.on(events.NewMessage(pattern=r"^/save(?:\s+([\s\S]+))?$", func=is_owner))
    async def _save_cmd(event):
        links = saver.parse_links(event.pattern_match.group(1) or "")
        if not links:
            await event.respond("Foydalanish: <code>/save https://t.me/c/1234567890/15</code>\n"
                                "Yoki xabar havolasining o'zini yuboring (xabar ustida → «Havolani nusxalash»).",
                                parse_mode="html")
            return
        await _save_links(event, links)

    @bot.on(events.NewMessage(pattern=r"^/find(?:\s+([\s\S]+))?$", func=is_owner))
    async def _find_cmd(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await event.respond("Istalgan narsani qidirish mumkin:\n"
                                "<code>/find Ali tashlagan username</code>\n"
                                "<code>/find Vali yuborgan manzil</code>\n"
                                "<code>/find Sardor kecha tashlagan pdf fayl</code>\n"
                                "<code>/find wifi paroli</code> (ism yo'q — barcha chatlardan)\n\n"
                                "Yoki oddiy yozing/ayting: «Ali menga qayerda uchrashamiz degandi, topib ber».",
                                parse_mode="html")
            return
        await _run_find(event, arg)

    @bot.on(events.CallbackQuery(pattern=rb"^find:", func=is_owner))
    async def _find_callback(event):
        _, key, idx = event.data.decode().split(":")
        pending = _find_pending.get(key)
        if not pending or not idx.isdigit() or int(idx) >= len(pending[1]):
            await event.answer("So'rov eskirgan — qaytadan yozing")
            return
        await event.answer()
        q, cands = pending
        c = cands[int(idx)]
        await _find_in(event, c.id, c.title, c.username, q)

    @bot.on(events.CallbackQuery(pattern=rb"^dmsave:\d+$", func=is_owner))
    async def _dm_save(event):
        uid = int(event.data.decode().split(":", 1)[1])
        if await _mention_in_saved(uid):
            await event.answer("📥 «Saqlangan xabarlar»ga yubordim — o'sha yerda ism ustiga bosing", alert=True)
        else:
            await event.answer("❌ Yuborib bo'lmadi — akkauntingiz bu odamni tanimaydi", alert=True)

    @bot.on(events.CallbackQuery(pattern=rb"^fsave:", func=is_owner))
    async def _find_save(event):
        refs = _find_saved.get(event.data.decode().split(":", 1)[1])
        if not refs:
            await event.answer("Ro'yxat eskirgan — qaytadan qidiring")
            return
        by_chat: dict[int, list[int]] = {}
        for cid, mid in refs:
            by_chat.setdefault(cid, []).append(mid)
        ok, failed = 0, 0
        for cid, ids in by_chat.items():
            try:
                await ctx.user.forward_messages("me", ids, from_peer=cid)
                ok += len(ids)
            except Exception as e:
                log.warning("Xabar Saqlanganlarga yuborilmadi (%s)", type(e).__name__)
                failed += len(ids)
        await event.answer(f"📥 {ok} ta xabar Saqlangan xabarlarga yuborildi"
                           + (f", {failed} tasi yuborilmadi (chatda nusxalash taqiqlangan bo'lishi mumkin)"
                              if failed else ""), alert=bool(failed))

    @bot.on(events.NewMessage(pattern=r"^/unmute(?:\s+(\S+))?$", func=is_owner))
    async def _unmute(event):
        arg = event.pattern_match.group(1)
        uid = await _resolve_user_id(arg) if arg else None
        if uid is None:
            await event.respond("Foydalanish: <code>/unmute 123456789</code> yoki <code>/unmute @username</code>",
                                parse_mode="html")
            return
        was_muted = await storage.is_muted(uid)
        await storage.unmute(uid)
        await event.respond(f"🔊 <code>{uid}</code> chatida kotiba yana javob beradi."
                            + ("" if was_muted else " (U jim holatda emas edi.)"), parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"^/ignore\s+(\S+)", func=is_owner))
    async def _ignore(event):
        arg = event.pattern_match.group(1)
        uid = await _resolve_user_id(arg)
        if uid is None:
            await event.respond("❌ Foydalanuvchi topilmadi.")
            return
        await storage.add_ignored(uid, arg)
        await event.respond(f"🚫 {arg} ({uid}) ga endi avtojavob berilmaydi.")

    @bot.on(events.NewMessage(pattern=r"^/unignore\s+(\S+)", func=is_owner))
    async def _unignore(event):
        uid = await _resolve_user_id(event.pattern_match.group(1))
        ok = uid is not None and await storage.remove_ignored(uid)
        await event.respond("✅ Istisnodan chiqarildi." if ok else "❌ Ro'yxatda topilmadi.")

    @bot.on(events.NewMessage(pattern=r"^/ignored$", func=is_owner))
    async def _ignored(event):
        rows = await storage.list_ignored()
        if not rows:
            await event.respond("Istisnolar ro'yxati bo'sh.")
            return
        await event.respond("\n".join(f"• {r['label']} — <code>{r['user_id']}</code>" for r in rows),
                            parse_mode="html")

    # ---------- hisobot va nazorat ----------
    @bot.on(events.NewMessage(pattern=r"^/report$", func=is_owner))
    async def _report(event):
        await event.respond(await report.make_report(), parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"^/usage$", func=is_owner))
    async def _usage(event):
        st = await storage.get_stats()
        lim = lambda n: str(n) if n else "cheksiz"  # noqa: E731
        busiest = sorted((st.get("chat_ai") or {}).items(), key=lambda kv: -kv[1])[:3]
        top = "\n".join(f"  • <code>{cid}</code>: {n}" for cid, n in busiest) or "  —"
        await event.respond(
            f"🤖 <b>Bugungi AI sarfi</b>\n"
            f"So'rovlar: {st.get('ai_requests', 0)} / {lim(config.AI_DAILY_LIMIT)}\n"
            f"  javob: {st.get('ai_chat', 0)}, ovoz: {st.get('ai_voice', 0)}, "
            f"rasm: {st.get('ai_image', 0)}, ovozli javob: {st.get('ai_tts', 0)}\n"
            f"Tokenlar: ~{st.get('ai_tokens', 0)}\n"
            f"Bitta odamga limit: {lim(config.AI_CHAT_DAILY_LIMIT)} so'rov/kun\n"
            f"Eng ko'p ishlatganlar:\n{top}",
            parse_mode="html",
        )

    @bot.on(events.NewMessage(pattern=r"^/health$", func=is_owner))
    async def _health(event):
        if ctx.monitor is None:
            await event.respond("Holat nazorati ishlamayapti.")
            return
        await event.respond("⏳ Tekshirilmoqda...")
        results = await ctx.monitor.check_once()
        await event.respond("\n".join(f"{'✅' if p is None else '❌'} {name}" + (f": {p}" if p else "")
                                      for name, p in results.items())
                            + f"\n🧠 Xotira: {health.memory_note()}"
                            + f"\n\n🎤 {voicelab.describe()}"
                            + f"\n☁️ Google Drive: {await gdrive.check() or 'ishlayapti'}"
                            + f"\n📸 Instagram: {instagram.describe()}")

    # ---------- profil: bio, iqtibos, online ----------
    # /bio, /online, /quote — argumentsiz bo'lsa tugmali panel, argument bilan — to'g'ridan-to'g'ri
    @bot.on(events.NewMessage(pattern=r"^/bio(?:\s+(\S+))?$", func=is_owner))
    async def _bio(event):
        arg = (event.pattern_match.group(1) or "").lower()
        if not arg:
            await _send_panel(event)
        elif arg in ("on", "off", "now", "restore"):
            await event.respond(await _do_action(f"bio_{arg}"))
        else:
            await event.respond("Foydalanish: /bio, /bio on, /bio off, /bio now, /bio restore")

    @bot.on(events.NewMessage(pattern=r"^/quote(?:\s+([\s\S]+))?$", func=is_owner))
    async def _quote(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await _send_panel(event, extra="✍️ Doimiy iqtibos qo'yish: <code>/quote Ilm — kuch</code>")
        elif arg.lower() in ("auto", "off"):
            await event.respond(await _do_action(f"quote_{arg.lower()}"))
        else:
            arg = arg.strip("“”\"'«»")
            await runtime.update(quote_mode="fixed", quote_text=arg)
            note = ""
            if bio_updater.tg_len(arg) > 22:
                note = ("\n⚠️ Iqtibos uzun — oddiy akkauntda bio 70 belgi, sig'masa iqtibossiz chiqadi. "
                        "~22 belgigacha tavsiya etiladi.")
            await event.respond(f"✅ Iqtibos: “{arg}”{note}")

    @bot.on(events.NewMessage(pattern=r"^/online(?:\s+(\S+))?$", func=is_owner))
    async def _online(event):
        arg = (event.pattern_match.group(1) or "").lower()
        if arg in ("on", "off"):
            await event.respond(await _do_action(f"online_{arg}"))
        else:
            await _send_panel(event)

    # ---------- 🔑 Telegram akkaunt (tg_login.py) ----------
    @bot.on(events.NewMessage(pattern=r"^/tglogin(?:@\w+)?(?:\s.*)?$", func=is_owner))
    async def _tg_login(event):
        me = await user.get_me()
        name = html.escape(f"{me.first_name or ''} {me.last_name or ''}".strip())
        await event.respond(
            f"✅ Telegram akkaunt ulangan: <b>{name}</b>"
            + (f" (@{html.escape(me.username)})" if me.username else "")
            + f", id <code>{me.id}</code>\n\n"
            + _second_status() + "\n\n"
            "Asosiy akkauntni almashtirish: /tglogout — Anna qayta ishga tushadi va yangi akkaunt "
            "uchun telefon, kod va parolni shu botda so'raydi (kompyuterda terminal oynasida ishlasa — "
            "terminalda).", parse_mode="html")

    # ---------- 📱 2-akkaunt: shu botning o'zida ikkinchi Telegram akkaunt ----------
    @bot.on(events.NewMessage(pattern=r"^/tglogin2(?:@\w+)?$", func=is_owner))
    async def _tg_login2(event):
        global _login2_task
        if 2 in ctx.users:
            await event.respond(_second_status() + "\n\nChiqish: /tglogout2", parse_mode="html")
            return
        if _login2_task and not _login2_task.done():
            await event.respond("⏳ 2-akkaunt ulanishi davom etyapti — so'ralgan ma'lumotni yuboring "
                                "(to'xtatish: «bekor»).")
            return
        _login2_task = asyncio.create_task(_login_second(bot))

    @bot.on(events.NewMessage(pattern=r"^/tglogout2(?:@\w+)?$", func=is_owner))
    async def _tg_logout2(event):
        if 2 not in ctx.users:
            await event.respond("2-akkaunt ulanmagan. Ulash: /tglogin2")
            return
        await event.respond(
            f"⚠️ 2-akkauntdan (<b>{html.escape(ctx.names.get(2, ''))}</b>) chiqilsinmi? Sessiya bekor qilinadi, "
            "asosiy akkaunt ishlashda davom etadi.", parse_mode="html",
            buttons=[[Button.inline("✅ Ha, chiqish", b"tg:logout2_ok"), Button.inline("↩️ Yo'q", b"tg:cancel")]])

    @bot.on(events.NewMessage(pattern=r"^/tglogout(?:@\w+)?$", func=is_owner))
    async def _tg_logout(event):
        await event.respond(
            "⚠️ Telegram akkauntdan chiqilsinmi?\n\nSessiya bekor qilinadi (Telegram → Qurilmalar ro'yxatidan "
            "ham o'chadi), Anna to'xtab qayta ishga tushadi va yangi login so'raydi.",
            buttons=[[Button.inline("✅ Ha, chiqish", b"tg:logout_ok"), Button.inline("↩️ Yo'q", b"tg:cancel")]])

    @bot.on(events.CallbackQuery(pattern=rb"^tg:(logout_ok|logout2_ok|cancel)$", func=is_owner))
    async def _tg_logout_cb(event):
        if event.data == b"tg:cancel":
            await event.edit("↩️ Bekor qilindi — akkaunt ulangan holda qoldi.")
            return
        if event.data == b"tg:logout2_ok":
            await event.edit(await logout_second())
            return
        await event.edit("🚪 Akkauntdan chiqilyapti...")
        await tg_logout()

    @bot.on(events.NewMessage(pattern=r"^/yangixabar(?:\s+(\S+))?$", func=is_owner))
    async def _new_msg(event):
        arg = (event.pattern_match.group(1) or "").lower()
        if arg in ("on", "off"):
            await runtime.update(new_msg_alerts=arg == "on")
        text, buttons = new_msg_panel()
        await event.respond(text, buttons=buttons)

    @bot.on(events.CallbackQuery(pattern=rb"^newmsg:(on|off)$", func=is_owner))
    async def _new_msg_cb(event):
        on = event.data == b"newmsg:on"
        await runtime.update(new_msg_alerts=on)
        await event.answer("📩 «Yangi xabar» yoqildi" if on else "🔕 «Yangi xabar» endi kelmaydi")
        text, buttons = new_msg_panel()
        await event.respond(text, buttons=buttons)

    # ---------- shaxsiy buxgalter ----------
    # Egasining ovozli xabari: matnga o'giriladi va kirim-chiqim sifatida yoziladi
    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and (e.voice or e.audio or e.video_note)))
    async def _fin_voice(event):
        status = await event.respond("🎤 Tinglayapman...")
        try:
            data = await event.download_media(file=bytes)
            ext = "mp4" if event.video_note else "ogg" if event.voice else "mp3"
            text = await ai.transcribe(data, f"voice.{ext}")
            await storage.stat_inc(ai_requests=1, ai_voice=1)
        except Exception as e:
            log.warning("Ovozli xabar matnga o'girilmadi (%s)", type(e).__name__)
            await status.edit("❌ Ovozni matnga o'gira olmadim. Yozib yuboring yoki qayta urinib ko'ring.")
            return
        if not text:
            await status.edit("🤔 Ovozda gap eshitilmadi.")
            return
        await status.edit(f"🎤 Eshitdim: <i>{html.escape(text[:500])}</i>", parse_mode="html")
        if deleted_accounts.asks_for_list(text):
            await _show_gone(event, text)
            return
        if (req := watchers.parse(text)) is not None:  # «Ali online bo'lsa xabar ber»
            await _watch_request(event, req)
            return
        if finder.looks_like_search(text):
            await _run_find(event, text)
            return
        if not config.FINANCE_ENABLED:
            await event.respond("Qidirish uchun ayting, masalan: «Ali yuborgan manzilni topib ber».")
            return
        if not await _fin_record(event, text, "voice"):
            await event.respond("Bu kirim-chiqimga o'xshamadi. Masalan: «nonga 25 ming ishlatdim» "
                                "yoki «Alidan 200 ming oldim».")

    @bot.on(events.NewMessage(pattern=r"^/fin$", func=is_owner))
    async def _fin(event):
        await event.respond(await finance.make_report("today"), parse_mode="html", buttons=_fin_panel_buttons())

    @bot.on(events.NewMessage(pattern=r"^/(today|days3|week|month)$", func=is_owner))
    async def _fin_period(event):
        name = FIN_PERIOD_CMDS[event.pattern_match.group(1)]
        await event.respond(await finance.make_report(name), parse_mode="html", buttons=_fin_panel_buttons())

    @bot.on(events.NewMessage(pattern=r"^/balance$", func=is_owner))
    async def _fin_balance(event):
        await event.respond(await finance.make_balance(), parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"^/history$", func=is_owner))
    async def _fin_hist(event):
        text, buttons = await _fin_history()
        await event.respond(text, parse_mode="html", buttons=buttons)

    @bot.on(events.NewMessage(pattern=r"^/undo$", func=is_owner))
    async def _fin_undo(event):
        batch = await storage.last_batch()
        n = await storage.delete_batch(batch) if batch else 0
        await event.respond(f"↩️ Oxirgi yozuv bekor qilindi ({n} ta)." if n else "Bekor qiladigan yozuv yo'q.")

    @bot.on(events.NewMessage(pattern=r"^/budget(?:\s+(\S+)(?:\s+(.+))?)?$", func=is_owner))
    async def _fin_budget(event):
        cat, raw = event.pattern_match.group(1), event.pattern_match.group(2)
        if not cat:
            await event.respond(await _fin_budget_text(), parse_mode="html")
            return
        cat = "jami" if cat.lower() in ("jami", "total", "umumiy") else finance._norm_cat("expense", cat)
        if raw is None:
            await event.respond("Summa yozing: <code>/budget oziq-ovqat 2000000</code>", parse_mode="html")
            return
        if raw.strip().lower() in ("off", "0", "-", "o'chir"):
            await storage.set_budget(cat, None)
            await event.respond(f"🗑 «{cat}» byudjeti olib tashlandi.")
            return
        amount = _parse_budget_amount(raw.strip())
        if amount <= 0:
            await event.respond("Summa noto'g'ri. Masalan: 2000000 yoki 2mln")
            return
        await storage.set_budget(cat, amount)
        await event.respond(f"🎯 «{cat}» oylik byudjeti: {finance.fmt_sum(amount)}\n\n"
                            + await _fin_budget_text(), parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"^/export(?:\s+(\S+))?$", func=is_owner))
    async def _fin_export_cmd(event):
        name = (event.pattern_match.group(1) or "month").lower()
        name = {"hafta": "7", "week": "7", "oy": "month", "hammasi": "all"}.get(name, name)
        if name not in ("3", "7", "30", "month", "all", "today"):
            await event.respond("Foydalanish: /export, /export 7, /export 30, /export all")
            return
        await _send_export(name)

    @bot.on(events.CallbackQuery(pattern=rb"^fin:", func=is_owner))
    async def _fin_callback(event):
        parts = event.data.decode().split(":")
        action, arg = parts[1], parts[2] if len(parts) > 2 else ""
        if action == "rep" and arg in finance.PERIODS:
            await event.answer()
            await event.respond(await finance.make_report(arg), parse_mode="html", buttons=_fin_panel_buttons())
        elif action == "balance":
            await event.answer()
            await event.respond(await finance.make_balance(), parse_mode="html")
        elif action == "history":
            await event.answer()
            text, buttons = await _fin_history()
            await event.respond(text, parse_mode="html", buttons=buttons)
        elif action == "budget":
            await event.answer()
            await event.respond(await _fin_budget_text(), parse_mode="html")
        elif action == "export":
            await event.answer("Tayyorlanmoqda...")
            await _send_export(arg)
        elif action == "undo":
            n = await storage.delete_batch(arg)
            await event.answer(f"Bekor qilindi ({n} ta)" if n else "Allaqachon o'chirilgan")
            if n:
                try:
                    await event.edit("↩️ <s>Bekor qilindi</s>", parse_mode="html")
                except Exception:
                    pass
        elif action == "del":
            ok = await storage.delete_transaction(arg)
            await event.answer("🗑 O'chirildi" if ok else "Allaqachon o'chirilgan")
            text, buttons = await _fin_history()
            try:
                await event.edit(text, parse_mode="html", buttons=buttons)
            except Exception:
                pass
        elif action == "cat":
            tx = await storage.get_transaction(arg)
            if not tx:
                await event.answer("Yozuv topilmadi")
                return
            cats = list(finance.cats_for(tx["type"]))
            btns = [Button.inline(f"{finance.cat_icon(tx['type'], c)} {c}", f"fin:setc:{arg}:{i}".encode())
                    for i, c in enumerate(cats)]
            await event.answer()
            await event.respond(f"✏️ «{html.escape(tx.get('note') or '')}» uchun kategoriya:",
                                parse_mode="html", buttons=[btns[i:i + 3] for i in range(0, len(btns), 3)])
        elif action == "setc":
            tx = await storage.get_transaction(arg)
            cats = list(finance.cats_for(tx["type"])) if tx else []
            idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
            if not tx or not 0 <= idx < len(cats):
                await event.answer("Yozuv topilmadi")
                return
            await storage.update_transaction(arg, category=cats[idx])
            await event.answer(f"✅ {cats[idx]}")
            try:
                await event.edit(f"✅ «{html.escape(tx.get('note') or '')}» → {cats[idx]}", parse_mode="html")
            except Exception:
                pass
        else:
            await event.answer()

    # Panel tugmalari: cfg:<amal>
    @bot.on(events.CallbackQuery(pattern=rb"^cfg:", func=is_owner))
    async def _cfg_callback(event):
        action = event.data.decode().split(":", 1)[1]
        result = await _do_action(action)
        await event.answer(result[:190])
        text, buttons = await _panel()
        try:
            await event.edit(text, parse_mode="html", buttons=buttons)
        except Exception:
            pass  # panel o'zgarmagan bo'lsa Telegram xato qaytaradi — e'tiborsiz

    # Oddiy matn: tugma orqali yoki xulosaga reply qilib javob yozish
    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and e.text and not e.text.startswith("/")
                              and e.raw_text.strip() not in MENU_TEXTS))
    async def _plain(event):
        if tg_login.busy():
            return   # bot orqali Telegram login (telefon, kod, parol) davom etyapti — tg_login o'zi ishlaydi
        # ☁️ Google Drive'ni qayta ulash: brauzer manzil qatoridagi http://127.0.0.1:8765/?...code=...
        if gdrive.parse_redirect(event.text):
            await _drive_finish(event)
            return
        # Instagram login paytida tasdiqlash kodi kutilmoqda
        if instagram.waiting_code():
            if instagram.submit_code(event.text):
                await event.respond("⏳ Kod Instagram'ga yuborildi, tekshirilmoqda...")
                return
            if event.text.strip().lower() in ("bekor", "cancel", "stop", "отмена"):
                instagram.cancel_code()
                await event.respond("❌ Instagram'ga kirish bekor qilindi.")
                return
        # «➕ Yangi eslatma» bosilgan -> keyingi matn eslatma deb tushuniladi
        if _awaiting_reply.get("watch") and not event.is_reply:
            if event.text.strip().lower() in ("bekor", "cancel", "stop", "отмена"):
                _awaiting_reply.pop("watch", None)
                await event.respond("❌ Bekor qilindi.")
                return
            req = watchers.parse(event.text, strict=False)
            if req is None:
                await event.respond("🤔 Tushunmadim. Raqam (<code>+998 90 123 45 67</code>), "
                                    "<code>@username</code> yoki <code>id 123456789</code> yozing. "
                                    "Bekor qilish: «bekor»", parse_mode="html")
                return
            _awaiting_reply.pop("watch", None)
            await _watch_request(event, req)
            return
        # Instagram bildirishnomasiga reply yoki «✍️ Javob berish» -> matn Instagram chatiga ketadi
        ig_thread = _ig_notif_map.get(event.reply_to_msg_id) if event.is_reply else None
        if ig_thread is None and not event.is_reply:
            ig_thread = _awaiting_reply.pop("ig", None)
        if ig_thread:
            await event.respond(await instagram.send_text(ig_thread, event.text))
            return
        chat_id, slot = None, 1
        if event.is_reply:
            chat_id = _notif_map.get(event.reply_to_msg_id)
            slot = _notif_slot.get(event.reply_to_msg_id, 1)
        # Bildirishnomaga reply: «shu user online bo'lsa menga xabar ber» — suhbatdoshga yuborilmaydi,
        # o'sha odamga eslatma qo'yiladi
        if (chat_id is not None and slot == 1 and watchers.about_this(event.text)
                and (req := watchers.parse(event.text)) is not None):
            if not (req.user_ref or req.number):
                req.kind, req.user_ref, req.name = "online", str(chat_id), ""
            await _watch_request(event, req, "\nℹ️ Matn suhbatdoshga yuborilmadi — eslatma deb tushundim.")
            return
        # Javob yozish kutilmayapti va matnda xabar havolasi bor -> mediani Saqlangan xabarlarga joylaymiz
        if chat_id is None and "chat" not in _awaiting_reply:
            links = saver.parse_links(event.text)
            if links:
                await _save_links(event, links)
                return
            # Instagram post/reels/storis havolasi -> mediani yuklab beradi
            if instagram.has_media_link(event.text):
                await _download_ig_links(event, instagram.parse_media_links(event.text))
                return
        if chat_id is None:
            chat_id = _awaiting_reply.pop("chat", None)
            slot = _awaiting_slot.pop("chat", 1) if chat_id is not None else 1
        # Faqat ID raqami yuborildi -> o'sha odamning lichkasiga o'tish tugmasi
        if chat_id is None and (m := USER_ID_RE.match(event.text)):
            await _open_dm(event, m.group(1))
            return
        # «o'chirilgan akkauntlarni ko'rsat» -> ro'yxat (taxminiy ismlari bilan)
        if chat_id is None and deleted_accounts.asks_for_list(event.text):
            await _show_gone(event, event.text)
            return
        # «95 446 68 86 raqami tg ga qo'shilsa xabar ber», «@ali online bo'lsa xabar ber» -> eslatma
        if chat_id is None and (req := watchers.parse(event.text)) is not None:
            await _watch_request(event, req)
            return
        # «Ali menga tashlagan usernameni top» -> chatdan qidirish
        if chat_id is None and finder.looks_like_search(event.text):
            await _run_find(event, event.text)
            return
        # Matn faqat raqam yoki @username bo'lsa — bu eslatma, buxgalterga (va AI'ga) bermaymiz
        if chat_id is None and ONLY_REF_RE.match(event.text or ""):
            await _dont_understand(event)
            return
        # Hech kimga javob kutilmayapti -> balki bu kirim-chiqim ("nonga 25 ming ishlatdim")
        if chat_id is None and config.FINANCE_ENABLED and await _fin_record(event, event.text, "text"):
            return
        if chat_id is None:
            await _dont_understand(event)
            return
        _awaiting_reply.pop("chat", None)
        await _send_as_owner(chat_id, event.text, event, slot)

    @bot.on(events.CallbackQuery(pattern=rb"^(reply|done|mute|ignore):", func=is_owner))
    async def _callback(event):
        action, _, raw = event.data.decode().partition(":")
        raw_id, _, raw_slot = raw.partition(":")
        chat_id, slot = int(raw_id), int(raw_slot or 1)
        if action == "reply":
            _awaiting_reply.pop("ig", None)
            _awaiting_reply["chat"] = chat_id
            _awaiting_slot["chat"] = slot
            await event.answer()
            via = f" ({slot}-akkauntdan)" if slot != 1 else ""
            await event.respond(f"✍️ <code>{chat_id}</code> ga javob matnini yozing{via}:", parse_mode="html")
        elif action == "done":
            await storage.mark_handled(chat_id)
            await event.answer("Ko'rildi deb belgilandi")
        elif action == "mute":
            await storage.mute(chat_id, OWNER_TAKEOVER_MINUTES * 60)
            await event.answer(f"Bu chatda AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi")
        elif action == "ignore":
            await storage.add_ignored(chat_id, str(chat_id))
            await event.answer("Istisnoga qo'shildi — endi avtojavob berilmaydi")
