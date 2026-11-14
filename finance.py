"""Shaxsiy buxgalter: egasi botga yozgan / aytgan kirim-chiqimlarni hisobga oladi.

"bugun nonga 25 ming, zapravkaga 30 ming ishlatdim" -> 2 ta chiqim
"Alidan 200 ming oldim"                            -> kirim (Ali)
"Validan 1 mln qarz oldim"                          -> qarz (kirim/chiqimga qo'shilmaydi, alohida yuritiladi)

AI (OpenAI) matnni tahlil qiladi; u ishlamasa — oddiy qoidalar bilan taxminiy tahlil.
Hisobot matnlari sof funksiyalar (test qilish oson).
"""
import asyncio
import calendar
import csv
import html
import io
import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
import storage

log = logging.getLogger("finance")

MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
          "sentabr", "oktabr", "noyabr", "dekabr"]
WEEKDAYS = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]

# Yozuv turlari. Kirim/chiqim hisobotga kiradi; qarzlar alohida (balansni buzmaydi)
TYPES = {  # turi: (belgi, nomi)
    "expense": ("🔻", "chiqim"),
    "income": ("🟢", "kirim"),
    "debt_taken": ("📥", "qarz oldim"),        # men qarzdorman
    "debt_given": ("📤", "qarz berdim"),       # menga qarzdor
    "repay_out": ("↪️", "qarzimni qaytardim"),
    "repay_in": ("↩️", "qarzimni qaytarishdi"),
}


def type_label(kind: str) -> str:
    icon, name = TYPES.get(kind, ("•", kind))
    return f"{icon} {name}"
EXPENSE_CATS = {
    "oziq-ovqat": "🍞", "transport": "⛽", "kafe": "🍽", "uy-kommunal": "🏠", "aloqa": "📱",
    "kiyim": "👕", "sog'liq": "💊", "ta'lim": "📚", "ko'ngilochar": "🎮", "sovg'a": "🎁",
    "oila": "👨‍👩‍👧", "texnika": "💻", "boshqa": "📦",
}
INCOME_CATS = {
    "oylik": "💼", "ish": "🛠", "sovg'a": "🎁", "sotuv": "🏷", "boshqa": "💰",
}

_TZ = ZoneInfo(config.TIMEZONE)


def now() -> datetime:
    return datetime.now(_TZ)


