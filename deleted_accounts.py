"""O'chirilgan akkauntlar: suhbatdosh akkauntini o'chirsa, Telegram'da u «Deleted Account» bo'lib qoladi —
ismi ham, username'i ham yo'qoladi, lekin yozishma chatda turadi.

- scan(): akkauntdagi bunday chatlarni topadi va butun yozishmasini arxivga ko'chiradi;
- ism: arxivda oldin saqlangan nomi (siz kontaktga yozgan ism) bo'lsa — o'sha. Bo'lmasa — profil
  kuzatuvidagi oxirgi ismi, u ham bo'lmasa yozishmadan TAXMIN qilinadi (siz uni qanday chaqirgansiz:
  «Aziz aka», «Dilnoza»...) — AI bilan, AI yo'q bo'lsa oddiy qoida bilan;
- bot orqali ro'yxat: «o'chirilgan akkauntlarni ko'rsat», qidiruv esa taxminiy ism bo'yicha ham topadi.
"""
import json
import logging
import re
import time
from collections import Counter

from telethon.tl.types import Message

import archive
import config
import storage

log = logging.getLogger("deleted_accounts")

MIN_LINES = 3          # bundan kam xabarli yozishmadan ism taxmin qilinmaydi
GUESS_CHARS = 9000     # AI ga beriladigan yozishma hajmi

# «o'chirilgan akkauntlar», «delete bo'lgan hisoblar», «deleted account», «удалённые аккаунты»
_GONE_VERB = r"(?:o['ʻ’`]?chir\w*|o['ʻ’`]?chgan\w*|delet\w*|udal\w*|удал\w*)"
_GONE_NOUN = r"(?:ak+a?unt\w*|ac+a?unt\w*|ac+ount\w*|hisoblar\w*|аккаунт\w*|акаунт\w*)"
GONE_RE = re.compile(
    rf"\b{_GONE_VERB}\W+(?:[\w'ʻ’]+\W+){{0,3}}?{_GONE_NOUN}|\b{_GONE_NOUN}\W+(?:[\w'ʻ’]+\W+){{0,2}}?{_GONE_VERB}",
    re.IGNORECASE)


def asks_for_list(text: str) -> bool:
    """Egasi o'chirilgan akkauntlar haqida so'rayaptimi."""
    return bool(GONE_RE.search(text or ""))


_LIST_STOP = {"ko'rsat", "korsat", "ko'rsatib", "korsatib", "ro'yxat", "royxat", "ro'yxatini", "sanab", "qani",
              "kimlar", "hammasini", "list", "show", "покажи", "список", "bo'lgan", "bolgan", "qilib", "qilingan",
              "yuborilgan", "ketgan", "bor", "barcha", "hamma", "akkauntdagi", "akkauntlardagi", "haqida",
              "gaplashgan", "yozishgan", "chat", "chatni", "chatini", "yozishma", "yozishmani"}


def name_words(text: str) -> str:
    """So'rovdan «o'chirilgan akkaunt», «ko'rsat», «top» kabi so'zlar olib tashlangan qismi (ism yoki mavzu)."""
    import finder
    out = []
    for w in re.findall(r"[\w'ʻ’]+", text or "", re.UNICODE):
        n = finder._norm(w).strip("'")
        if (len(n) < 2 or n in finder._STOP or n in _LIST_STOP or finder._SEARCH_VERB.fullmatch(n)
                or re.fullmatch(_GONE_VERB, n, re.IGNORECASE) or re.fullmatch(_GONE_NOUN, n, re.IGNORECASE)):
            continue
        out.append(w)
    return " ".join(out)


# ---------- ismni taxmin qilish ----------
_GREET = re.compile(
    r"\b(?:salom|assalomu\s+alaykum|assalom|asalom|va\s+alaykum|rahmat|raxmat|hayrli\s+\w+|xayrli\s+\w+|"
    r"привет|здравствуй\w*|спасибо|hi|hello|hey)[\s,!.]+([A-ZА-ЯЁЎҚҒҲ][\w'ʻ’]{1,20})"
    r"(?:\s+(aka|opa|uka|singlim|ustoz|brat|akaxon|opajon|домла))?", re.UNICODE)
