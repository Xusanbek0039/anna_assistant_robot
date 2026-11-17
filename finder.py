"""Chatlardan qidirish: egasi botga «Ali menga tashlagan usernameni top» deb yozadi — akkauntdagi o'sha
odam bilan bo'lgan chat xabarlari ko'rib chiqiladi va ulardan username, telefon raqam, havola, email yoki
karta raqamlari ajratib beriladi.

Hammasi egasining o'z akkaunti (userbot) orqali, faqat o'qish rejimida ishlaydi; hech narsa yuborilmaydi.
"""
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("finder")

SCAN_LIMIT = 3000      # bitta chatda ko'rib chiqiladigan oxirgi xabarlar soni
DIALOG_LIMIT = 500     # ism bo'yicha qidiriladigan chatlar soni
MAX_RESULTS = 20       # javobda ko'rsatiladigan topilmalar

KINDS = {
    "username": "👤 Username",
    "phone": "📞 Telefon raqam",
    "link": "🔗 Havola",
    "email": "📧 Email",
    "card": "💳 Karta raqami",
}
KIND_ALIASES = {
    "username": "username", "user": "username", "nik": "username", "nick": "username", "niki": "username",
    "юзернейм": "username", "ник": "username",
    "phone": "phone", "raqam": "phone", "nomer": "phone", "nomeri": "phone", "telefon": "phone",
    "tel": "phone", "номер": "phone", "телефон": "phone",
    "link": "link", "havola": "link", "ssilka": "link", "silka": "link", "url": "link", "sayt": "link",
    "ссылка": "link", "ссылку": "link",
    "email": "email", "mail": "email", "pochta": "email", "gmail": "email", "почта": "email",
    "karta": "card", "kartа": "card", "card": "card", "plastik": "card", "карта": "card", "карту": "card",
    "all": "all", "hammasi": "all", "hamma": "all", "barchasi": "all",
}

# «top», «topib ber», «qidir», «izla», «find», «найди» ...
_SEARCH_VERB = re.compile(
    r"\b(top|topib|topgin|topchi|topsang|topa\w*|qidir\w*|izla\w*|find|search|na[iy]di|найди\w*|поищи|ищи)\b",
    re.IGNORECASE)
_KIND_WORD = re.compile(r"[\w'ʻ’@]+", re.UNICODE)
# Chat nomini ajratishda e'tiborga olinmaydigan so'zlar
_STOP = {
    "menga", "men", "mening", "meni", "bilan", "chat", "chatdan", "chatidan", "chatda", "dan", "ni", "ga",
    "tashlagan", "tashlagandi", "tashlab", "kelgan", "kelgandi", "jo'natgandi", "yuborgan", "yuborgandi", "yozgan", "yozgandi", "bergan",
    "bergandi", "jo'natgan", "jonatgan", "otgan", "bitta", "bir", "osha", "o'sha", "oshani", "o'shani",
    "shu", "shuni", "u", "uni", "edi", "ekan", "qachonlardir", "oldin", "avval", "iltimos", "pls",
    "top", "topib", "ber", "topibber", "qidir", "qidirib", "izla", "izlab", "find", "search", "kerak",
    "akkauntimdan", "akkauntdan", "akkaunt", "chatimdan", "xabar", "xabardan", "xabarlardan", "habar",
    "habardan", "habarlardan", "anna", "kim", "qaysi", "nima", "va", "yoki", "hakozo", "hokazo", "boshqa",
    "the", "from", "in", "me", "sent", "my", "chatni", "chatini",
}

USERNAME_RE = re.compile(r"(?<![\w@.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
TME_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?!\+|joinchat/|c/|s/|addlist/|addstickers/)"
                    r"([A-Za-z][A-Za-z0-9_]{3,31})\b", re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'«»]+|(?:t|telegram)\.me/[^\s<>\"'«»]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
CARD_RE = re.compile(r"(?<![\d+])(?:\d{4}[ \-]?){3}\d{4}(?!\d)")
PHONE_RE = re.compile(r"(?<![\w+])(\+?\d[\d \-()]{6,18}\d)(?!\w)")