def day_key(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def cats_for(kind: str) -> dict:
    return INCOME_CATS if kind == "income" else EXPENSE_CATS


def cat_icon(kind: str, cat: str) -> str:
    return cats_for(kind).get(cat, "📦" if kind != "income" else "💰")


def fmt_sum(n: float, currency: str = "") -> str:
    """25000 -> "25 000 so'm" """
    cur = currency or config.FINANCE_CURRENCY
    s = f"{abs(int(round(n))):,}".replace(",", " ")
    return f"{'-' if n < 0 else ''}{s} {cur}".strip()


# ---------- tahlil ----------
# Moliyaga umuman o'xshamaydigan matn uchun AI chaqirilmaydi
_MONEY_HINT = re.compile(r"\d|\bming|mln|million|milliard|so['ʻ’`]?m\b|dollar|\$|\byuz\b|qarz", re.IGNORECASE)

PROMPT = """Sen shaxsiy buxgalter yordamchisisan. Foydalanuvchi o'zbek (yoki rus) tilida o'z pul
harakatlarini yozadi yoki aytadi (ovozli xabar matnga o'girilgan, xatolari bo'lishi mumkin).
Matndan barcha pul harakatlarini ajratib, FAQAT JSON qaytar:
{"finance": true|false, "items": [{"type": "...", "amount": 25000, "currency": "UZS",
  "category": "...", "note": "non", "person": null, "date_offset": 0}]}

Qoidalar:
- "finance": matn pul harakati haqida bo'lmasa (salom, savol, boshqa gap) — false va items bo'sh.
- amount — butun son, so'mda: "25 ming"=25000, "1.5 mln"/"bir yarim million"=1500000,
  "yigirma besh ming"=25000, "500"=500 (agar kontekstdan ming ekani aniq bo'lsa — 500000).
- currency: so'm bo'lsa "UZS", dollar bo'lsa "USD" (amount dollarda).
- type:
  * "expense" — sarfladim, ishlatdim, to'ladim, sotib oldim, "nonga 25 ming", "...ga ketdi".
  * "income" — "...dan oldim", "...dan pul oldim", "berishdi", "tushdi", "oylik oldim", "topdim",
    "ishlab topdim", "buyurtmadan keldi". "Alidan 200 ming oldim" — bu KIRIM (person="Ali").
  * "debt_taken" — faqat "qarz" so'zi bo'lsa: "Alidan 100 ming qarz oldim".
  * "debt_given" — "Aliga 100 ming qarz berdim".
  * "repay_out" — "Aliga qarzimni qaytardim".
  * "repay_in" — "Ali qarzini qaytardi".
- category: expense uchun bittasi: oziq-ovqat, transport (yoqilg'i, zapravka, taksi, metro), kafe,
  uy-kommunal (ijara, svet, gaz, suv), aloqa (telefon, internet), kiyim, sog'liq, ta'lim, ko'ngilochar,
  sovg'a, oila, texnika, boshqa. income uchun: oylik, ish (buyurtma, frilans), sovg'a, sotuv, boshqa.
  Qarz turlari uchun "boshqa".
- note — qisqa nom (1-3 so'z, o'zbekcha): "non", "zapravka", "Alidan".
- person — pul kimdan/kimga (ism), bo'lmasa null.
- date_offset: bugun=0, kecha=-1, o'tgan kuni=-2. Aytilmasa 0.
"""


def _norm_cat(kind: str, cat: str) -> str:
    cat = (cat or "").strip().lower().replace("ʻ", "'").replace("’", "'").replace("`", "'")
    cats = cats_for(kind)
    if cat in cats:
        return cat
    for c in cats:
        if cat and (c.startswith(cat) or cat.startswith(c.split("-")[0])):
            return c
    return "boshqa"


def clean_items(raw: list) -> list[dict]:
    """AI natijasini tekshiradi: noto'g'ri yozuvlar tashlab yuboriladi."""
    items = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("type", "")).strip()
        if kind not in TYPES:
            continue
        try:
            amount = float(it.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 < amount < 1e13:
            continue
        try:
            offset = int(it.get("date_offset") or 0)
        except (TypeError, ValueError):
            offset = 0
        currency = str(it.get("currency") or "UZS").upper()
        person = it.get("person")
        items.append({
            "type": kind,
            "amount": round(amount, 2) if currency != "UZS" else int(round(amount)),
            "currency": currency if currency in ("UZS", "USD", "EUR", "RUB") else "UZS",
            "category": _norm_cat(kind, str(it.get("category", ""))) if kind in ("expense", "income")
            else "boshqa",
            "note": str(it.get("note") or "").strip()[:60],
            "person": str(person).strip()[:40] if person else None,
            "date_offset": max(-60, min(0, offset)),
        })
    return items


def looks_financial(text: str) -> bool:
    return bool(_MONEY_HINT.search(text or ""))


async def parse(text: str) -> dict:
    """{"finance": bool, "items": [...], "tokens": int, "ai": bool}. AI xatosida istisno chiqaradi."""
    import ai  # openai faqat kerak bo'lganda

    today = now()
    resp = await ai._get_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": f"Bugun: {today:%Y-%m-%d}, {WEEKDAYS[today.weekday()]}.\n"
                                              f"Matn: {text[:2000]}"}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=600,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    items = clean_items(data.get("items"))
    return {"finance": bool(data.get("finance")) or bool(items),
            "items": items,
            "tokens": getattr(resp.usage, "total_tokens", 0) or 0,
            "ai": True}


# --- AI'siz zaxira tahlil (taxminiy) ---
_AMOUNT = re.compile(
    r"(\d+(?:[.,]\d+)?(?:[\s ]\d{3})*)\s*(mlrd|milliard|mln|million|ming|k\b|so['ʻ’`]?m|sum|\$|dollar)?",
    re.IGNORECASE)
