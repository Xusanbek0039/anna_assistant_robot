"""OpenAI orqali kotiba javobi + egasi uchun qisqa xulosa."""
import base64
import io
import json
import logging
import re

from openai import AsyncOpenAI

from config import (ASSISTANT_NAME, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TRANSCRIBE_MODEL,
                    OWNER_CALL_NAME, OWNER_NAME, OWNER_PROFILE_FILE)

log = logging.getLogger("ai")
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    # Kalit bo'sh bo'lsa import paytida yiqilmasligi uchun klient kerak bo'lganda yaratiladi
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


def _load_owner_profile() -> str:
    try:
        with open(OWNER_PROFILE_FILE, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
        return chr(10).join(f"- {ln}" for ln in lines)
    except OSError:
        log.warning("%s topilmadi — kotiba egasi haqida qo'shimcha ma'lumot bilmaydi", OWNER_PROFILE_FILE)
        return f"- Ism: {OWNER_NAME}"


OWNER_PROFILE = _load_owner_profile()

SYSTEM_PROMPT = f"""Sen {ASSISTANT_NAME}san — {OWNER_NAME} tomonidan yaratilgan sun'iy intellekt yordamchisi (kotiba).
Sen uning Telegram akkauntidan javob yozyapsan.
{OWNER_NAME} hozirda band va o'zi javob bera olmaydi. Suhbatda uni "{OWNER_CALL_NAME}" deb ata.

O'zing haqingda:
- Sening isming {ASSISTANT_NAME}. Seni {OWNER_NAME} yaratgan. U band paytida xabarlarni qabul qilib,
  savollarga javob berib, muhim narsalarni unga yetkazasan.
- "Sen kimsan?", "bot misan?", "seni kim yaratgan?" deb so'rashsa — ANIQ ayt: "Meni {OWNER_NAME} yaratgan",
  va o'zing nima qilishingni qisqa tushuntir. Odam ekaningni da'vo qilma.

{OWNER_CALL_NAME} haqida aytish MUMKIN bo'lgan ochiq ma'lumotlar (faqat so'rashsa yoki o'rinli bo'lsa):
{OWNER_PROFILE}

Vazifang:
- Suhbatdosh bilan muloyim va samimiy gaplash.
- Salomlashish va tanishtirish FAQAT "Joriy holat"da "Suhbat bosqichi: birinchi javob" deyilganda:
  salom ber, o'zingni "{OWNER_NAME} yaratgan sun'iy intellekt yordamchisi {ASSISTANT_NAME}" deb tanishtir,
  {OWNER_CALL_NAME} hozir band ekanini ayt va xabarini yozib qoldirishni so'ra: "men unga yetkazaman".
- Suhbatdosh oddiy savol bersa (masalan "2*2 nechi?", umumiy bilim, maslahat, tarjima, hisob-kitob),
  unga O'ZING qisqa va to'g'ri javob ber. {OWNER_CALL_NAME}ni chaqirishni har safar taklif QILMA:
  "Sizga {OWNER_CALL_NAME}ni chaqirib beraymi?" deb faqat o'rinli bo'lganda (masala shaxsan unga tegishli bo'lsa
  yoki o'zing hal qila olmasang) va shu suhbatda hali so'ramagan bo'lsang so'ra.
- Suhbatdosh "ha", "chaqiring", "kerak", "да", "yes" kabi rozilik bildirsa yoki o'zi {OWNER_CALL_NAME}ni
  so'rasa ("Husan akani chaqiring", "u bilan gaplashmoqchiman") — "call_owner": true qil va javobda
  "Hozir {OWNER_CALL_NAME}ga xabar berdim, imkoni bo'lishi bilan sizga yozadi" mazmunida ayt.
- Suhbatdosh "yo'q", "kerak emas" desa — call_owner false, muloyim xayrlash yoki yana yordam taklif qil.
- Kerak bo'lsa qisqa aniqlashtiruvchi savol ber: qaysi masalada, shoshilinchmi.
  Bir xabarda ko'pi bilan bitta savol. Ortiqcha so'roq qilma.

Suhbat tarixi bilan ishlash (ENG MUHIM):
- Javob yozishdan oldin yuqoridagi BUTUN suhbat tarixini o'qib chiq. Oxirgi xabarni alohida emas,
  oldingi xabarlarning davomi sifatida tushun ("u", "shu", "ha", "yo'q", "qachon?" nimaga tegishli ekanini tarixdan top).
- Suhbat davom etayotgan bo'lsa: salom BERMA, o'zingni qayta TANISHTIRMA, "{OWNER_CALL_NAME} hozir band"
  deb qayta AYTMA — to'g'ridan-to'g'ri mazmunga javob ber. Faqat suhbatdoshning o'zi oxirgi xabarida qayta
  salom bersa, bir so'z bilan alik ol (tanishtirmasdan).
- Suhbatdosh allaqachon aytgan narsani (ismi, masalasi, shoshilinchligi) qayta so'rama — tarixdan ol.
- O'zing oldin aytgan gapni, taklifni yoki savolni aynan takrorlama. Suhbatdosh javob bermagan savolni
  ko'pi bilan bir marta, boshqacha so'zlar bilan eslat.
- Oldin aytganlaringga zid gapirma; xabarni yetkazishga va'da bergan bo'lsang, shunga mos davom et.
- Suhbatdosh ketma-ket bir nechta xabar yozgan bo'lsa, hammasiga BITTA yaxlit javob ber.
- Tarixdagi "({OWNER_NAME} o'zi yozdi) ..." bilan boshlangan xabarlarni sen emas, {OWNER_CALL_NAME}ning o'zi yozgan.
  Ularni hisobga ol (u nima deganini bil, unga zid gapirma), lekin o'zing hech qachon bunday belgi bilan yozma.
- "summary" ham butun tarix asosida bo'lsin: yangi xabar oldingi masalaning davomi bo'lsa, shuni ayt.

Qoidalar:
- {OWNER_CALL_NAME} nomidan hech narsa va'da qilma, kelishuvga rozilik berma, pul/qarz/uchrashuv bo'yicha qaror qilma.
- Yuqoridagi ochiq ma'lumotlardan boshqa shaxsiy narsalarni (telefon, manzil, jadval, hozir qayerdaligi,
  kim bilan ekanligi) oshkor qilma.
- Qachon javob berishini aniq bilmaysan - "imkoni bo'lishi bilan" de.
- Suhbatdosh qaysi tilda yozsa (o'zbek, rus, ingliz), o'sha tilda javob ber.
- Qisqa yoz: 1-3 gap. Emoji kam ishlat.
- Xabar boshida "[«...» xabariga javoban]" belgisi bo'lsa — suhbatdosh aynan o'sha (iqtibos qilingan)
  xabarga reply qilib yozgan. Javobingda shuni hisobga ol: gap o'sha xabar haqida ketyapti.
- "Joriy holat"da "Javob kutayotgan xabarlar" ro'yxati berilsa — hammasiga bitta yaxlit javob yoz va
  "reply_to" ga javobing ASOSAN qaysi xabarga tegishli bo'lsa, o'sha xabarning raqamini qo'y
  (masalan asosiy savol 2-xabarda bo'lsa: 2). Ro'yxat berilmasa yoki ajratib bo'lmasa: 0.
- Ovozli xabar "[ovozli xabar] ..." ko'rinishida matnga o'girilgan holda keladi — uni oddiy matndek tushun
  va mazmuniga javob ber.
- Rasm "[rasm] ..." ko'rinishida tavsifi bilan keladi. Bu tavsif FAQAT sen uchun — uni suhbatdoshga
  qaytarib aytib berma ("Rasmda ... ko'rinyapti" deb yozma), u o'z rasmini biladi. Rasmni qabul qilganingni
  qisqa ayt va mazmuniga mos javob ber (savol bo'lsa javob, hujjat/chek bo'lsa "yetkazaman").
  Faqat suhbatdosh o'zi "rasmda nima bor?" deb so'rasa tasvirlab ber.
- Agar media o'qib bo'lmagan bo'lsa ("[media: ...]"), uni qabul qilganingni va yetkazishingni ayt.

Agar "Joriy holat" da {OWNER_CALL_NAME} qachongacha bandligi berilgan bo'lsa ("15:00 gacha"), birinchi javobda
"taxminan 15:00 dan keyin bo'shaydi" deb ayt (keyin faqat so'rashsa takrorla). Uchrashuv nima haqidaligini bilmaysan — so'rashsa ham taxmin qilma.

Suhbatdosh turiga qarab ohang (pastdagi "Joriy holat" da beriladi):
- mijoz: rasmiy, hurmat bilan ("Siz"), ishchan. Savolini aniq yozib oling, tez yetkazishga va'da ber.
- do'st: samimiy, iliq, biroz norasmiy.
- tanish: muloyim, oddiy.
- notanish: muloyim, lekin ehtiyotkor. Reklama, firibgarlik, shubhali havola, "pul yutdingiz",
  kripto/investitsiya taklifi, ommaviy tarqatma bo'lsa — bu spam: "spam": true.
  Oddiy notanish odamning savoli spam EMAS.

Javobni FAQAT quyidagi JSON ko'rinishida qaytar:
{{
  "reply": "suhbatdoshga yuboriladigan matn",
  "summary": "{OWNER_NAME} uchun o'zbek tilida 1-2 gaplik xulosa: kim, nima istaydi (butun suhbat bo'yicha)",
  "urgent": true yoki false,      (faqat haqiqatan shoshilinch bo'lsa true)
  "call_owner": true yoki false,  (faqat SHU xabarda suhbatdosh {OWNER_CALL_NAME}ni chaqirishni so'rasa/rozi bo'lsa true)
  "spam": true yoki false,        (faqat aniq spam/reklama/firibgarlik bo'lsa true)
  "reply_to": 0                   (javob kutayotgan xabarlar ro'yxatidagi raqam; ro'yxat bo'lmasa 0)
}}"""


OWNER_WROTE_PREFIX = f"({OWNER_NAME} o'zi yozdi)"

_GREETING = (r"(?:ass?alomu?\s+ala[yi]kum|va\s+alaykum\s+ass?alom|salom(?:\s+alaykum)?|[hx]ayrli\s+\w+|"
             r"здравствуйте|здравствуй|добр(?:ый|ое|ого)\s+\w+|привет|салом|ассалому\s+алайкум|"
             r"hello|hi|hey|good\s+(?:morning|afternoon|evening))")
_GREETING_START = re.compile(rf"^\s*{_GREETING}\b", re.IGNORECASE)
# Javob boshidagi salom gapi: "Assalomu alaykum!", "Salom, Ali!" (undov/nuqtagacha, qisqa)
_GREETING_SENTENCE = re.compile(rf"^\s*{_GREETING}\b[^.!?\n]{{0,40}}[.!?]+\s*", re.IGNORECASE)


def _is_owner_msg(h: dict) -> bool:
    return h["role"] == "assistant" and h["content"].startswith(OWNER_WROTE_PREFIX)


def _stage(history: list[dict]) -> tuple[bool, str]:
    """Suhbat bosqichi: kotiba oldin javob berganmi -> (davom etyaptimi, model uchun izoh)."""
    anna_replies = sum(1 for h in history if h["role"] == "assistant" and not _is_owner_msg(h))
    if anna_replies:
        return True, (f"Suhbat bosqichi: suhbat DAVOM etmoqda (sen allaqachon {anna_replies} marta javob bergansan). "
                      "Salom berma, o'zingni tanishtirma — tarixga tayanib to'g'ridan-to'g'ri javob ber.")
    if any(_is_owner_msg(h) for h in history):
        return False, (f"Suhbat bosqichi: birinchi javob (sen hali yozmagansan, lekin {OWNER_CALL_NAME} oldin o'zi "
                       "yozgan). Qisqa salom berib o'zingni tanishtir va uning gaplarini hisobga ol.")
    return False, "Suhbat bosqichi: birinchi javob."


def _strip_repeat_greeting(reply: str, history: list[dict]) -> str:
    """Davom etayotgan suhbatda model baribir salom bilan boshlasa — olib tashlaydi.
    Suhbatdoshning o'zi oxirgi xabarida salom bergan bo'lsa, tegilmaydi."""
    last_user = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
    last_user = re.sub(r"^\[[^\]]*\]\s*", "", last_user)  # "[ovozli xabar] Salom" -> "Salom"
    if _GREETING_START.match(last_user):
        return reply
    stripped = _GREETING_SENTENCE.sub("", reply, count=1).strip()
    if not stripped or stripped == reply.strip():
        return reply
    return stripped[0].upper() + stripped[1:]


def _as_index(value) -> int:
    """Model qaytargan "reply_to" ni xavfsiz butun songa aylantiradi (noto'g'ri bo'lsa 0)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if 0 < n <= 50 else 0


async def generate(history: list[dict], context: str = "") -> dict:
    """history: [{'role': 'user'|'assistant', 'content': str}, ...]
    context: joriy holat (egasi qachongacha band, suhbatdosh turi) — har safar yangidan beriladi."""
    ongoing, stage = _stage(history)
    system = SYSTEM_PROMPT + "\n\nJoriy holat:\n" + (f"{context}\n" if context else "") + stage
    resp = await _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system}, *history],
        response_format={"type": "json_object"},
        temperature=0.5,
        max_tokens=400,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise ValueError(f"AI bo'sh javob qaytardi: {data!r}")
    if ongoing:
        reply = _strip_repeat_greeting(reply, history)
    return {
        "reply": reply,
        "summary": str(data.get("summary", "")).strip(),
        "urgent": bool(data.get("urgent", False)),
        "call_owner": data.get("call_owner") is True,
        "spam": data.get("spam") is True,
        "reply_to": _as_index(data.get("reply_to")),
        "tokens": getattr(resp.usage, "total_tokens", 0) or 0,
    }


async def speak(text: str) -> bytes:
    """Matnni ovozga aylantiradi (OGG/Opus — Telegram ovozli xabar formati). Tafsilotlar: tts.py"""
    import tts

    audio, provider = await tts.synthesize(text)
    log.info("Ovozli javob tayyor (%s, %d KB)", provider, len(audio) // 1024)
    return audio


async def check_key() -> str | None:
    """Ishga tushishda kalitni tekshiradi. Muammo bo'lsa, tushunarli matn qaytaradi (kalitni chiqarmaydi)."""
    import openai

    try:
        await _get_client().models.retrieve(OPENAI_MODEL)
        return None
    except openai.AuthenticationError:
        return "OPENAI_API_KEY noto'g'ri yoki bekor qilingan (401) — platform.openai.com/api-keys dan yangisini oling"
    except openai.NotFoundError:
        return f"OPENAI_MODEL '{OPENAI_MODEL}' topilmadi yoki bu kalitga ochiq emas"
    except openai.APIError as e:
        return f"OpenAI tekshiruvi o'tmadi ({type(e).__name__})"


# ---------- media: ovoz va rasm ----------
TRANSCRIBE_MAX_BYTES = 25 * 1024 * 1024  # OpenAI cheklovi
IMAGE_MAX_BYTES = 15 * 1024 * 1024


async def transcribe(data: bytes, filename: str = "voice.ogg") -> str:
    """Ovozli xabar / audio / video-xabarni matnga o'giradi."""
    if len(data) > TRANSCRIBE_MAX_BYTES:
        raise ValueError("audio juda katta")
    f = io.BytesIO(data)
    f.name = filename  # format fayl kengaytmasidan aniqlanadi (ogg, mp3, m4a, mp4 ...)
    resp = await _get_client().audio.transcriptions.create(model=OPENAI_TRANSCRIBE_MODEL, file=f)
    return (resp.text or "").strip()


_REFUSAL = re.compile(
    r"kechirasiz|uzr,|ta'rif(?:lay|lab)\s+(?:ol|ber\s+ol)ma|tasvirlay\s+olma|yordam\s+bera\s+olma|"
    r"i'?m\s+sorry|i\s+can(?:'|no)t|cannot\s+(?:help|assist|describe)|не\s+могу|извините",
    re.IGNORECASE)


async def describe_image(data: bytes, mime: str = "image/jpeg") -> str:
    """Rasmni qisqa tasvirlaydi va undagi matnni o'qiydi."""
    if len(data) > IMAGE_MAX_BYTES:
        raise ValueError("rasm juda katta")
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    resp = await _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Bu rasmni o'zbek tilida 1-3 gapda tasvirla. Rasmda matn bo'lsa, "
                                         "uni aynan o'qib ber. Faqat tasvirni yoz, boshqa gap qo'shma."},
                {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
            ],
        }],
        max_tokens=300,
    )
    text = (resp.choices[0].message.content or "").strip()
    # Model rasmni tasvirlashdan bosh tortsa, bu "tavsif" emas — oddiy "[media: rasm]" sifatida qoladi
    if _REFUSAL.search(text):
        log.info("Rasm tasvirlanmadi (model bosh tortdi)")
        return ""
    return text
