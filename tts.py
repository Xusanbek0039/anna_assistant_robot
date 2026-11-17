"""Ovozli javob: matn -> Telegram ovozli xabari (OGG/Opus).

Faqat VoiceLab (voicelab.uz) o'zbek ayol ovozi "Madina — Honey" ishlatiladi (VOICELAB_API_KEY kerak).
Boshqa zaxira ovoz yo'q: VoiceLab ishlamasa — istisno ko'tariladi, suhbatdoshga matn yuboriladi
va egasiga bot orqali muammo haqida xabar boradi (userbot._send_voice).

Ovozga berishdan oldin matn "o'qishga" tayyorlanadi: raqamlar so'z bilan (25 000 -> yigirma besh ming),
oʻ/gʻ to'g'ri belgilar bilan, emoji va havolalar olib tashlanadi, kirillcha o'zbek matni lotinga o'giriladi.
Matn tili (uz/ru/en) VoiceLab'ga uzatiladi.
"""
import io
import logging
import re

import config

log = logging.getLogger("tts")

MAX_CHARS = 1500

# ---------- raqamlar -> o'zbekcha so'zlar ----------
_ONES = ["nol", "bir", "ikki", "uch", "toʻrt", "besh", "olti", "yetti", "sakkiz", "toʻqqiz"]
_TENS = ["", "oʻn", "yigirma", "oʻttiz", "qirq", "ellik", "oltmish", "yetmish", "sakson", "toʻqson"]
_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr",
           "noyabr", "dekabr"]
_SCALES = [(10 ** 12, "trillion"), (10 ** 9, "milliard"), (10 ** 6, "million"), (1000, "ming")]


def _below_thousand(n: int) -> str:
    parts = []
    h, rest = divmod(n, 100)
    if h:
        parts.append("yuz" if h == 1 else f"{_ONES[h]} yuz")
    t, o = divmod(rest, 10)
    if t:
        parts.append(_TENS[t])
    if o:
        parts.append(_ONES[o])
    return " ".join(parts)


def number_words(n: int) -> str:
    """1250000 -> "bir million ikki yuz ellik ming". 1000 -> "ming"."""
    if n == 0:
        return "nol"
    if n < 0:
        return "minus " + number_words(-n)
    parts = []
    for value, name in _SCALES:
        q, n = divmod(n, value)
        if q:
            parts.append(name if (q == 1 and value == 1000) else f"{number_words(q)} {name}")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def ordinal_words(n: int) -> str:
    """14 -> "oʻn toʻrtinchi", 2 -> "ikkinchi"."""
    w = number_words(n)
    return w + ("nchi" if w[-1] in "aeiou" else "inchi")


def _decimal_words(whole: str, frac: str) -> str:
    frac = frac.rstrip("0") or "0"
    if frac == "5" and len(frac) == 1:
        return f"{number_words(int(whole))} yarim" if whole != "0" else "yarim"
    base = {1: "oʻndan", 2: "yuzdan", 3: "mingdan"}.get(len(frac))
    if not base:
        return f"{number_words(int(whole))} butun " + " ".join(_ONES[int(c)] for c in frac)
    return f"{number_words(int(whole))} butun {base} {number_words(int(frac))}"


# ---------- matnni o'qishga tayyorlash ----------
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
_URL = re.compile(r"https?://\S+|www\.\S+|t\.me/\S+")
_APOS = "'`’‘ʼʻ´"
_OG = re.compile(rf"([oOgG])[{_APOS}]")
# tutuq belgisi (ma'no -> maʼno); oʻ/gʻ dagi ʻ ga tegilmaydi
_TUTUQ = re.compile(r"(?<=[A-Za-z])['`’‘ʼ´](?=[A-Za-z])")
_GROUPED = re.compile(r"(?<![\d.,])\d{1,3}(?:[  ]\d{3})+(?![\d])")
_ABBR = [
    (re.compile(r"\bmlrd\b\.?", re.I), "milliard"), (re.compile(r"\bmln\b\.?", re.I), "million"),
    (re.compile(r"\bh\.k\.", re.I), "hokazo"), (re.compile(r"\bva\s+b\.", re.I), "va boshqalar"),
    (re.compile(r"\bt\.y\.", re.I), "tugʻilgan yili"), (re.compile(r"\bso['’ʻ]?m\b", re.I), "soʻm"),
    (re.compile(r"\$\s*(\d[\d.,]*)"), r"\1 dollar"), (re.compile(r"(\d)\s*\$"), r"\1 dollar"),
    (re.compile(r"(\d)\s*%"), r"\1 foiz"), (re.compile(r"№\s*"), "raqam "),
]

_CYR_UZ = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "ʼ", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "ў": "oʻ", "қ": "q", "ғ": "gʻ", "ҳ": "h",
}
_UZ_CYR_ONLY = set("ўқғҳЎҚҒҲ")