_MULT = {"mlrd": 1e9, "milliard": 1e9, "mln": 1e6, "million": 1e6, "ming": 1e3, "k": 1e3}
_INCOME_WORDS = re.compile(r"\b\w+dan\b.*\boldim|berishdi|berdi\b|tushdi|topdim|oylik|keldi", re.IGNORECASE)
_KEYWORDS = {
    "oziq-ovqat": ("non", "go'sht", "sut", "bozor", "meva", "ovqat", "produkt", "supermarket", "makro", "korzinka"),
    "transport": ("zapravka", "zaprafka", "benzin", "metan", "propan", "taksi", "yandex", "metro", "avtobus"),
    "kafe": ("kafe", "restoran", "choyxona", "lavash", "osh", "kofe", "burger", "shashlik"),
    "uy-kommunal": ("svet", "gaz", "suv", "ijara", "kommunal", "kvartira"),
    "aloqa": ("telefon", "internet", "paynet", "balans", "uzmobile", "beeline", "ucell", "mobiuz"),
    "kiyim": ("kiyim", "kurtka", "oyoq kiyim", "krossovka", "shim", "ko'ylak"),
    "sog'liq": ("dori", "apteka", "shifokor", "doktor", "klinika"),
    "ta'lim": ("kurs", "kitob", "o'qish", "kontrakt"),
}


def _guess_cat(segment: str) -> str:
    low = segment.lower()
    for cat, words in _KEYWORDS.items():
        if any(w in low for w in words):
            return cat
    return "boshqa"


def local_parse(text: str) -> dict:
    """Qoidalar bilan taxminiy tahlil: har bir summa — alohida yozuv, turi umumiy fe'ldan."""
    if not looks_financial(text):
        return {"finance": False, "items": [], "tokens": 0, "ai": False}
    low = text.lower()
    debt = "qarz" in low
    kind = "income" if _INCOME_WORDS.search(low) and not re.search(r"ishlatdim|sarfladim|to'ladim", low) \
        else "expense"
    if debt:
        kind = "debt_given" if "berdim" in low else "debt_taken"
    offset = -1 if "kecha" in low else 0
    items, prev_end = [], 0
    for m in _AMOUNT.finditer(text):
        num = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            value = float(num)
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        value *= _MULT.get(unit, 1)
        if value < 100 and not unit:  # "2 ta non" kabi sonlar summa emas
            continue
        segment = text[prev_end:m.start()]
        prev_end = m.end()
        words = re.findall(r"[\w'ʻ’]+", segment)
        note = re.sub(r"(ga|ni|dan)$", "", words[-1]) if words else ""
        items.append({"type": kind, "amount": int(value), "currency": "USD" if unit in ("$", "dollar") else "UZS",
                      "category": _guess_cat(segment) if kind == "expense" else "boshqa",
                      "note": note[:40], "person": None, "date_offset": offset})
    return {"finance": bool(items), "items": items, "tokens": 0, "ai": False}


def make_docs(items: list[dict], source: str, raw: str, when: datetime | None = None) -> list[dict]:
    """Tahlil natijasidan MongoDB hujjatlari."""
    when = when or now()
    docs = []
    for it in items:
        d = (when + timedelta(days=it.get("date_offset", 0))).date()
        docs.append({
            "type": it["type"], "amount": it["amount"], "currency": it.get("currency", "UZS"),
            "category": it["category"], "note": it.get("note", ""), "person": it.get("person"),
            "day": day_key(d), "ts": when.timestamp(), "source": source, "raw": raw[:500],
        })
    return docs


# ---------- hisob-kitob ----------
def summarize(rows: list[dict]) -> dict:
    s = {"income": 0, "expense": 0, "exp_cat": defaultdict(float), "inc_cat": defaultdict(float),
         "inc_person": defaultdict(float), "biggest": None, "count": 0, "other": defaultdict(lambda: [0, 0]),
         "debt": defaultdict(float), "days": set()}
    for r in rows:
        amt, kind, cur = r.get("amount", 0), r.get("type"), r.get("currency", "UZS")
        s["count"] += 1
        s["days"].add(r.get("day"))
        if cur != "UZS":
            if kind == "income":
                s["other"][cur][0] += amt
            elif kind == "expense":
                s["other"][cur][1] += amt
            continue
        if kind == "expense":
            s["expense"] += amt
            s["exp_cat"][r.get("category", "boshqa")] += amt
            if s["biggest"] is None or amt > s["biggest"]["amount"]:
                s["biggest"] = r
        elif kind == "income":
            s["income"] += amt
            s["inc_cat"][r.get("category", "boshqa")] += amt
            if r.get("person"):
                s["inc_person"][r["person"]] += amt
        elif kind in TYPES:
            s["debt"][kind] += amt
    return s