# Telegram'ning o'z qidiruv filtrlari (butun tarix bo'yicha, server tomonida)
MEDIA_FILTERS = {
    "photo": "InputMessagesFilterPhotos", "video": "InputMessagesFilterVideo",
    "document": "InputMessagesFilterDocument", "voice": "InputMessagesFilterVoice",
    "round": "InputMessagesFilterRoundVideo", "location": "InputMessagesFilterGeo",
    "contact": "InputMessagesFilterContacts", "music": "InputMessagesFilterMusic",
    "gif": "InputMessagesFilterGif", "link": "InputMessagesFilterUrl",
}
MEDIA_NAMES = {
    "photo": "rasm", "video": "video", "document": "fayl", "voice": "ovozli xabar", "round": "video-xabar",
    "location": "lokatsiya", "contact": "kontakt", "music": "musiqa", "gif": "GIF", "link": "havola",
}
MEDIA_WORDS = {
    "photo": ("rasm", "foto", "surat", "skrin", "screenshot", "фото", "картин", "скрин", "photo"),
    "video": ("video", "видео"),
    "document": ("fayl", "hujjat", "dokument", "pdf", "файл", "документ", "document"),
    "voice": ("ovozli", "golos", "голосов", "voice"),
    "round": ("krujok", "кружок"),
    "location": ("lokatsiya", "lokatsya", "geolokatsiya", "geo", "joylashuv", "локац", "геолок", "location"),
    "contact": ("kontakt", "контакт"),
    "music": ("qo'shiq", "qoshiq", "musiqa", "muzika", "песн", "трек", "музык"),
    "gif": ("gif", "гиф"),
}
# Pul haqidagi gap (buxgalterga tegishli) — qidiruv deb olinmaydi
_FIN_HINT = re.compile(r"\bming\b|\bmln\b|million|so['ʻ’`]?m\b|dollar|\$|qarz|\boldim\b|\bberdim\b|ishlatdim",
                       re.IGNORECASE)
_UZ_SUFFIXES = ("larini", "larni", "lardan", "larga", "laridan", "ning", "dan", "ini", "sini", "ni", "ga",
                "da", "lar", "imni", "ingni", "im", "ing")


@dataclass
class Query:
    name: str              # chat/kontakt nomi (bo'sh bo'lishi mumkin — unda barcha chatlardan)
    kind: str = "all"      # username | phone | link | email | card | all (all = istalgan narsa)
    only_them: bool = False  # «menga tashlagan» — faqat suhbatdosh yuborganlari
    media: str | None = None  # photo | video | document | voice | location | ...
    keywords: list[str] = field(default_factory=list)  # xabarlarda uchrashi mumkin bo'lgan so'zlar
    days: int | None = None   # faqat oxirgi N kun ichida
    what: str = ""            # nima qidirilayotgani (odam tilida)
    words: list[str] = field(default_factory=list)  # AI'siz tahlilda ism/so'z aralash qolgan so'zlar
    ai: bool = False          # so'rov AI bilan tahlil qilinganmi
    text: str = ""            # asl so'rov


@dataclass
class Found:
    chat_id: int
    id: int
    date: datetime | None
    mine: bool
    text: str
    media: str = ""
    chat_title: str = ""


@dataclass
class Hit:
    kind: str
    value: str
    date: datetime | None
    mine: bool
    snippet: str = ""


@dataclass
class Candidate:
    id: int
    title: str
    username: str | None = None
    score: float = 0
    aliases: list[str] = field(default_factory=list)


# ---------- so'rovni tushunish ----------
def _norm(s: str) -> str:
    return re.sub(r"[ʻ’`‘]", "'", (s or "").lower()).strip()


def detect_kind(text: str) -> str:
    for w in _KIND_WORD.findall(_norm(text)):
        w = w.strip("'@")
        for alias, kind in KIND_ALIASES.items():
            # «usernameni», «raqamini», «havolani», «nomerini» — qo'shimchalar bilan ham
            if kind != "all" and len(alias) >= 4 and w.startswith(alias):
                return kind
            if w == alias:
                return kind
    return "all"


def _media_word(word: str) -> str | None:
    for media, stems in MEDIA_WORDS.items():
        if any(word.startswith(s) for s in stems):
            return media
    return None


def detect_media(text: str) -> str | None:
    for w in _KIND_WORD.findall(_norm(text)):
        m = _media_word(w.strip("'@"))
        if m:
            return m
    return None


def looks_like_search(text: str) -> bool:
    """«... top», «... qidirib ber», «... izla». Pul haqidagi gap («200 ming topib berdim») — buxgalterga."""
    t = text or ""
    if not _SEARCH_VERB.search(t):
        return False
    if detect_kind(t) != "all" or detect_media(t):
        return True
    return not _FIN_HINT.search(t)


