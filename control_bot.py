"""Nazorat boti: egasiga xulosalar yuboradi va buyruqlarni qabul qiladi."""
import html
import logging

from telethon import Button, TelegramClient, events

import availability
import bio_updater
import config
import report
import runtime
import storage
from config import OWNER_TAKEOVER_MINUTES

log = logging.getLogger("bot")

HELP = (
    "<b>Anna — avtojavob kotiba</b>\n\n"
    "/auto — aqlli rejim: faqat band bo'lganingizda javob beradi\n"
    "/on — doim javob beradi\n"
    "/off — avtojavobni o'chirish\n"
    "/status — holat\n"
    "/tag &lt;id yoki @username&gt; &lt;oila|mijoz|dost|clear&gt; — kontakt turi\n"
    "/tags — turlar ro'yxati\n\n"
    "<b>Hisobot va nazorat</b>\n"
    "/report — bugungi hisobot (har kuni kechqurun avtomatik ham keladi)\n"
    "/usage — bugungi AI sarfi va limitlar\n"
    "/health — MongoDB, OpenAI, Telegram holati\n\n"
    "<b>Profil</b>\n"
    "/bio — joriy bio va holat\n"
    "/bio on | off | now | restore — yoqish, to'xtatish, hozir yangilash, asl bio'ni qaytarish\n"
    "/quote &lt;matn&gt; — doimiy iqtibos, /quote auto — har kuni almashadi, /quote off — iqtibossiz\n"
    "/online on | off — akkauntni online ko'rsatish\n\n"
    "<b>Boshqa</b>\n"
    "/inbox — javob berilmagan xabarlar\n"
    "/reply &lt;chat_id&gt; &lt;matn&gt; — sizning nomingizdan javob yozish\n"
    "/ignore &lt;id yoki @username&gt; — bu odamga avtojavob bermaslik\n"
    "/unignore &lt;id&gt; — istisnodan chiqarish\n"
    "/ignored — istisnolar ro'yxati\n\n"
    "Xulosa xabariga <i>reply</i> qilib yozsangiz, matn o'sha odamga sizning nomingizdan ketadi."
)

MODE_NAMES = {"on": "🟢 doim yoqiq (/on)", "off": "⚪️ o'chirilgan (/off)", "auto": "🤖 aqlli (/auto)"}
TAGS = {
    "oila": "👨‍👩‍👧 oila — kotiba javob bermaydi, faqat sizga xabar beradi",
    "mijoz": "💼 mijoz — rasmiy ohang",
    "dost": "🤝 do'st — samimiy ohang",
}
TAG_ALIASES = {"do'st": "dost", "dost": "dost", "family": "oila", "client": "mijoz", "friend": "dost"}

# notification message id -> chat_id  (reply orqali javob berish uchun)
_notif_map: dict[int, int] = {}
# egasi "Javob berish" tugmasini bosgandan keyin kutilayotgan chat
_awaiting_reply: dict[str, int] = {}


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
    ]


async def notify(chat_id: int, name: str, username: str | None, text: str,
                 summary: str, urgent: bool, ai_reply: str | None) -> None:
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
                                         buttons=_buttons(chat_id))
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


async def send_media(chat_id: int, name: str, data: bytes, filename: str | None) -> None:
    try:
        file = data
        if filename:
            import io
            file = io.BytesIO(data)
            file.name = filename
        msg = await ctx.bot.send_file(ctx.owner_id, file,
                                      caption=f"📎 {name} yubordi ({chat_id})")
        _notif_map[msg.id] = chat_id
    except Exception:
        log.exception("Media botga yuborilmadi")


async def _send_as_owner(chat_id: int, text: str, event) -> None:
    try:
        # Egasi nomidan yuboriladi; userbot buni "egasi o'zi yozdi" deb ko'radi va AI jim bo'ladi
        await ctx.user.send_message(chat_id, text)
        await event.respond(f"✅ Yuborildi. Bu chatda AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi.")
    except Exception as e:
        await event.respond(f"❌ Yuborilmadi: {e}")


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


def setup(bot: TelegramClient, user: TelegramClient, owner_id: int) -> None:
    ctx.bot, ctx.user, ctx.owner_id = bot, user, owner_id
    is_owner = lambda e: e.sender_id == owner_id  # noqa: E731

    @bot.on(events.NewMessage(pattern=r"^/(start|help)", func=is_owner))
    async def _help(event):
        if event.raw_text.startswith("/start"):
            await set_commands(bot, owner_id)  # birinchi /start dan keyin menyu faqat egasiga o'rnatiladi
        await event.respond(HELP + "\n\n💡 Pastdagi <b>/</b> yoki ☰ <b>Menyu</b> tugmasini bosing — "
                                   "barcha buyruqlar chiqadi.", parse_mode="html")

    @bot.on(events.NewMessage(func=lambda e: e.sender_id != owner_id and e.is_private))
    async def _stranger(event):
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
                                      for name, p in results.items()))

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
    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and e.text and not e.text.startswith("/")))
    async def _plain(event):
        chat_id = None
        if event.is_reply:
            chat_id = _notif_map.get(event.reply_to_msg_id)
        if chat_id is None:
            chat_id = _awaiting_reply.pop("chat", None)
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