def debts(rows: list[dict]) -> tuple[dict, dict]:
    """(men qarzdor: {kimga: summa}, menga qarzdor: {kim: summa}) — barcha vaqt bo'yicha."""
    i_owe, owed = defaultdict(float), defaultdict(float)
    for r in rows:
        if r.get("currency", "UZS") != "UZS":
            continue
        who = (r.get("person") or "nomaʼlum").strip().title()
        kind, amt = r.get("type"), r.get("amount", 0)
        if kind == "debt_taken":
            i_owe[who] += amt
        elif kind == "repay_out":
            i_owe[who] -= amt
        elif kind == "debt_given":
            owed[who] += amt
        elif kind == "repay_in":
            owed[who] -= amt
    return ({k: v for k, v in i_owe.items() if v > 0}, {k: v for k, v in owed.items() if v > 0})


def _bar(part: float, total: float, width: int = 10) -> str:
    n = round(width * part / total) if total else 0
    return "▇" * n + "▁" * (width - n)


def _pct_change(cur: float, prev: float) -> str:
    if not prev:
        return ""
    ch = (cur - prev) / prev * 100
    if abs(ch) < 1:
        return " (o'zgarishsiz)"
    return f" ({'↑' if ch > 0 else '↓'} {abs(ch):.0f}% oldingi davrga nisbatan)"


def fmt_date(d: date) -> str:
    return f"{d.day}-{MONTHS[d.month - 1]}"


def row_line(r: dict) -> str:
    kind = r.get("type")
    icon = cat_icon(kind, r.get("category", "boshqa")) if kind in ("expense", "income") else TYPES[kind][0]
    sign = "−" if kind in ("expense", "debt_given", "repay_out") else "+"
    note = html.escape(r.get("note") or r.get("category") or "")
    who = f" ({html.escape(r['person'])})" if r.get("person") and kind != "expense" else ""
    cur = "" if r.get("currency", "UZS") == "UZS" else r["currency"]
    return f"{icon} {note}{who} — {sign}{fmt_sum(r.get('amount', 0), cur)}"


def build_report(rows: list[dict], title: str, start: date, end: date, *,
                 prev_rows: list[dict] | None = None, budget: dict | None = None,
                 month_rows: list[dict] | None = None, detail: bool | None = None) -> str:
    """Davr hisoboti (HTML). detail=True bo'lsa har bir yozuv ro'yxati ham chiqadi."""
    days = (end - start).days + 1
    period = fmt_date(start) if days == 1 else f"{fmt_date(start)} — {fmt_date(end)}"
    lines = [f"📒 <b>{title}</b> — {period}", ""]
    if not rows:
        lines.append("Bu davrda hech narsa yozilmagan. ✍️ Yozish uchun: «nonga 25 ming ishlatdim»")
        return "\n".join(lines)

    s = summarize(rows)
    p = summarize(prev_rows) if prev_rows else None
    bal = s["income"] - s["expense"]
    lines.append(f"🟢 Kirim: <b>{fmt_sum(s['income'])}</b>"
                 + (_pct_change(s["income"], p["income"]) if p else ""))
    lines.append(f"🔻 Chiqim: <b>{fmt_sum(s['expense'])}</b>"
                 + (_pct_change(s["expense"], p["expense"]) if p else ""))
    lines.append(f"{'📈' if bal >= 0 else '📉'} Balans: <b>{'+' if bal > 0 else ''}{fmt_sum(bal)}</b>")
    for cur, (inc, exp) in s["other"].items():
        lines.append(f"💵 {cur}: kirim {fmt_sum(inc, cur)}, chiqim {fmt_sum(exp, cur)}")

    if s["exp_cat"]:
        lines += ["", "<b>Xarajatlar bo'yicha:</b>"]
        for cat, amt in sorted(s["exp_cat"].items(), key=lambda kv: -kv[1]):
            pct = amt / s["expense"] * 100 if s["expense"] else 0
            lines.append(f"{cat_icon('expense', cat)} {cat}: {fmt_sum(amt)} — {pct:.0f}%\n"
                         f"<code>{_bar(amt, s['expense'])}</code>")
    if s["inc_person"] or len(s["inc_cat"]) > 1:
        lines += ["", "<b>Kirim manbalari:</b>"]
        for who, amt in sorted(s["inc_person"].items(), key=lambda kv: -kv[1]):
            lines.append(f"👤 {html.escape(who)}: {fmt_sum(amt)}")
        if not s["inc_person"]:
            for cat, amt in sorted(s["inc_cat"].items(), key=lambda kv: -kv[1]):
                lines.append(f"{cat_icon('income', cat)} {cat}: {fmt_sum(amt)}")

    if s["debt"]:
        lines += ["", "<b>Qarz harakatlari:</b>"]
        for kind, amt in s["debt"].items():
            lines.append(f"{type_label(kind)}: {fmt_sum(amt)}")

    lines.append("")
    if s["biggest"]:
        b = s["biggest"]
        lines.append(f"💸 Eng katta xarajat: {html.escape(b.get('note') or b.get('category'))} — "
                     f"{fmt_sum(b['amount'])}")
    if days > 1 and s["expense"]:
        lines.append(f"📅 Kunlik o'rtacha chiqim: {fmt_sum(s['expense'] / days)}")
    lines.append(f"🧾 Yozuvlar: {s['count']} ta")

    if budget:
        warn = budget_lines(budget, month_rows if month_rows is not None else rows)
        if warn:
            lines += ["", "<b>Oylik byudjet:</b>", *warn]

    if detail is None:
        detail = days == 1
    if detail:
        lines += ["", "<b>Yozuvlar:</b>"]
        for r in rows[:40]:
            lines.append(row_line(r))
        if len(rows) > 40:
            lines.append(f"… va yana {len(rows) - 40} ta (/history)")
    return "\n".join(lines)


