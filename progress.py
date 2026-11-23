"""⏳ Jonli «yuklanmoqda» ko'rsatkichi — ikki rejimda.

1. 🎨 **HTML widget** (asosiy) — Telegram'ning yangi imkoniyati: bot xabar ichiga HTML/CSS/JS
   joylaydi va Telegram uni o'zi render qiladi. Progress bar CSS `transition` bilan SILLIQ suriladi,
   ustidan yorug'lik to'lqini (shimmer) to'xtovsiz o'tadi — bularning hammasi klient tomonida,
   bizning tahrirlashimizga bog'liq emas. Faqat holat (`chat_data`) yangilanadi, HTML o'zgarmaydi.
   Widget faqat shaxsiy chatlarda ishlaydi — bizda bot egasiga yozadi, shuning uchun mos.

2. 📝 **Matn** (zaxira) — widget ishlamasa: Braille spinner va `▰░` bar, bar ning bo'sh qismi
   Telegram spoileri ostida (Telegram uni o'zi qaynatib turadi).

Ikki rejimning interfeysi bir xil, shuning uchun `instagram.py` va `saver.py` ga tegilmaydi:
    bar.set(matn, ulush, done=baytlar, total=baytlar)
"""
import asyncio
import json
import logging
import time

from telethon.tl.functions.messages import EditMessageWidgetDataRequest, SendMediaRequest
from telethon.tl.types import InputMediaWidget, MessageEntitySpoiler

log = logging.getLogger("progress")

# ---------- umumiy ----------
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"        # Braille spinner: 10 kadr, silliq aylanadi
BAR_FULL, BAR_EMPTY = "▰", "░"   # bo'sh qism Telegram spoileri ostiga olinadi
BAR_LEN = 12
PROGRESS_EVERY = 1.1      # matn rejimi: xabar shuncha soniyada bir tahrirlanadi
WIDGET_EVERY = 2.0        # widget rejimi: CSS o'zi animatsiya qiladi, tez-tez yangilash kerak emas
SPEED_SMOOTH = 0.6        # tezlik o'rtachasi (sakramasligi uchun)
ETA_MAX = 3600            # bundan uzoq bo'lsa qolgan vaqt ko'rsatilmaydi
CHAT_DATA_MAX = 1024      # Telegram cheklovi (bayt)

# None — widget hali sinalmagan; False — Telegram rad etdi, boshqa urinmaymiz
_widgets_ok: bool | None = None


def widgets_state() -> bool | None:
    return _widgets_ok


def reset_widgets() -> None:
    """Testlar uchun: widget holatini tozalaydi."""
    global _widgets_ok
    _widgets_ok = None


def tg_len(text: str) -> int:
    """Telegram belgilarni UTF-16 birlikda hisoblaydi (emoji va ba'zi harflar — 2 birlik)."""
    return len(text.encode("utf-16-le")) // 2


def progress_parts(done: float) -> tuple[str, str]:
    """(to'lgan qism, bo'sh qism). Bo'sh qism spoiler ostiga olinadi."""
    n = max(0, min(BAR_LEN, round(done * BAR_LEN)))
    return BAR_FULL * n, BAR_EMPTY * (BAR_LEN - n)


def human_size(n: float) -> str:
    n = max(0, n)
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n:.0f} B"


def human_left(seconds: float) -> str:
    s = int(seconds)
    if s >= 60:
        return f"~{s // 60} daq {s % 60} s" if s % 60 else f"~{s // 60} daq"
    return f"~{max(1, s)} s"