_TITLE = re.compile(r"\b([A-ZА-ЯЁЎҚҒҲ][\w'ʻ’]{1,20})\s+(aka|opa|akaxon|opajon|ustoz|domla|брат)\b", re.UNICODE)
_NOT_NAMES = {"Salom", "Rahmat", "Ha", "Yo'q", "Ok", "Xop", "Mayli", "Men", "Siz", "Sen", "Bu", "Shu", "Hayrli",
              "Xayrli", "Assalomu", "Va", "Kecha", "Bugun", "Ertaga", "Hello", "Hi", "Привет", "Ну", "Да", "Нет"}


def heuristic_name(docs: list[dict]) -> str:
    """AI'siz: egasining xabarlaridagi murojaatlar («Salom Aziz», «Sardor aka») — eng ko'p uchragani."""
    votes: Counter = Counter()
    for d in docs:
        if not d.get("out"):
            continue
        text = d.get("text") or ""
        for m in list(_GREET.finditer(text)) + list(_TITLE.finditer(text)):
            name = m.group(1)
            if name in _NOT_NAMES:
                continue
            votes[f"{name} {m.group(2)}" if m.group(2) else name] += 1
    if not votes:
        return ""
    # «Aziz» va «Aziz aka» — bitta odam: unvonli variant ustun
    best, _ = votes.most_common(1)[0]
    base = best.split()[0]
    titled = [n for n in votes if n.startswith(base + " ")]
    return max(titled, key=votes.get) if titled else best


NAME_PROMPT = """Bu Telegram yozishmasi. "Siz" — akkaunt egasi. Suhbatdosh akkauntini o'chirgan, uning ismi
endi ko'rinmaydi. Yozishmadan suhbatdosh kimligini TAXMIN qil: egasi unga qanday murojaat qilgan
("Aziz aka", "Dilnoza", "ustoz"), suhbatdosh o'zini qanday tanishtirgan, imzosi va h.k.
Faqat JSON qaytar: {"name": "", "confidence": "low", "hint": ""}
- name: kontakt nomi sifatida yoziladigan qisqa ism (1-3 so'z, masalan "Aziz aka", "Dilnoza opa").
  Ism umuman uchramasa — kimligini qisqa yoz ("Taksi haydovchisi", "Kvartira egasi"), confidence "low".
  Hech narsa aniqlab bo'lmasa — "".
- confidence: high | medium | low.
- hint: nimaga asoslanding (o'zbekcha, 1 qisqa gap)."""


async def ai_guess(docs: list[dict]) -> tuple[dict, int]:
    """({"name","confidence","hint"}, tokenlar). AI xatosida istisno chiqaradi."""
    import ai
    text = archive.chat_lines_from(docs, "Suhbatdosh", max_chars=GUESS_CHARS)
    resp = await ai._get_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": NAME_PROMPT}, {"role": "user", "content": text}],
        response_format={"type": "json_object"}, temperature=0, max_tokens=150,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    name = " ".join(str(data.get("name") or "").split())[:40]
    conf = data.get("confidence") if data.get("confidence") in ("high", "medium", "low") else "low"
    return ({"name": name, "confidence": conf, "hint": str(data.get("hint") or "")[:200]},
            getattr(resp.usage, "total_tokens", 0) or 0)


async def _ai_allowed() -> bool:
    if not config.OPENAI_API_KEY:
        return False
    try:
        st = await storage.get_stats()
        return not (config.AI_DAILY_LIMIT and st.get("ai_requests", 0) >= config.AI_DAILY_LIMIT)
    except Exception:
        return True