def stem(word: str) -> str:
    """«manzilni» -> «manzil», «kitoblarni» -> «kitob» (Telegram qidiruvi o'zak bilan yaxshiroq topadi)."""
    w = _norm(word).strip("'.,!?")
    for suf in _UZ_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def parse_query(text: str) -> Query:
    """AI'siz tahlil. Ism va qidirilayotgan so'zlarni ajratib bo'lmaydi — ikkalasi `words` da qoladi,
    chat tanlangach chat nomiga mos kelmaganlari kalit so'z bo'ladi (`keywords_for`)."""
    low = _norm(text)
    only_them = bool(re.search(r"\bmenga\b|\bменя\b|\bмне\b", low))
    kind = detect_kind(low)
    media = detect_media(low)
    words = []
    for w in re.findall(r"[\w'@.]+", text or "", re.UNICODE):
        n = _norm(w).strip("'.")
        if not n or n in _STOP or n in KIND_ALIASES or _SEARCH_VERB.fullmatch(n) or _media_word(n):
            continue
        if any(n.startswith(a) for a in KIND_ALIASES if len(a) >= 4):
            continue  # «usernameni», «raqamini»
        words.append(w.strip(".,!?"))
    return Query(name=" ".join(words).strip(), kind=kind, only_them=only_them, media=media,
                 words=words, text=text or "")


def keywords_for(q: Query, title: str = "", username: str | None = None) -> list[str]:
    """Qidiruv so'zlari: AI bergani yoki (AI'siz) chat nomiga tegishli bo'lmagan so'zlar."""
    if q.ai:
        return q.keywords
    out = []
    for w in q.words:
        if title and score_name(w, title, username) > 0:
            continue  # bu — chat egasining ismi
        s = stem(w)
        if len(s) >= 3 and s not in out:
            out.append(s)
    return out


PARSE_PROMPT = """Sen Telegram chatlaridan qidiruv so'rovini tahlil qilasan. Akkaunt egasi o'z chatlaridan
nimanidir topmoqchi (o'zbek, rus yoki ingliz tilida, ovozdan o'girilgan bo'lsa xatolari bo'lishi mumkin).
Faqat JSON qaytar:
{"contact": "", "kind": "other", "media": "none", "keywords": [], "only_them": false, "days": null, "what": ""}
- contact: kimning chatidan qidirish kerak — kontakt nomi yoki @username, qo'shimchalarsiz
  ("Alidan" -> "Ali", "Sardor akaning" -> "Sardor aka", "ishxona guruhidan" -> "ishxona"). Aytilmagan bo'lsa "".
- kind: aynan username | phone | link | email | card qidirilsa — shu; boshqa HAMMA narsa uchun "other".
- media: photo | video | document | voice | round | location | contact | music | gif | link | none.
  "manzil/lokatsiya tashlagandi" -> location (manzil matn bo'lishi ham mumkin — keywords ga ham qo'sh).
- keywords: xabar MATNIDA uchrashi mumkin bo'lgan 0-8 ta qisqa so'z: o'zak shaklida (qo'shimchasiz),
  sinonimlari, lotin/kirill va rus/ingliz variantlari bilan. Kontakt ismini, "top/yubordi/tashladi" kabi
  so'zlarni QO'SHMA. Masalan "uchrashuv joyi" -> ["uchrash", "joy", "manzil", "adres", "адрес", "встреч"];
  "wifi paroli" -> ["wifi", "parol", "пароль", "password"]. Faqat media qidirilsa [] bo'lishi mumkin.
- only_them: "menga yubordi/tashladi/aytdi" -> true; "men yuborgandim" yoki noaniq -> false.
- days: vaqt aytilgan bo'lsa necha kun ichida (bugun -> 1, kecha -> 2, shu hafta -> 7, o'tgan hafta -> 14,
  o'tgan oy -> 62), aks holda null.
- what: nima qidirilayotgani, qisqa o'zbekcha (masalan "uchrashuv manzili", "wifi paroli")."""

PICK_PROMPT = """Sen akkaunt egasiga uning Telegram chatlaridagi xabarlardan kerakli narsani topib berasan.
Har bir xabar: [id] sana, kim (chat): matn. Faqat JSON qaytar: {"answer": "", "ids": []}
- answer: savolga qisqa va aniq javob (o'zbekcha, 1-3 gap). Topilgan qiymatni AYNAN keltir (username, raqam,
  manzil, nom, parol, summa, sana, havola...). Rasm/fayl/lokatsiya bo'lsa, qaysi xabar ekanini ayt.
  Javob topilmasa "".
- ids: javobga tegishli eng mos 1-5 ta xabar id si, eng mosi birinchi. Hech biri mos bo'lmasa [].
Xabarlarda yo'q narsani o'ylab topma."""