# ---------- 🎨 HTML widget ----------
# Cheklovlar (core.telegram.org/bots/widgets): HTML ≤ 32 KB va zlib bilan ≤ 4 KB; tashqi fayl,
# tarmoq, iframe, video yo'q. Widget ichida ishlamaydigan CSS'dan ataylab voz kechilgan:
# vh, overflow:auto, position:sticky, 3D transform, clip-path, @font-face, :has(), inset.
WIDGET_HTML = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:13px -apple-system,Segoe UI,Roboto,sans-serif;color:#fff;padding:12px 14px;
background:linear-gradient(135deg,#17101f,#2a1030)}
.t{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600}
.d{width:8px;height:8px;border-radius:50%;background:#e1306c;animation:p 1.2s ease-in-out infinite}
@keyframes p{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.6)}}
.n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.w{margin:11px 0 0;height:10px;border-radius:5px;background:#ffffff1f;overflow:hidden}
.b{position:relative;height:100%;width:0;border-radius:5px;transition:width .45s ease;
background:linear-gradient(90deg,#833ab4,#c13584,#e1306c,#fd1d1d)}
.b:after{content:"";position:absolute;top:0;left:0;right:0;bottom:0;
background:linear-gradient(90deg,transparent,#ffffff73,transparent);animation:s 1.5s linear infinite}
@keyframes s{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.r{display:flex;justify-content:space-between;gap:10px;margin-top:7px;font-size:12px;opacity:.85}
</style>
<div class="t"><span class="d"></span><span class="n" id="k">Yuklanmoqda</span><span id="f"></span></div>
<div class="w"><div class="b" id="b"></div></div>
<div class="r"><span id="z"></span><span id="e"></span></div>
<script>
var W=window.TelegramWidget,G=function(i){return document.getElementById(i)};
function r(){var d={};try{d=JSON.parse(W.chatData)||{}}catch(x){}
G("k").textContent=d.t||"Yuklanmoqda";
G("b").style.width=(d.p==null?0:d.p)+"%";
G("f").textContent=d.p==null?"":d.p+"%";
G("z").textContent=d.z||"";G("e").textContent=d.e||"";}
W.onEvent("chatDataChanged",r);r();
</script>"""


def widget_data(text: str, fraction: float | None, size: str = "", extra: str = "") -> str:
    """Widget ichidagi JS o'qiydigan holat (JSON). Telegram cheklovi: 1024 bayt."""
    data: dict = {"t": text}
    if fraction is not None:
        data["p"] = max(0, min(100, round(fraction * 100)))
    if size:
        data["z"] = size
    if extra:
        data["e"] = extra
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(out.encode()) > CHAT_DATA_MAX:   # uzun matnni qisqartiramiz (amalda bo'lmaydi)
        data["t"] = text[:120]
        out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return out


async def _send_widget(client, chat, text: str):
    """Widget xabarini yuboradi va xabar obyektini qaytaradi. Xatoni yuqoriga uzatadi."""
    peer = await client.get_input_entity(chat)
    req = SendMediaRequest(
        peer=peer, message="",
        media=InputMediaWidget(width=0, height=0, html=WIDGET_HTML,     # 0 = avtomatik o'lcham
                               chat_data=widget_data(text, None)))
    result = await client(req)
    return client._get_response_message(req, result, peer)


async def open_progress(client, chat, event, text: str = "Yuklanmoqda...",
                        action: str = "document") -> "Progress":
    """Ko'rsatkichni ochadi: imkoni bo'lsa HTML widget, bo'lmasa oddiy matnli xabar.

    client/chat — bot va egasining chati; event — zaxira xabar yuborish uchun (event.respond).
    """
    global _widgets_ok
    if _widgets_ok is not False and client is not None and chat is not None:
        try:
            msg = await _send_widget(client, chat, text)
            if msg is not None:
                if _widgets_ok is None:
                    log.info("Yuklash ko'rsatkichi: HTML widget ishlayapti")
                _widgets_ok = True
                return Progress(msg, client, chat, action, text, widget=True)
        except Exception as e:
            _widgets_ok = False
            log.info("HTML widget qo'llanmadi (%s) — matnli ko'rsatkich ishlatiladi", type(e).__name__)
    msg = await event.respond(f"{SPIN[0]}  {text}")
    return Progress(msg, client, chat, action, text)


class Progress:
    """Yuklash ko'rsatkichi. `widget=True` bo'lsa holat Telegram widgetiga yuboriladi,
    aks holda xabar matni tahrirlanadi (spinner + spoiler bar)."""

    def __init__(self, msg, client=None, chat=None, action: str = "document",
                 text: str = "Yuklanmoqda...", widget: bool = False) -> None:
        self.msg, self.client, self.chat, self.action = msg, client, chat, action
        self.widget = widget
        self.text, self.fraction = text, None
        self.done = self.total = 0
        self.speed = 0.0
        self._tick, self._task, self._act = 0, None, None
        self._last: tuple[float, int] | None = None   # (vaqt, baytlar) — tezlik uchun
        self._plain = False                           # spoiler ishlamasa, oddiy matnga o'tamiz
        # Xabar allaqachon shu holat bilan yuborilgan — birinchi yangilanish behuda bo'lmasin
        self._shown = widget_data(text, None) if widget else f"{SPIN[0]}  {text}"

    # ---------- holat ----------
    def set(self, text: str | None = None, fraction: float | None = None,
            done: int = 0, total: int = 0) -> None:
        """Holatni o'zgartiradi (keyingi yangilanishda ko'rinadi).

        fraction — 0..1 yoki None (hajm noma'lum); done/total — baytlar (tezlik va hajm uchun).
        """
        if text is not None:
            self.text = text
        self.fraction = fraction
        self.total = total
        if done:
            self._measure(done)
        self.done = done

    def _measure(self, done: int) -> None:
        """Tezlikni oxirgi ikki o'lchov orasidagi farqdan hisoblaydi (eksponensial o'rtacha)."""
        now = time.monotonic()
        if self._last is not None:
            elapsed, grown = now - self._last[0], done - self._last[1]
            if elapsed >= 0.25 and grown > 0:
                fresh = grown / elapsed
                self.speed = fresh if not self.speed else (SPEED_SMOOTH * self.speed
                                                           + (1 - SPEED_SMOOTH) * fresh)
                self._last = (now, done)
        else:
            self._last = (now, done)

    def _size_text(self) -> str:
        if self.total:
            return f"{human_size(self.done)} / {human_size(self.total)}"
        return human_size(self.done) if self.done else ""

    def _rate_text(self) -> str:
        if not self.speed:
            return ""
        out = f"{human_size(self.speed)}/s"
        if self.total and self.done < self.total:
            left = (self.total - self.done) / self.speed
            if left <= ETA_MAX:
                out += f" · {human_left(left)}"
        return out

    def _status_line(self) -> str:
        """«4.2 / 12.0 MB · 2.4 MB/s · ~3 s» — faqat ma'lum bo'lgan qismlar."""
        return " · ".join(bit for bit in (self._size_text(), self._rate_text()) if bit)

    # ---------- ishga tushirish ----------
    async def __aenter__(self) -> "Progress":
        if self.client is not None and self.chat is not None:
            self._act = self.client.action(self.chat, self.action)
            try:
                await self._act.__aenter__()
            except Exception:
                self._act = None
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._act is not None:
            try:
                await self._act.__aexit__(*exc)
            except Exception:
                pass

    async def _loop(self) -> None:
        # Birinchi yangilanish DARHOL: aks holda tez tugagan ishda ko'rsatkich umuman ko'rinmay qolardi
        every = WIDGET_EVERY if self.widget else PROGRESS_EVERY
        while True:
            await self.render()
            await asyncio.sleep(every)

    # ---------- matn rejimi ----------
    def build(self) -> tuple[str, list]:
        """(matn, entity'lar). Bo'sh bar qismi spoiler bo'lib belgilanadi."""
        spin = SPIN[self._tick % len(SPIN)]
        head = f"{spin}  {self.text}"
        if self.fraction is None:
            return head, []
        full, empty = progress_parts(self.fraction)
        text = f"{head}\n\n{full}{empty}   {min(100, round(self.fraction * 100))}%"
        if status := self._status_line():
            text += f"\n{status}"
        if not empty or self._plain:
            return text, []
        # Telegram entity offsetlari UTF-16 birlikda hisoblanadi (emoji — 2 birlik)
        offset = tg_len(f"{head}\n\n{full}")
        return text, [MessageEntitySpoiler(offset=offset, length=tg_len(empty))]

    # ---------- yangilash ----------
    async def render(self) -> None:
        if self.widget:
            await self._render_widget()
            return
        text, entities = self.build()
        if text == self._shown:
            self._tick += 1       # matn o'zgarmasa ham keyingi kadrda spinner aylanadi
            return
        self._tick += 1
        try:
            await self.msg.edit(text, formatting_entities=entities or None)
            self._shown = text
        except Exception:
            if not entities:
                return  # matn o'zgarmagan yoki Telegram cheklovi — ko'rsatkich muhim emas
            self._plain = True    # spoiler qabul qilinmadi — bundan keyin oddiy matn
            try:
                await self.msg.edit(text)
                self._shown = text
            except Exception:
                pass

    async def _render_widget(self) -> None:
        """Faqat holatni yuboradi — HTML o'zgarmaydi, animatsiyani Telegram o'zi davom ettiradi."""
        data = widget_data(self.text, self.fraction, self._size_text(), self._rate_text())
        if data == self._shown:
            return
        try:
            await self.client(EditMessageWidgetDataRequest(
                peer=await self.client.get_input_entity(self.chat), id=self.msg.id, chat_data=data))
            self._shown = data
        except Exception:
            pass  # Telegram cheklovi yoki xabar o'chirilgan — ko'rsatkich muhim emas

    # ---------- yakun ----------
    async def finish(self, text: str, event=None, send_long=None) -> None:
        """Natijani ko'rsatadi. Widget rejimida widget xabari o'chirilib, natija matn bo'lib keladi
        (natija uzun bo'lishi mumkin — widget unga mos emas)."""
        if self.widget:
            try:
                await self.msg.delete()
            except Exception:
                log.info("Widget xabari o'chirilmadi — natija alohida xabar bo'lib keladi")
            if event is not None:
                await self._respond(event, text, send_long)
            return
        try:
            await self.msg.edit(text, parse_mode="html", link_preview=False)
        except Exception:
            if event is not None:
                await self._respond(event, text, send_long)

    @staticmethod
    async def _respond(event, text: str, send_long=None) -> None:
        if send_long is not None:
            await send_long(event.respond, text, parse_mode="html", link_preview=False)
        else:
            await event.respond(text, parse_mode="html", link_preview=False)
