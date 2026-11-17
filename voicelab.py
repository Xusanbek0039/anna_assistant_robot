"""VoiceLab (voicelab.uz): ovozni matnga o'girish (STT) va matnni o'zbekcha ovozga aylantirish (TTS).

Jarayon (https://docs.voicelab.uz/api/stt):
  1. POST /v1/stt (multipart: audio, language) -> 202 {"id": "stt_...", "status": "queued"}
  2. GET /v1/stt/transcriptions/{id} -> status: queued | processing | completed | failed
     tayyor bo'lgach matn `transcript` maydonida.
  3. Maxfiylik uchun natija o'qilgach VoiceLab'dagi yozuv o'chiriladi (VOICELAB_DELETE_AFTER).

Qo'shimcha kutubxona kerak emas: standart urllib, alohida oqimda (asyncio.to_thread).
"""
import asyncio
import json
import logging
import mimetypes
import time
import urllib.error
import urllib.request
import uuid

import config

log = logging.getLogger("voicelab")

USER_AGENT = "anna-assistant/1.0"
SUBMIT_RETRIES = 3        # 429/503 yoki tarmoq xatosida qayta yuborish (o'sha Idempotency-Key bilan)
POLL_INTERVAL = 1.0       # hujjatda tavsiya etilgan: soniyada bir marta (Retry-After bo'lsa — o'shancha)
_MIME = {"ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "mp3": "audio/mpeg", "wav": "audio/wav",
         "m4a": "audio/mp4", "aac": "audio/aac", "webm": "audio/webm", "flac": "audio/flac", "mp4": "audio/mp4"}


class VoiceLabError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"VoiceLab {status} {code}: {message}".strip())
        self.status, self.code = status, code


def enabled() -> bool:
    return bool(config.VOICELAB_API_KEY)


def audio_tools_problem() -> str | None:
    """Ovozli javob uchun `av` kerak (WAV/MP3 -> Telegram OGG/Opus). Yo'q bo'lsa — muammo matni."""
    try:
        import av  # noqa: F401
        return None
    except Exception as e:
        return (f"`av` kutubxonasi ishlamayapti ({type(e).__name__}) — ovozli javob ishlamaydi (matn yuboriladi). "
                "Serverda: pip install -r requirements.txt")


def describe() -> str:
    """Ovoz qaysi xizmat orqali ishlayotgani (log va /health uchun; kalit ko'rsatilmaydi)."""
    stt = f"VoiceLab (til: {config.VOICELAB_LANGUAGE})" if enabled() else "OpenAI (VOICELAB_API_KEY yo'q)"
    tts = f"VoiceLab ({config.VOICELAB_TTS_VOICE})" if enabled() else "o'chiq (VOICELAB_API_KEY yo'q)"
    problem = audio_tools_problem()
    return f"ovoz -> matn: {stt}; javob ovozi: {tts}" + (f"\n⚠️ {problem}" if problem else "")