def budget_lines(budget: dict, month_rows: list[dict]) -> list[str]:
    s = summarize(month_rows)
    out = []
    for cat, limit in sorted(budget.items()):
        spent = s["expense"] if cat == "jami" else s["exp_cat"].get(cat, 0)
        pct = spent / limit * 100 if limit else 0
        mark = "🔴" if pct >= 100 else "🟡" if pct >= 80 else "🟢"
        left = limit - spent
        lines_left = f"qoldi {fmt_sum(left)}" if left >= 0 else f"oshib ketdi {fmt_sum(-left)}"
        out.append(f"{mark} {cat}: {fmt_sum(spent)} / {fmt_sum(limit)} ({pct:.0f}%) — {lines_left}")
    return out


def budget_alerts(budget: dict, before: list[dict], after: list[dict]) -> list[str]:
    """Yangi yozuvdan keyin 80% yoki 100% chegarasi kesib o'tilgan bo'lsa — ogohlantirishlar."""
    if not budget:
        return []
    b, a = summarize(before), summarize(after)
    out = []
    for cat, limit in budget.items():
        if not limit:
            continue
        x = b["expense"] if cat == "jami" else b["exp_cat"].get(cat, 0)
        y = a["expense"] if cat == "jami" else a["exp_cat"].get(cat, 0)
        name = "umumiy oylik" if cat == "jami" else f"«{cat}»"
        if x < limit <= y:
            out.append(f"🔴 {name} byudjet tugadi: {fmt_sum(y)} / {fmt_sum(limit)}")
        elif x < 0.8 * limit <= y:
            out.append(f"🟡 {name} byudjetning {y / limit * 100:.0f}% ishlatildi "
                       f"(qoldi {fmt_sum(limit - y)})")
    return out


# ---------- davrlar ----------
PERIODS = {"today": "Bugungi moliya", "3": "3 kunlik hisobot", "7": "Haftalik hisobot",
           "month": "Oylik hisobot", "30": "30 kunlik hisobot"}


