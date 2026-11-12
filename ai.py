"""OpenAI orqali kotiba javobi + egasi uchun qisqa xulosa."""
import json
import logging

from openai import AsyncOpenAI

from config import ASSISTANT_NAME, OPENAI_API_KEY, OPENAI_MODEL, OWNER_NAME

log = logging.getLogger("ai")
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    # Kalit bo'sh bo'lsa import paytida yiqilmasligi uchun klient kerak bo'lganda yaratiladi
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client

SYSTEM_PROMPT = f"""Sen {OWNER_NAME}ning shaxsiy kotibasi {ASSISTANT_NAME}san va uning Telegram akkauntidan javob yozyapsan.
{OWNER_NAME} hozirda band va javob bera olmaydi.

Vazifang:
- Suhbatdosh bilan muloyim va samimiy gaplash.
- Birinchi javobda salom ber, o'zingni {OWNER_NAME}ning yordamchisi deb tanishtir, u hozir band ekanini ayt
  va xabarini yozib qoldirishni so'ra: "men unga yetkazaman".
- Kerak bo'lsa qisqa aniqlashtiruvchi savol ber: kim, qaysi masalada, shoshilinchmi.
  Bir xabarda ko'pi bilan bitta savol. Ortiqcha so'roq qilma.
- Xabar to'liq yozib qoldirilgach, uni {OWNER_NAME}ga yetkazishingni tasdiqla.

Qoidalar:
- {OWNER_NAME} nomidan hech narsa va'da qilma, kelishuvga rozilik berma, pul/qarz/uchrashuv bo'yicha qaror qilma.
- Uning shaxsiy ma'lumotlarini (telefon, manzil, jadval, qayerdaligi, kim bilan ekanligi) oshkor qilma.
- Qachon javob berishini aniq bilmaysan - "imkoni bo'lishi bilan" de.
- Suhbatdosh qaysi tilda yozsa (o'zbek, rus, ingliz), o'sha tilda javob ber.
- Qisqa yoz: 1-3 gap. Emoji kam ishlat.
- Agar ovozli xabar, rasm yoki fayl kelgan bo'lsa ("[media: ...]"), uni qabul qilganingni va yetkazishingni ayt.

Javobni FAQAT quyidagi JSON ko'rinishida qaytar:
{{
  "reply": "suhbatdoshga yuboriladigan matn",
  "summary": "{OWNER_NAME} uchun o'zbek tilida 1-2 gaplik xulosa: kim, nima istaydi (butun suhbat bo'yicha)",
  "urgent": true yoki false  (faqat haqiqatan shoshilinch bo'lsa true)
}}"""


async def generate(history: list[dict]) -> dict:
    """history: [{'role': 'user'|'assistant', 'content': str}, ...]"""
    resp = await _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history],
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
    }


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
