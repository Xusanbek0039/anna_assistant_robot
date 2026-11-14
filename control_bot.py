"""Nazorat boti: egasiga xulosalar yuboradi va buyruqlarni qabul qiladi."""
import html
import io
import logging
import time

from telethon import Button, TelegramClient, events

import ai
import availability
import bio_updater
import config
import finance
import report
import runtime
import saver
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
    "Xulosa xabariga <i>reply</i> qilib yozsangiz, matn o'sha odamga sizning nomingizdan ketadi."
)

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
        await event.respond(HELP + "\n\n💡 Pastdagi <b>/</b> yoki ☰ <b>Menyu</b> tugmasini bosing — "
                                   "barcha buyruqlar chiqadi.", parse_mode="html")

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

    # ---------- shaxsiy buxgalter ----------
    # Egasining ovozli xabari: matnga o'giriladi va kirim-chiqim sifatida yoziladi
    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and (e.voice or e.audio or e.video_note)))
    async def _fin_voice(event):
        if not config.FINANCE_ENABLED:
            return
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
    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and e.text and not e.text.startswith("/")))
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