async def ai_parse(text: str) -> tuple[Query, int]:
    """So'rovni AI bilan tahlil qiladi. (Query, tokenlar). AI xatosida istisno chiqaradi."""
    import json

    import ai
    import config

    resp = await ai._get_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": PARSE_PROMPT}, {"role": "user", "content": text[:1000]}],
        response_format={"type": "json_object"}, temperature=0, max_tokens=300,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    local = parse_query(text)
    kind = data.get("kind") if data.get("kind") in KINDS else "all"
    media = data.get("media") if data.get("media") in MEDIA_FILTERS else None
    kws = []
    for k in data.get("keywords") or []:
        k = str(k).strip()
        if 2 <= len(k) <= 40 and k.lower() not in (x.lower() for x in kws):
            kws.append(k)
    days = data.get("days")
    days = int(days) if isinstance(days, (int, float)) and 0 < days <= 3650 else None
    q = Query(name=str(data.get("contact") or "").strip(), kind=kind, only_them=bool(data.get("only_them")), media=media or local.media,
              keywords=kws[:8], days=days, what=str(data.get("what") or "")[:100], ai=True, text=text)
    return q, getattr(resp.usage, "total_tokens", 0) or 0


# ---------- chatni topish ----------
_SUFFIXES = ("ning", "dan", "ga", "ni", "da", "lar", "ka", "qa", "niki", "dagi", "bilan")


def _token_matches(word: str, name_part: str) -> bool:
    """«Alidan», «Alining», «Aliga» — «Ali» ga mos keladi."""
    if not name_part or len(name_part) < 2:
        return False
    if word == name_part:
        return True
    if word.startswith(name_part):
        rest = word[len(name_part):].lstrip("'")
        return rest in _SUFFIXES or rest.startswith(("ning", "dan", "ga", "ni", "da"))
    return False


def score_name(query: str, title: str, username: str | None = None) -> float:
    """So'rovdagi so'zlar chat nomiga qanchalik mos. 0 — umuman mos emas."""
    q_words = [w.lstrip("@") for w in re.findall(r"[\w'@]+", _norm(query), re.UNICODE)]
    if not q_words:
        return 0
    parts = [p for p in re.findall(r"[\w']+", _norm(title), re.UNICODE) if len(p) >= 2]
    uname = _norm(username or "")
    score = 0.0
    for w in q_words:
        if uname and w == uname:
            score += 10
        elif any(w == p for p in parts):
            score += 3
        elif any(_token_matches(w, p) for p in parts):
            score += 2
        elif len(w) >= 4 and any(p.startswith(w) for p in parts):
            score += 1
    if score and _norm(query) == _norm(title):
        score += 5
    return score


async def find_chats(client, name: str, limit: int = DIALOG_LIMIT) -> list[Candidate]:
    """Akkauntdagi chatlardan nomi so'rovga mos keladiganlari (eng mosi birinchi)."""
    out: list[Candidate] = []
    async for d in client.iter_dialogs(limit=limit):
        ent = d.entity
        username = getattr(ent, "username", None)
        title = d.name or ""
        s = score_name(name, title, username)
        if s > 0:
            if d.is_user:
                s += 0.5  # shaxsiy chatlar ustun
            out.append(Candidate(id=d.id, title=title, username=username, score=s))
    out.sort(key=lambda c: -c.score)
    return out


# ---------- xabarlardan ajratish ----------
def _clean_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if not 9 <= len(digits) <= 15:
        return None
    if re.fullmatch(r"(19|20)\d{2}[-.]?\d{2}[-.]?\d{2}", raw.strip()):
        return None  # sana
    if len(digits) == 9:
        return f"+998 {digits[:2]} {digits[2:5]} {digits[5:7]} {digits[7:]}"
    if len(digits) == 12 and digits.startswith("998"):
        return f"+998 {digits[3:5]} {digits[5:8]} {digits[8:10]} {digits[10:]}"
    return ("+" if raw.strip().startswith("+") or len(digits) > 10 else "") + digits


