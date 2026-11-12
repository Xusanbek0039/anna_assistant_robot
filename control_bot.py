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


def setup(bot: TelegramClient, user: TelegramClient, owner_id: int) -> None:
    ctx.bot, ctx.user, ctx.owner_id = bot, user, owner_id
    is_owner = lambda e: e.sender_id == owner_id  # noqa: E731

    @bot.on(events.NewMessage(pattern=r"^/(start|help)", func=is_owner))
    async def _help(event):
        await event.respond(HELP, parse_mode="html")

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
        mode = await storage.get_mode()
        st = await availability.get_status()
        await event.respond(
            f"Rejim: {MODE_NAMES[mode]}\n"
            f"Hozir: {'🟢 javob beradi' if st.reply else '⚪️ jim'} — {st.reason}\n"
            f"Oxirgi 24 soatda kelgan xabarlar: {await storage.today_count()}\n"
            f"Javob kutayotganlar: {len(await storage.pending_inbox())}"
        )

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
    @bot.on(events.NewMessage(pattern=r"^/bio(?:\s+(\S+))?$", func=is_owner))
    async def _bio(event):
        arg = (event.pattern_match.group(1) or "").lower()
        upd = bio_updater.INSTANCE
        if arg == "on":
            await runtime.update(bio_enabled=True)
            await event.respond("🟢 Bio yangilash yoqildi.")
        elif arg == "off":
            await runtime.update(bio_enabled=False)
            await event.respond("⏸ Bio yangilash to'xtatildi. Bio oxirgi holatida qoladi. "
                                "Asl bio kerak bo'lsa: /bio restore")
        elif arg == "now":
            if upd is None:
                await event.respond("Bio moduli ishlamayapti (BIO_ENABLED=false).")
                return
            await runtime.update()  # siklni uyg'otadi
            await event.respond("🔄 Bio hozir yangilanadi.")
        elif arg == "restore":
            if upd is None:
                await event.respond("Bio moduli ishlamayapti (BIO_ENABLED=false).")
                return
            await runtime.update(bio_enabled=False)
            await upd.restore()
            original = await bio_updater.get_original_bio()
            await event.respond(f"↩️ Asl bio qaytarildi va avtomatik yangilash to'xtatildi:\n{original!r}\n"
                                "Qayta yoqish: /bio on")
        elif arg:
            await event.respond("Foydalanish: /bio, /bio on, /bio off, /bio now, /bio restore")
        else:
            state = "🟢 yoqiq" if runtime.get("bio_enabled") else "⏸ to'xtatilgan"
            current = upd.current_bio if upd else None
            qmode = {"auto": "har kuni almashadi (quotes.txt)", "off": "o'chirilgan",
                     "fixed": f"doimiy: “{runtime.get('quote_text')}”"}[runtime.get("quote_mode")]
            await event.respond(
                f"Bio yangilash: {state}{' (DRY_RUN)' if config.DRY_RUN else ''}\n"
                f"Joriy bio: {current!r}\n"
                f"Iqtibos: {qmode}\n"
                f"Online: {'🟢 yoqiq' if runtime.get('online') else '⚪️ o‘chiq'}"
            )

    @bot.on(events.NewMessage(pattern=r"^/quote(?:\s+([\s\S]+))?$", func=is_owner))
    async def _quote(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await event.respond("Foydalanish: /quote <matn>, /quote auto, /quote off")
        elif arg.lower() == "auto":
            await runtime.update(quote_mode="auto")
            await event.respond("🔁 Iqtibos har kuni quotes.txt dan almashadi.")
        elif arg.lower() == "off":
            await runtime.update(quote_mode="off")
            await event.respond("🚫 Bio iqtibossiz chiqadi.")
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
            await runtime.update(online=(arg == "on"))
            await event.respond("🟢 Akkaunt online ko'rinadi." if arg == "on"
                                else "⚪️ Online ko'rsatish o'chirildi.")
        else:
            await event.respond(f"Online: {'🟢 yoqiq' if runtime.get('online') else '⚪️ o‘chiq'}\n"
                                "Foydalanish: /online on, /online off")

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

    @bot.on(events.CallbackQuery(func=is_owner))
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
