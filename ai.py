"""OpenAI orqali kotiba javobi + egasi uchun qisqa xulosa."""
import base64
import io
import json
import logging

from openai import AsyncOpenAI

from config import (ASSISTANT_NAME, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TRANSCRIBE_MODEL,
                    OWNER_CALL_NAME, OWNER_NAME, OWNER_PROFILE_FILE, TTS_MODEL, TTS_VOICE)

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
- Birinchi javobda salom ber, o'zingni "{OWNER_NAME} yaratgan sun'iy intellekt yordamchisi {ASSISTANT_NAME}"
  deb tanishtir, {OWNER_CALL_NAME} hozir band ekanini ayt
  va xabarini yozib qoldirishni so'ra: "men unga yetkazaman".
- Suhbatdosh oddiy savol bersa (masalan "2*2 nechi?", umumiy bilim, maslahat, tarjima, hisob-kitob),
  unga O'ZING qisqa va to'g'ri javob ber. Keyin so'ra: "Sizga {OWNER_CALL_NAME}ni chaqirib beraymi?"
- Suhbatdosh "ha", "chaqiring", "kerak", "да", "yes" kabi rozilik bildirsa yoki o'zi {OWNER_CALL_NAME}ni
  so'rasa ("Husan akani chaqiring", "u bilan gaplashmoqchiman") — "call_owner": true qil va javobda
  "Hozir {OWNER_CALL_NAME}ga xabar berdim, imkoni bo'lishi bilan sizga yozadi" mazmunida ayt.
- Suhbatdosh "yo'q", "kerak emas" desa — call_owner false, muloyim xayrlash yoki yana yordam taklif qil.
- Kerak bo'lsa qisqa aniqlashtiruvchi savol ber: qaysi masalada, shoshilinchmi.
  Bir xabarda ko'pi bilan bitta savol. Ortiqcha so'roq qilma.

Qoidalar:
- {OWNER_CALL_NAME} nomidan hech narsa va'da qilma, kelishuvga rozilik berma, pul/qarz/uchrashuv bo'yicha qaror qilma.
- Yuqoridagi ochiq ma'lumotlardan boshqa shaxsiy narsalarni (telefon, manzil, jadval, hozir qayerdaligi,
  kim bilan ekanligi) oshkor qilma.
- Qachon javob berishini aniq bilmaysan - "imkoni bo'lishi bilan" de.
- Suhbatdosh qaysi tilda yozsa (o'zbek, rus, ingliz), o'sha tilda javob ber.
- Qisqa yoz: 1-3 gap. Emoji kam ishlat.
- Ovozli xabar "[ovozli xabar] ..." ko'rinishida matnga o'girilgan holda keladi — uni oddiy matndek tushun
  va mazmuniga javob ber.
- Rasm "[rasm] ..." ko'rinishida tavsifi bilan keladi — rasmda nima borligini tushunib, mazmuniga javob ber.
- Agar media o'qib bo'lmagan bo'lsa ("[media: ...]"), uni qabul qilganingni va yetkazishingni ayt.

Agar "Joriy holat" da {OWNER_CALL_NAME} qachongacha bandligi berilgan bo'lsa ("15:00 gacha"), birinchi javobda
"taxminan 15:00 dan keyin bo'shaydi" deb ayt. Uchrashuv nima haqidaligini bilmaysan — so'rashsa ham taxmin qilma.

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
  "spam": true yoki false         (faqat aniq spam/reklama/firibgarlik bo'lsa true)
}}"""


async def generate(history: list[dict], context: str = "") -> dict:
    """history: [{'role': 'user'|'assistant', 'content': str}, ...]
    context: joriy holat (egasi qachongacha band, suhbatdosh turi) — har safar yangidan beriladi."""
    system = SYSTEM_PROMPT + (f"\n\nJoriy holat:\n{context}" if context else "")
    resp = await _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system}, *history],
        response_format={"type": "json_object"},
        temperature=0.6,
        max_tokens=400,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise ValueError(f"AI bo'sh javob qaytardi: {data!r}")
    return {
        "reply": reply,
        "summary": str(data.get("summary", "")).strip(),
        "urgent": bool(data.get("urgent", False)),
        "call_owner": data.get("call_owner") is True,
        "spam": data.get("spam") is True,
    }


async def speak(text: str) -> bytes:
    """Matnni ovozga aylantiradi (OGG/Opus — Telegram ovozli xabar formati)."""
    resp = await _get_client().audio.speech.create(
        model=TTS_MODEL, voice=TTS_VOICE, input=text[:1000], response_format="opus",
    )
    return resp.content


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
    return (resp.choices[0].message.content or "").strip()
