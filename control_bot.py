"""Nazorat boti: egasiga xulosalar yuboradi va buyruqlarni qabul qiladi."""
import html
import io
import itertools
import logging
import time

from telethon import Button, TelegramClient, events

import ai
import archive
import availability
import bio_updater
import config
import finance
import finder
import gdrive
import report
import runtime
import saver
import storage
import voicelab
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
    "/arxiv — arxiv paneli, /arxiv &lt;ism&gt; — chatni nomi bo'yicha topish\n\n"
    "<b>Profil</b>\n"
    "/bio — joriy bio va holat\n"
    "/bio on | off | now | restore — yoqish, to'xtatish, hozir yangilash, asl bio'ni qaytarish\n"
    "/quote &lt;matn&gt; — doimiy iqtibos, /quote auto — har kuni almashadi, /quote off — iqtibossiz\n"
    "/online on | off — akkauntni online ko'rsatish\n\n"
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
MENU_TEXTS = {MENU_PANEL, MENU_INBOX, MENU_ARCHIVE, MENU_FIN, MENU_FIND, MENU_REPORT, MENU_HELP}


def main_keyboard() -> list:
    return [
        [Button.text(MENU_PANEL, resize=True, persistent=True), Button.text(MENU_INBOX)],
        [Button.text(MENU_ARCHIVE), Button.text(MENU_FIN)],
        [Button.text(MENU_FIND), Button.text(MENU_REPORT), Button.text(MENU_HELP)],
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
# egasi "Javob berish" tugmasini bosgandan keyin kutilayotgan chat
_awaiting_reply: dict[str, int] = {}
# chatlardan qidirish: tanlov kutayotgan so'rovlar va topilgan xabarlar (tugmalar uchun)
_find_pending: dict[str, tuple] = {}
_find_saved: dict[str, list[tuple[int, int]]] = {}
_find_seq = itertools.count(1)


class Ctx:
    bot: TelegramClient
    user: TelegramClient
    owner_id: int
    monitor = None  # health.Monitor (main.py o'rnatadi)


ctx = Ctx()


async def send_html(text: str) -> None:
    """Egasiga HTML xabar (kunlik hisobot uchun)."""
    await ctx.bot.send_message(ctx.owner_id, text, parse_mode="html")


def _who(name: str, username: str | None, chat_id: int) -> str:
    who = f"<b>{html.escape(name or 'Nomaʼlum')}</b>"
    if username:
        who += f" (@{html.escape(username)})"
    return who + f"  <code>{chat_id}</code>"


def _buttons(chat_id: int):
    return [
        [Button.inline("✍️ Javob berish", f"reply:{chat_id}".encode()),
         Button.inline("✅ Ko'rildi", f"done:{chat_id}".encode())],
        [Button.inline("🔇 Bu chatda AI jim", f"mute:{chat_id}".encode()),
         Button.inline("🚫 Istisnoga qo'shish", f"ignore:{chat_id}".encode())],
        [Button.inline("📄 Yozishmani yuklab olish (HTML)", f"arc:html:{chat_id}:a".encode())],
    ]


async def notify(chat_id: int, name: str, username: str | None, text: str,
                 summary: str, urgent: bool, ai_reply: str | None, reply_to: int | None = None) -> None:
    """reply_to: shu xabar bilan kelgan media (bot xabari) id si — bildirishnoma uning tagiga bog'lanadi."""
    head = "🔴 <b>SHOSHILINCH</b>\n" if urgent else "📩 <b>Yangi xabar</b>\n"
    body = (
        f"{head}👤 {_who(name, username, chat_id)}\n\n"
        f"💬 <i>{html.escape(text[:1000])}</i>\n"
    )
    if summary:
        body += f"\n📝 <b>Xulosa:</b> {html.escape(summary)}\n"
    if ai_reply:
        body += f"\n🤖 <b>Anna javobi:</b> {html.escape(ai_reply)}"
    try:
        msg = await ctx.bot.send_message(ctx.owner_id, body, parse_mode="html",
                                         buttons=_buttons(chat_id), reply_to=reply_to)
        _notif_map[msg.id] = chat_id
    except Exception:
        log.exception("Egasiga xabar yuborilmadi (botga /start bosganmisiz?)")


async def call_owner(chat_id: int, name: str, username: str | None, text: str, summary: str) -> None:
    """Suhbatdosh egasini chaqirishni so'radi — alohida, ko'zga tashlanadigan xabar."""
    body = (
        "📞 <b>SIZNI CHAQIRISHYAPTI!</b>\n"
        f"👤 {_who(name, username, chat_id)}\n\n"
        f"💬 <i>{html.escape(text[:1000])}</i>\n"
    )
    if summary:
        body += f"\n📝 <b>Xulosa:</b> {html.escape(summary)}"
    try:
        msg = await ctx.bot.send_message(ctx.owner_id, body, parse_mode="html",
                                         buttons=_buttons(chat_id))
        _notif_map[msg.id] = chat_id
    except Exception:
        log.exception("Chaqiruv xabari yuborilmadi (botga /start bosganmisiz?)")


async def alert(text: str) -> None:
    """Egasiga tizim ogohlantirishi (masalan, AI kaliti ishlamayapti)."""
    try:
        await ctx.bot.send_message(ctx.owner_id, text)
    except Exception:
        log.warning("Ogohlantirish botga yuborilmadi")


_MEDIA_ICONS = {"rasm": "📸", "video": "🎬", "ovozli xabar": "🎤", "video-xabar": "📹", "audio": "🎵", "fayl": "📎"}


async def send_media(chat_id: int, name: str, data: bytes, filename: str | None = None, *,
                     kind: str = "media", username: str | None = None, caption: str = "",
                     view_once: bool = False, mime: str = "", attributes=None,
                     thumb: bytes | None = None) -> int | None:
    """Suhbatdosh yuborgan mediani egasiga ASL ko'rinishida yuboradi: rasm — rasm, video — video,
    ovozli xabar — ovozli xabar. Bir marta ko'riladigan media ham oddiy (saqlasa bo'ladigan) holda keladi.
    Yuborilgan xabar id sini qaytaradi (bildirishnoma shunga reply bo'lib keladi)."""
    cap = f"{_MEDIA_ICONS.get(kind, '📎')} {_who(name, username, chat_id)} — {html.escape(kind)}"
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
        _notif_map[msg.id] = chat_id
        if is_round:  # dumaloq videoga izoh yozib bo'lmaydi — alohida xabar
            note = await ctx.bot.send_message(ctx.owner_id, cap, parse_mode="html", reply_to=msg.id)
            _notif_map[note.id] = chat_id
        return msg.id
    except Exception:
        log.exception("Media botga yuborilmadi")
        return None


async def _send_as_owner(chat_id: int, text: str, event) -> None:
    try:
        # Egasi nomidan yuboriladi; userbot buni "egasi o'zi yozdi" deb ko'radi va AI jim bo'ladi
        await ctx.user.send_message(chat_id, text)
        await event.respond(f"✅ Yuborildi. Bu chatda AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi.")
    except Exception as e:
        await event.respond(f"❌ Yuborilmadi: {e}")


async def _save_links(event, links: list) -> None:
    """Havolalardagi mediani egasining Saqlangan xabarlariga joylaydi; jarayon bitta xabarda ko'rsatiladi."""
    many = len(links) > 1
    status = await event.respond("⏳ Saqlanmoqda...")
    done: list[str] = []

    async def show(text: str) -> None:
        try:
            await status.edit("\n".join(done + ([text] if text else [])), link_preview=False)
        except Exception:
            pass  # matn o'zgarmagan yoki Telegram cheklovi — jarayon ko'rsatkichi muhim emas

    for i, link in enumerate(links, 1):
        prefix = f"{i}/{len(links)} " if many else ""
        result = await saver.save_link(ctx.user, link, lambda t, p=prefix: show(f"{p}{t}"))
        done.append(f"{prefix}{result}" + (f" — {link.url}" if many else ""))
        await show("⏳ Keyingisi..." if i < len(links) else "")


async def _resolve_user_id(arg: str) -> int | None:
    arg = arg.strip()
    if arg.lstrip("-").isdigit():
        return int(arg)
    try:
        ent = await ctx.user.get_entity(arg)
        return ent.id
    except Exception:
        return None


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
    ("find", "🔎 Chatlardan istalgan narsani topish"),
    ("save", "💾 Havoladagi mediani Saqlangan xabarlarga joylash"),
    ("bio", "🕒 Bio, iqtibos va online — tugmali panel"),
    ("quote", "💬 Iqtibos: /quote matn | auto | off"),
    ("online", "🟢 Online ko'rinish: on | off"),
    ("usage", "💸 Bugungi AI sarfi va limitlar"),
    ("health", "🩺 MongoDB, OpenAI, Telegram holati"),
    ("tags", "🏷 Kontakt turlari ro'yxati"),
    ("tag", "🏷 Tur berish: /tag @user oila|mijoz|dost"),
    ("reply", "✍️ Javob: /reply chat_id matn"),
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
    lines = [head, f"👤 {_who(info.get('title') or '', info.get('username'), chat_id)}",
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
        [Button.inline("⏸ Arxivni to'xtatish" if on else "▶️ Arxivni yoqish", b"arc:set:archive_enabled"),
         Button.inline("🔕 Xabar bermasin" if alerts else "🔔 Xabar bersin", b"arc:set:archive_alerts")],
        [Button.inline("⚪️ Guruhlarsiz" if groups else "👥 Guruhlar ham", b"arc:set:archive_groups")],
    ]
    if gdrive.enabled():
        buttons[-1].append(Button.inline("☁️ Drive'ga hozir yuklash", b"arc:drive"))
        media_on = runtime.get("drive_media")
        buttons.append([Button.inline("⏸ Rasm/video Drive'ga — to'xtatish" if media_on
                                      else "🖼 Rasm/videoni Drive'ga yuklash", b"arc:set:drive_media")])
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
        label = (r.get("title") or str(r["chat_id"]))[:28]
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
    text = (f"💬 {_who(info.get('title') or '', info.get('username'), chat_id)}\n"
            f"💾 Arxivda: {info.get('count', 0)} ta xabar · 🗑 o'chirilgan: {info.get('deleted', 0)}\n"
            f"Oxirgi xabar: {_when(info.get('last')) or '—'}\n\n"
            "«Butun yozishma» ga Telegram'da hali turgan xabarlar ham qo'shiladi.")
    buttons = _html_buttons(chat_id)
    if info.get("drive_link"):
        buttons.append([Button.url("☁️ Google Drive'da ochish", info["drive_link"])])
    return text, buttons + [[Button.inline("⬅️ Chatlar", b"arc:list:0")]]


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
        if chat_id is None and not keywords and not q.media:
            await status.edit("Nimani qidiray? Masalan: «Ali yuborgan manzilni top» yoki «wifi parolini qidir».")
            return
        items = await finder.collect(ctx.user, chat_id, q, keywords)
        if not items and q.only_them:
            q.only_them = False
            items = await finder.collect(ctx.user, chat_id, q, keywords)
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
        if picked:
            key = _remember(_find_saved, [(f.chat_id, f.id) for f in picked])
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
    is_owner = lambda e: e.sender_id == owner_id  # noqa: E731

    @bot.on(events.NewMessage(pattern=r"^/(start|help)", func=is_owner))
    async def _help(event):
        if event.raw_text.startswith("/start"):
            await set_commands(bot, owner_id)  # birinchi /start dan keyin menyu faqat egasiga o'rnatiladi
        await event.respond(HELP, parse_mode="html", buttons=main_keyboard())

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
        elif choice == MENU_HELP:
            await event.respond(HELP, parse_mode="html", buttons=main_keyboard())

    @bot.on(events.NewMessage(pattern=r"^/arxiv(?:\s+([\s\S]+))?$", func=is_owner))
    async def _arxiv(event):
        name = (event.pattern_match.group(1) or "").strip()
        text, buttons = await (_archive_list(0, False, name) if name else _archive_panel())
        await event.respond(text, parse_mode="html", buttons=buttons)

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
        await event.answer()
        if action == "set" and arg in ("archive_enabled", "archive_alerts", "archive_groups", "drive_media"):
            await runtime.update(**{arg: not runtime.get(arg)})
            text, buttons = await _archive_panel()
        elif action in ("list", "dlist"):
            text, buttons = await _archive_list(int(arg or 0), action == "dlist")
        elif action == "chat":
            text, buttons = await _archive_chat_view(int(arg))
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
                            + f"\n\n🎤 {voicelab.describe()}"
                            + f"\n☁️ Google Drive: {await gdrive.check() or 'ishlayapti'}")

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
        chat_id = None
        if event.is_reply:
            chat_id = _notif_map.get(event.reply_to_msg_id)
        # Javob yozish kutilmayapti va matnda xabar havolasi bor -> mediani Saqlangan xabarlarga joylaymiz
        if chat_id is None and "chat" not in _awaiting_reply:
            links = saver.parse_links(event.text)
            if links:
                await _save_links(event, links)
                return
        if chat_id is None:
            chat_id = _awaiting_reply.pop("chat", None)
        # «Ali menga tashlagan usernameni top» -> chatdan qidirish
        if chat_id is None and finder.looks_like_search(event.text):
            await _run_find(event, event.text)
            return
        # Hech kimga javob kutilmayapti -> balki bu kirim-chiqim ("nonga 25 ming ishlatdim")
        if chat_id is None and config.FINANCE_ENABLED and await _fin_record(event, event.text, "text"):
            return
        if chat_id is None:
            await event.respond("Kimga javob yozmoqchisiz? Xulosa xabariga reply qiling "
                                "yoki «✍️ Javob berish» tugmasini bosing.")
            return
        _awaiting_reply.pop("chat", None)
        await _send_as_owner(chat_id, event.text, event)

    @bot.on(events.CallbackQuery(pattern=rb"^(reply|done|mute|ignore):", func=is_owner))
    async def _callback(event):
        action, _, raw = event.data.decode().partition(":")
        chat_id = int(raw)
        if action == "reply":
            _awaiting_reply["chat"] = chat_id
            await event.answer()
            await event.respond(f"✍️ <code>{chat_id}</code> ga javob matnini yozing:", parse_mode="html")
        elif action == "done":
            await storage.mark_handled(chat_id)
            await event.answer("Ko'rildi deb belgilandi")
        elif action == "mute":
            await storage.mute(chat_id, OWNER_TAKEOVER_MINUTES * 60)
            await event.answer(f"Bu chatda AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi")
        elif action == "ignore":
            await storage.add_ignored(chat_id, str(chat_id))
            await event.answer("Istisnoga qo'shildi — endi avtojavob berilmaydi")