def _translit_uz(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _CYR_UZ:
            lat = _CYR_UZ[low]
            out.append(lat.capitalize() if ch != low and lat else lat)
        else:
            out.append(ch)
    return "".join(out)


_EN_WORDS = re.compile(r"\b(the|is|are|you|your|and|what|please|hello|thanks|will|can|i'm|it's|this|that)\b",
                       re.I)


def detect_lang(text: str) -> str:
    """uz | ru | en (oddiy belgilar hisobiga ko'ra)."""
    cyr = len(re.findall(r"[А-Яа-яЁёЎўҚқҒғҲҳ]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    if cyr > lat:
        return "uz" if any(c in _UZ_CYR_ONLY for c in text) else "ru"
    words = max(1, len(re.findall(r"[A-Za-z']+", text)))
    if len(_EN_WORDS.findall(text)) / words > 0.15:
        return "en"
    return "uz"


def _numbers_to_words(text: str) -> str:
    text = _GROUPED.sub(lambda m: re.sub(r"[  ]", "", m.group()), text)  # "25 000" -> "25000"
    # vaqt: 15:00 -> "oʻn besh", 15:30 -> "oʻn besh oʻttiz"
    text = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
                  lambda m: number_words(int(m.group(1))) + ("" if m.group(2) == "00"
                                                            else " " + number_words(int(m.group(2)))), text)
    # tartib son: 14-noyabr -> "oʻn toʻrtinchi noyabr", 2-chi -> "ikkinchi"
    text = re.sub(r"\b(\d{1,4})-(?:chi|nchi|inchi)\b", lambda m: ordinal_words(int(m.group(1))), text)
    text = re.sub(r"\b(\d{1,4})-(?=[A-Za-zʻ])", lambda m: ordinal_words(int(m.group(1))) + " ", text)
    # sana: 15.11.2026 -> "oʻn beshinchi noyabr ikki ming yigirma olti yil"
    def _date(m):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            return m.group()
        return f"{ordinal_words(d)} {_MONTHS[mo - 1]}" + (f" {number_words(int(y))} yil" if y else "")
    text = re.sub(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)", _date, text)
    # o'nli kasr: 1.5 -> "bir yarim", 2,25 -> "ikki butun yuzdan yigirma besh"
    text = re.sub(r"\b(\d+)[.,](\d{1,3})\b", lambda m: _decimal_words(m.group(1), m.group(2)), text)
    # qolgan butun sonlar; qo'shimcha yopishgan bo'lsa ajratamiz: "25mingga" -> "yigirma besh mingga"
    text = re.sub(r"\d+", lambda m: (" " + number_words(int(m.group())) + " ") if len(m.group()) <= 15
                  else " ".join(_ONES[int(c)] for c in m.group()), text)
    # ajralib qolgan qo'shimchani songa yopishtiramiz: "oʻn besh dan" -> "oʻn beshdan", "yil da" -> "yilda"
    return _SUFFIX_GAP.sub(r"\1\2", text)


_NUM_WORDS = sorted(set(_ONES + _TENS[1:] + ["yuz", "ming", "million", "milliard", "trillion", "yil", "yarim"]),
                    key=len, reverse=True)
_SUFFIX_GAP = re.compile(
    rf"\b({'|'.join(_NUM_WORDS)}|\w+inchi|\w+nchi)\s+-?(tagacha|tadan|gacha|ning|dagi|dan|lik|lar|ga|da|ta|ni)\b")


def prepare_text(text: str, lang: str = "uz") -> str:
    """Matnni ovozli o'qishga tayyorlaydi (sof funksiya)."""
    t = _URL.sub(" havola ", text or "")
    t = _EMOJI.sub(" ", t)
    t = re.sub(r"[*_`#<>~|]+", " ", t)
    t = re.sub(r"@(\w+)", r"\1", t)
    if lang == "uz":
        if any(c in _UZ_CYR_ONLY for c in t) or len(re.findall(r"[А-Яа-я]", t)) > len(re.findall(r"[A-Za-z]", t)):
            t = _translit_uz(t)
        for rx, repl in _ABBR:
            t = rx.sub(repl, t)
        t = _OG.sub(lambda m: m.group(1) + "ʻ", t)   # o' g' -> oʻ gʻ (rasmiy belgi, to'g'ri talaffuz)
        t = _TUTUQ.sub("ʼ", t)                       # ma'no -> maʼno
        t = _numbers_to_words(t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t[:MAX_CHARS]


# ---------- audio ----------
def to_ogg_opus(data: bytes) -> bytes:
    """mp3/wav -> OGG/Opus (48 kHz, mono) — Telegram uni haqiqiy ovozli xabar (to'lqin chizig'i bilan) ko'rsatadi."""
    import av

    src = av.open(io.BytesIO(data))
    buf = io.BytesIO()
    out = av.open(buf, "w", format="ogg")
    stream = out.add_stream("libopus", rate=48000)
    stream.bit_rate = 40000
    stream.layout = "mono"
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    try:
        for frame in src.decode(audio=0):
            for r in resampler.resample(frame):
                for p in stream.encode(r):
                    out.mux(p)
        for r in resampler.resample(None):
            for p in stream.encode(r):
                out.mux(p)
        for p in stream.encode(None):
            out.mux(p)
    finally:
        out.close()
        src.close()
    return buf.getvalue()


async def _voicelab(text: str, lang: str) -> bytes:
    import asyncio

    import voicelab

    wav = await voicelab.synthesize(text, language=lang)
    return await asyncio.to_thread(to_ogg_opus, wav)


async def synthesize(text: str) -> tuple[bytes, str]:
    """(OGG/Opus audio, "voicelab"). Faqat VoiceLab; ishlamasa — istisno (zaxira ovoz yo'q)."""
    import voicelab

    lang = detect_lang(text)
    spoken = prepare_text(text, lang)
    if not spoken:
        raise ValueError("o'qiladigan matn yo'q")
    if not config.VOICELAB_API_KEY:
        raise voicelab.VoiceLabError(0, "no_api_key", "VOICELAB_API_KEY yo'q")
    return await _voicelab(spoken, lang), "voicelab"