def period_range(name: str, today: date) -> tuple[date, date, date, date]:
    """(boshlanish, tugash, oldingi davr boshlanishi, oldingi davr tugashi)"""
    if name == "month":
        start = today.replace(day=1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        return start, today, prev_start, prev_end
    n = 1 if name == "today" else int(name)
    start = today - timedelta(days=n - 1)
    return start, today, start - timedelta(days=n), start - timedelta(days=1)


async def make_report(name: str, today: date | None = None) -> str:
    today = today or now().date()
    start, end, ps, pe = period_range(name, today)
    rows = await storage.list_transactions(day_key(start), day_key(end))
    prev = await storage.list_transactions(day_key(ps), day_key(pe))
    budget = await storage.get_budget()
    month_rows = await storage.list_transactions(day_key(today.replace(day=1)), day_key(today)) \
        if budget else None
    return build_report(rows, PERIODS.get(name, "Hisobot"), start, end, prev_rows=prev, budget=budget,
                        month_rows=month_rows)


async def make_balance() -> str:
    rows = await storage.list_transactions()
    s = summarize(rows)
    i_owe, owed = debts(rows)
    today = now().date()
    month = summarize([r for r in rows if r.get("day", "") >= day_key(today.replace(day=1))])
    bal = s["income"] - s["expense"]
    lines = ["💼 <b>Umumiy balans</b> (barcha yozuvlar)", "",
             f"🟢 Jami kirim: {fmt_sum(s['income'])}",
             f"🔻 Jami chiqim: {fmt_sum(s['expense'])}",
             f"{'📈' if bal >= 0 else '📉'} Farq: <b>{'+' if bal > 0 else ''}{fmt_sum(bal)}</b>", "",
             f"🗓 {MONTHS[today.month - 1].capitalize()}: kirim {fmt_sum(month['income'])}, "
             f"chiqim {fmt_sum(month['expense'])}"]
    if i_owe:
        lines += ["", f"📥 <b>Men qarzdorman: {fmt_sum(sum(i_owe.values()))}</b>"]
        lines += [f"• {html.escape(k)}: {fmt_sum(v)}" for k, v in sorted(i_owe.items(), key=lambda kv: -kv[1])]
    if owed:
        lines += ["", f"📤 <b>Menga qarzdor: {fmt_sum(sum(owed.values()))}</b>"]
        lines += [f"• {html.escape(k)}: {fmt_sum(v)}" for k, v in sorted(owed.items(), key=lambda kv: -kv[1])]
    if not i_owe and not owed:
        lines += ["", "🤝 Ochiq qarzlar yo'q."]
    return "\n".join(lines)


def to_csv(rows: list[dict]) -> bytes:
    """Excel'da to'g'ri ochiladigan CSV (UTF-8 BOM)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Sana", "Turi", "Kategoriya", "Izoh", "Kim", "Summa", "Valyuta", "Manba", "Asl matn"])
    for r in rows:
        kind = r.get("type")
        sign = -1 if kind in ("expense", "debt_given", "repay_out") else 1
        w.writerow([r.get("day"), TYPES.get(kind, ("", kind))[1], r.get("category"), r.get("note"),
                    r.get("person") or "", sign * r.get("amount", 0), r.get("currency", "UZS"),
                    r.get("source"), r.get("raw", "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ---------- avtomatik hisobotlar ----------
def due_reports(today: date) -> list[str]:
    """Bugun kechqurun qaysi hisobotlar yuboriladi."""
    names = ["today"]
    if today.toordinal() % 3 == 0:
        names.append("3")
    if today.isoweekday() == config.FINANCE_WEEK_DAY:
        names.append("7")
    if today.day == calendar.monthrange(today.year, today.month)[1]:
        names.append("month")
    return names


async def run_reports(send) -> None:
    """Har kuni FINANCE_REPORT_TIME da kunlik, har 3 kunda 3 kunlik, haftada bir haftalik,
    oy oxirida oylik moliya hisoboti. Hech qachon istisno chiqarmaydi."""
    import report

    hh, mm = report._parse_time(config.FINANCE_REPORT_TIME)
    log.info("Moliya hisoboti har kuni %02d:%02d da yuboriladi", hh, mm)
    while True:
        await asyncio.sleep(report.seconds_until(now(), hh, mm))
        today = now().date()
        key = day_key(today)
        try:
            st = await storage.get_runtime()
            if st.get("last_fin_report") == key:
                continue
            for name in due_reports(today):
                text = await make_report(name, today)
                if name == "today" and "hech narsa yozilmagan" in text:
                    text = ("📒 <b>Bugungi moliya</b>\n\nBugun hech qanday kirim-chiqim yozilmadi. "
                            "Xarajat bo'lgan bo'lsa, hozir yozib yoki aytib yuboring 🎤")
                await send(text)
            await storage.set_runtime(last_fin_report=key)
            log.info("Moliya hisoboti yuborildi")
        except Exception as e:
            log.warning("Moliya hisoboti yuborilmadi (%s)", type(e).__name__)
        await asyncio.sleep(60)