def extract(text: str, entities=None, media=None) -> list[tuple[str, str]]:
    """Matn (va Telegram entity/media) dan (tur, qiymat) juftliklari."""
    from telethon.tl import types

    text = text or ""
    found: list[tuple[str, str]] = []
    cards = {re.sub(r"\D", "", m) for m in CARD_RE.findall(text)}
    for c in cards:
        found.append(("card", " ".join(c[i:i + 4] for i in range(0, 16, 4))))
    emails = EMAIL_RE.findall(text)
    found += [("email", e) for e in emails]
    no_mail = EMAIL_RE.sub(" ", text)
    found += [("username", "@" + u) for u in USERNAME_RE.findall(no_mail)]
    found += [("username", "@" + u) for u in TME_RE.findall(text)]
    found += [("link", u.rstrip(".,;:!?)»")) for u in URL_RE.findall(text)]
    no_urls = URL_RE.sub(" ", no_mail)
    for raw in PHONE_RE.findall(no_urls):
        if re.sub(r"\D", "", raw) in cards:
            continue
        p = _clean_phone(raw)
        if p:
            found.append(("phone", p))
    for e in entities or []:
        if isinstance(e, types.MessageEntityTextUrl):
            found.append(("link", e.url))
            m = TME_RE.fullmatch(e.url.split("?")[0].rstrip("/"))
            if m:
                found.append(("username", "@" + m.group(1)))
        elif isinstance(e, types.MessageEntityMentionName):
            found.append(("username", f"id:{e.user_id} «{text[e.offset:e.offset + e.length]}»"))
        elif isinstance(e, types.MessageEntityPhone):
            p = _clean_phone(text[e.offset:e.offset + e.length])
            if p:
                found.append(("phone", p))
    if isinstance(media, types.MessageMediaContact):
        name = " ".join(x for x in (media.first_name, media.last_name) if x)
        p = _clean_phone(media.phone_number or "")
        if p:
            found.append(("phone", f"{p} ({name})" if name else p))
        if media.user_id:
            found.append(("username", f"id:{media.user_id}" + (f" «{name}»" if name else "")))
    # takrorlarni olib tashlash (tartib saqlanadi)
    seen, out = set(), []
    for k, v in found:
        key = (k, v.lower())
        if key not in seen:
            seen.add(key)
            out.append((k, v))
    return out


async def search_chat(client, chat_id: int, kind: str = "all", only_them: bool = False,
                      limit: int = SCAN_LIMIT) -> tuple[list[Hit], int]:
    """Chatdagi oxirgi `limit` ta xabardan topilmalar (yangisi birinchi) va ko'rilgan xabarlar soni."""
    hits: list[Hit] = []
    seen: set[tuple[str, str]] = set()
    scanned = 0
    async for m in client.iter_messages(chat_id, limit=limit):
        scanned += 1
        if only_them and m.out:
            continue
        text = m.message or ""
        if not text and not m.media:
            continue
        for k, v in extract(text, m.entities, m.media):
            if kind != "all" and k != kind:
                continue
            key = (k, v.lower())
            if key in seen:
                continue
            seen.add(key)
            hits.append(Hit(kind=k, value=v, date=m.date, mine=bool(m.out), snippet=text[:120]))
    return hits, scanned


# ---------- javob matni ----------
def format_hits(title: str, hits: list[Hit], kind: str, scanned: int, only_them: bool) -> str:
    what = KINDS.get(kind, "🔎 Ma'lumot").split(" ", 1)[-1].lower() if kind != "all" else "ma'lumot"
    head = f"🔎 <b>{html.escape(title)}</b> chatidan"
    if not hits:
        return (f"{head} {what} topilmadi.\n"
                f"<i>Oxirgi {scanned} ta xabar ko'rib chiqildi"
                f"{', faqat u yuborganlari' if only_them else ''}.</i>")
    lines = [f"{head} topildi ({len(hits)} ta):", ""]
    for h in hits[:MAX_RESULTS]:
        icon = KINDS.get(h.kind, "•").split(" ", 1)[0]
        when = h.date.strftime("%d.%m.%Y") if h.date else ""
        who = "siz" if h.mine else "u"
        lines.append(f"{icon} <code>{html.escape(h.value)}</code>  <i>({when}, {who} yuborgan)</i>")
    if len(hits) > MAX_RESULTS:
        lines.append(f"\n…va yana {len(hits) - MAX_RESULTS} ta. Aniqroq qidiring: "
                     f"<code>/find ism username</code>")
    lines.append(f"\n<i>Oxirgi {scanned} ta xabar ko'rib chiqildi.</i>")
    return "\n".join(lines)
