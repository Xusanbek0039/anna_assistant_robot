"""Nazorat boti: egasiga xulosalar yuboradi va buyruqlarni qabul qiladi."""
import html
import logging

from telethon import Button, TelegramClient, events

import storage
from config import OWNER_TAKEOVER_MINUTES

log = logging.getLogger("bot")

HELP = (
    "<b>Anna — avtojavob kotiba</b>\n\n"
    "/on — avtojavobni yoqish\n"
    "/off — avtojavobni o'chirish\n"
    "/status — holat\n"
    "/inbox — javob berilmagan xabarlar\n"
    "/reply &lt;chat_id&gt; &lt;matn&gt; — sizning nomingizdan javob yozish\n"
    "/ignore &lt;id yoki @username&gt; — bu odamga avtojavob bermaslik\n"
    "/unignore &lt;id&gt; — istisnodan chiqarish\n"
    "/ignored — istisnolar ro'yxati\n\n"
    "Xulosa xabariga <i>reply</i> qilib yozsangiz, matn o'sha odamga sizning nomingizdan ketadi."
)

# notification message id -> chat_id  (reply orqali javob berish uchun)
_notif_map: dict[int, int] = {}
# egasi "Javob berish" tugmasini bosgandan keyin kutilayotgan chat
_awaiting_reply: dict[str, int] = {}


class Ctx:
    bot: TelegramClient
    user: TelegramClient
    owner_id: int


ctx = Ctx()


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
        storage.set_enabled(True)
        await event.respond("🟢 Avtojavob yoqildi.")

    @bot.on(events.NewMessage(pattern=r"^/off$", func=is_owner))
    async def _off(event):
        storage.set_enabled(False)
        await event.respond("⚪️ Avtojavob o'chirildi.")

    @bot.on(events.NewMessage(pattern=r"^/status$", func=is_owner))
    async def _status(event):
        state = "🟢 yoqilgan" if storage.is_enabled() else "⚪️ o'chirilgan"
        await event.respond(
            f"Avtojavob: {state}\n"
            f"Oxirgi 24 soatda kelgan xabarlar: {storage.today_count()}\n"
            f"Javob kutayotganlar: {len(storage.pending_inbox())}"
        )

    @bot.on(events.NewMessage(pattern=r"^/inbox$", func=is_owner))
    async def _inbox(event):
        rows = storage.pending_inbox()
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
        storage.add_ignored(uid, arg)
        await event.respond(f"🚫 {arg} ({uid}) ga endi avtojavob berilmaydi.")

    @bot.on(events.NewMessage(pattern=r"^/unignore\s+(\S+)", func=is_owner))
    async def _unignore(event):
        uid = await _resolve_user_id(event.pattern_match.group(1))
        ok = uid is not None and storage.remove_ignored(uid)
        await event.respond("✅ Istisnodan chiqarildi." if ok else "❌ Ro'yxatda topilmadi.")

    @bot.on(events.NewMessage(pattern=r"^/ignored$", func=is_owner))
    async def _ignored(event):
        rows = storage.list_ignored()
        if not rows:
            await event.respond("Istisnolar ro'yxati bo'sh.")
            return
        await event.respond("\n".join(f"• {r['label']} — <code>{r['user_id']}</code>" for r in rows),
                            parse_mode="html")

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
            storage.mark_handled(chat_id)
            await event.answer("Ko'rildi deb belgilandi")
        elif action == "mute":
            storage.mute(chat_id, OWNER_TAKEOVER_MINUTES * 60)
            await event.answer(f"Bu chatda AI {OWNER_TAKEOVER_MINUTES} daqiqa jim turadi")
        elif action == "ignore":
            storage.add_ignored(chat_id, str(chat_id))
            await event.answer("Istisnoga qo'shildi — endi avtojavob berilmaydi")