def _multipart(fields: dict[str, str], filename: str, data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----anna{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="{filename}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _request(method: str, path: str, body: bytes | None = None, headers: dict | None = None,
             timeout: float = 60) -> tuple[int, dict, dict]:
    """(HTTP status, JSON javob, sarlavhalar). 4xx/5xx — VoiceLabError."""
    req = urllib.request.Request(config.VOICELAB_BASE_URL.rstrip("/") + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {config.VOICELAB_API_KEY}")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if raw and "json" not in ctype:  # masalan TTS: audio/wav
                return resp.status, {"_raw": raw, "_ctype": ctype}, dict(resp.headers)
            return resp.status, (json.loads(raw) if raw else {}), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            err = json.loads(raw)
        except ValueError:
            err = {}
        err = err.get("error", err) if isinstance(err, dict) else {}
        code = (err.get("code") if isinstance(err, dict) else None) or "http_error"
        msg = (err.get("message") if isinstance(err, dict) else None) or ""
        exc = VoiceLabError(e.code, code, msg)
        exc.retry_after = e.headers.get("Retry-After") if e.headers else None
        raise exc from None


def _retry_after(headers, default: float) -> float:
    try:
        return max(0.5, min(30.0, float(headers.get("Retry-After"))))
    except (TypeError, ValueError, AttributeError):
        return default


async def _call(*args, **kw):
    return await asyncio.to_thread(_request, *args, **kw)


async def transcribe(data: bytes, filename: str = "voice.ogg", language: str | None = None) -> str:
    """Audio -> matn. Gap topilmasa "" qaytaradi. Boshqa xatolarda VoiceLabError/OSError."""
    if not enabled():
        raise VoiceLabError(0, "no_api_key", "VOICELAB_API_KEY yo'q")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "ogg"
    mime = _MIME.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body, ctype = _multipart({"language": language or config.VOICELAB_LANGUAGE, "include_speakers": "false"},
                             filename, data, mime)
    headers = {"Content-Type": ctype, "Idempotency-Key": str(uuid.uuid4())}  # qayta urinishda ham o'sha kalit

    job = None
    for attempt in range(1, SUBMIT_RETRIES + 1):
        try:
            _, job, _ = await _call("POST", "/v1/stt", body, headers, timeout=120)
            break
        except VoiceLabError as e:
            if e.code == "no_speech_detected":
                return ""
            if e.status not in (429, 503) or attempt == SUBMIT_RETRIES:
                raise
            await asyncio.sleep(_retry_after({"Retry-After": getattr(e, "retry_after", None)}, 2.0 * attempt))
        except OSError:  # tarmoq uzilishi / timeout — o'sha kalit bilan qayta (ikki marta pul yechilmaydi)
            if attempt == SUBMIT_RETRIES:
                raise
            await asyncio.sleep(2.0 * attempt)
    job_id = (job or {}).get("id")
    if not job_id:
        raise VoiceLabError(0, "no_job_id", "javobda id yo'q")

    deadline = time.monotonic() + config.VOICELAB_TIMEOUT
    try:
        while True:
            try:
                _, res, hdrs = await _call("GET", f"/v1/stt/transcriptions/{job_id}", timeout=30)
            except VoiceLabError as e:
                if e.status not in (429, 503):
                    raise
                res, hdrs = {"status": "processing"}, {"Retry-After": getattr(e, "retry_after", None)}
            status = res.get("status")
            if status == "completed":
                return (res.get("transcript") or "").strip()
            if status == "failed":
                err = res.get("error") or {}
                code = err.get("code", "failed") if isinstance(err, dict) else "failed"
                if code == "no_speech_detected":
                    return ""
                raise VoiceLabError(200, code, "transkripsiya muvaffaqiyatsiz")
            if time.monotonic() > deadline:
                raise VoiceLabError(0, "timeout", f"{config.VOICELAB_TIMEOUT}s ichida tayyor bo'lmadi")
            await asyncio.sleep(_retry_after(hdrs, POLL_INTERVAL))
    finally:
        if config.VOICELAB_DELETE_AFTER:
            try:  # suhbatdoshlarning ovozi VoiceLab'da saqlanib qolmasin
                await _call("DELETE", f"/v1/stt/transcriptions/{job_id}", timeout=30)
            except Exception as e:
                log.debug("VoiceLab yozuvi o'chirilmadi (%s)", type(e).__name__)


async def check_key() -> str | None:
    """Kalit ishlayaptimi (holat nazorati uchun). None — hammasi joyida, aks holda muammo matni."""
    try:
        _, res, _ = await _call("GET", "/v1/stt/preflight", timeout=20)  # kredit sarflamaydi
        return None if res.get("can_upload", True) else "yuklash mumkin emas (kredit tugaganmi?)"
    except VoiceLabError as e:
        return {"invalid_api_key": "kalit noto'g'ri", "insufficient_scope": "kalitda STT ruxsati yo'q",
                "insufficient_credits": "kredit tugagan"}.get(e.code, str(e))
    except OSError as e:
        return f"ulanib bo'lmadi ({type(e).__name__})"


# ---------- TTS: matn -> o'zbekcha ovoz ----------
_TTS_HINTS = {
    "no_api_key": "VOICELAB_API_KEY .env da yo'q",
    "invalid_api_key": "API kalit noto'g'ri yoki bekor qilingan — voicelab.uz/app/developer/api-keys",
    "insufficient_scope": "kalitda TTS ruxsati yo'q",
    "insufficient_credits": "kredit tugagan — voicelab.uz da hisobni to'ldiring",
    "timeout": "VoiceLab javobi juda kechikdi",
    "tts_failed": "VoiceLab ovozni yarata olmadi",
}


def explain_error(e: Exception) -> str:
    """VoiceLab TTS xatosini egasi uchun tushunarli matnga aylantiradi (kalit ko'rsatilmaydi)."""
    if isinstance(e, VoiceLabError):
        hint = _TTS_HINTS.get(e.code)
        if not hint and e.status == 429:
            hint = "so'rovlar limiti oshib ketdi (429)"
        elif not hint and e.status >= 500:
            hint = f"VoiceLab serverida nosozlik ({e.status})"
        return f"{hint} [{e}]" if hint else str(e)
    if isinstance(e, OSError):
        return f"VoiceLab'ga ulanib bo'lmadi ({type(e).__name__}: {e})"
    if isinstance(e, ImportError):
        return audio_tools_problem() or f"kutubxona yo'q ({e})"
    return f"{type(e).__name__}: {e}"


def _download(url: str, timeout: float = 60) -> bytes:
    """Imzolangan audio havolasi — Authorization SIZ yuklanadi (hujjat talabi)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


async def synthesize(text: str, language: str = "uz", voice_id: str | None = None,
                     speed: float | None = None) -> bytes:
    """Matn -> WAV audio (24 kHz, mono). Qisqa matn darhol qaytadi; uzoq davom etsa — tayyor bo'lguncha kutiladi."""
    if not enabled():
        raise VoiceLabError(0, "no_api_key", "VOICELAB_API_KEY yo'q")
    payload = {"text": text[:5000], "language": language,
               "voice_id": voice_id or config.VOICELAB_TTS_VOICE,
               "speed": max(0.5, min(2.0, speed or config.VOICELAB_TTS_SPEED))}
    if config.VOICELAB_TTS_MODEL:
        payload["model_id"] = config.VOICELAB_TTS_MODEL
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", "Accept": "audio/wav, application/json",
               "Idempotency-Key": str(uuid.uuid4())}
    res = {}
    for attempt in range(1, SUBMIT_RETRIES + 1):
        try:
            _, res, _ = await _call("POST", "/v1/tts", body, headers, timeout=90)
            break
        except VoiceLabError as e:
            if e.status not in (429, 500, 503) or attempt == SUBMIT_RETRIES:
                raise
            await asyncio.sleep(_retry_after({"Retry-After": getattr(e, "retry_after", None)}, 2.0 * attempt))
        except OSError:
            if attempt == SUBMIT_RETRIES:
                raise
            await asyncio.sleep(2.0 * attempt)
    if res.get("_raw"):
        return res["_raw"]
    gen_id = res.get("id")
    if not gen_id:
        raise VoiceLabError(0, "no_audio", "javobda audio ham, id ham yo'q")
    deadline = time.monotonic() + config.VOICELAB_TIMEOUT
    while True:
        _, job, hdrs = await _call("GET", f"/v1/tts/generations/{gen_id}", timeout=30)
        status = job.get("status")
        if status == "completed" and job.get("audio_url"):
            return await asyncio.to_thread(_download, job["audio_url"])
        if status == "failed":
            raise VoiceLabError(200, "tts_failed", "ovoz yaratilmadi")
        if time.monotonic() > deadline:
            raise VoiceLabError(0, "timeout", f"{config.VOICELAB_TIMEOUT}s ichida tayyor bo'lmadi")
        await asyncio.sleep(_retry_after(hdrs, POLL_INTERVAL))