async def name_chat(info: dict, use_ai: bool = True) -> dict | None:
    """Bitta o'chirilgan akkaunt uchun ism topadi va arxivga yozadi. Topilgan maydonlar yoki None."""
    cid = info["chat_id"]
    if archive.has_real_title(info):
        return None  # siz saqlagan ism arxivda bor
    prof = await storage.profile_get(cid) or {}
    old = prof.get("known_name") or ("" if prof.get("deleted") else
                                     " ".join(p for p in (prof.get("first"), prof.get("last")) if p))
    if old:
        await storage.archive_chat_set(cid, title=old)
        return {"title": old}
    docs = await storage.archive_messages(cid, limit=config.ARCHIVE_EXPORT_LIMIT)
    texts = [d for d in docs if (d.get("text") or "").strip()]
    if len(texts) < MIN_LINES or info.get("guess_tried") == len(docs):
        return None
    fields: dict = {"guess_tried": len(docs), "guess_at": time.time()}
    if use_ai and await _ai_allowed():
        try:
            g, tokens = await ai_guess(docs)
            try:
                await storage.stat_inc(ai_requests=1, ai_tokens=tokens)
            except Exception:
                pass
            if g["name"]:
                fields.update(guess_title=g["name"], guess_conf=g["confidence"], guess_hint=g["hint"],
                              guess_source="ai")
        except Exception as e:
            log.warning("Ism AI bilan taxmin qilinmadi (%s) — oddiy qoida", type(e).__name__)
    if "guess_title" not in fields:
        name = heuristic_name(docs)
        if name:
            fields.update(guess_title=name, guess_conf="low", guess_source="rule",
                          guess_hint="sizning murojaatlaringizdan («Salom …», «… aka»)")
    await storage.archive_chat_set(cid, **fields)
    return fields


async def name_all(use_ai: bool = True) -> int:
    """Ismi noma'lum barcha o'chirilgan akkauntlarga ism topadi. Nomlanganlar soni."""
    named = 0
    for info in await storage.archive_deleted_accounts():
        try:
            res = await name_chat(info, use_ai)
            named += bool(res and (res.get("title") or res.get("guess_title")))
        except Exception as e:
            log.warning("Chat %s uchun ism topilmadi (%s)", info["chat_id"], type(e).__name__)
    return named


# ---------- Telegram'dan topish ----------
async def scan(user, limit: int | None = None) -> tuple[int, int]:
    """Akkauntdagi o'chirilgan akkauntli chatlarni topib, yozishmasini arxivga ko'chiradi.
    (topilgan chatlar, yangi qo'shilgan xabarlar)."""
    limit = limit or config.ARCHIVE_EXPORT_LIMIT
    found = added_total = 0
    async for dialog in user.iter_dialogs():
        ent = dialog.entity
        if not (dialog.is_user and archive.is_deleted_account(ent)):
            continue
        found += 1
        cid = dialog.id
        try:
            await storage.archive_mark_account_deleted(cid)
            last = await storage.archive_last_msg_id(cid)
            if dialog.message is not None and last and dialog.message.id <= last:
                continue  # hammasi arxivda
            docs = []
            async for m in user.iter_messages(ent, limit=limit, min_id=last):
                if isinstance(m, Message):
                    docs.append(archive._as_history(archive.message_doc(m, cid)))
            added = await storage.archive_add_many(docs)
            if docs:
                await storage.archive_touch_chat(cid, "", None, "user", max(d["date"] for d in docs), added,
                                                 account_deleted=True)
            added_total += added
        except Exception as e:
            log.warning("O'chirilgan akkaunt chati %s arxivlanmadi (%s)", cid, type(e).__name__)
    if found:
        log.info("O'chirilgan akkauntlar: %d ta chat, arxivga %d ta xabar qo'shildi", found, added_total)
    return found, added_total


async def refresh(user, use_ai: bool = True) -> tuple[int, int, int]:
    """scan + ismlarni topish. (chatlar, yangi xabarlar, nomlanganlar)."""
    found, added = await scan(user)
    named = await name_all(use_ai)
    return found, added, named


# ---------- matn ----------
_CONF = {"high": "ishonchli", "medium": "taxminiy", "low": "juda taxminiy"}


def describe(info: dict) -> str:
    """Ism va uning manbasi: «Aziz aka (siz saqlagan ism)» yoki «≈ Aziz aka — taxminiy, AI: ...»."""
    if archive.has_real_title(info):
        return f"{info['title']} — siz saqlagan ism"
    if info.get("guess_title"):
        how = _CONF.get(info.get("guess_conf"), "taxminiy")
        hint = f": {info['guess_hint']}" if info.get("guess_hint") else ""
        return f"≈ {info['guess_title']} — {how}{hint}"
    return "ismi noma'lum (yozishmadan aniqlab bo'lmadi)"
