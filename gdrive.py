"""Google Drive: chat arxivini HTML fayl qilib Drive papkasiga yuklash (har chatga bitta fayl, yangilanib boradi).

Kerakli .env sozlamalari:
  GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET — Google Cloud -> Credentials -> OAuth client (Desktop app)
  GOOGLE_OAUTH_REFRESH_TOKEN — bir marta `python drive_auth.py` bilan olinadi
  GOOGLE_DRIVE_ROOT_FOLDER_ID — fayllar tushadigan papka (Drive'dagi papka havolasining oxiridagi id)
"""
import asyncio
import json
import logging
import os
import time

import httpx

import config

log = logging.getLogger("gdrive")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive"
FIELDS = "id,webViewLink"

_token: tuple[str, float] = ("", 0.0)


class DriveError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(f"{status} {reason}")
        self.status, self.reason = status, reason


def enabled() -> bool:
    return bool(config.GOOGLE_OAUTH_CLIENT_ID and config.GOOGLE_OAUTH_CLIENT_SECRET
                and config.GOOGLE_OAUTH_REFRESH_TOKEN and config.GOOGLE_DRIVE_ROOT_FOLDER_ID)


def missing() -> list[str]:
    names = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN",
             "GOOGLE_DRIVE_ROOT_FOLDER_ID")
    return [n for n in names if not getattr(config, n)]


def _reason(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("message") or str(err.get("code"))
    return f"{err}: {body.get('error_description', '')}".strip(": ")


def explain(e: Exception) -> str:
    """Egasiga tushunarli sabab."""
    if isinstance(e, DriveError):
        if "invalid_grant" in e.reason:
            return ("refresh token yaroqsiz yoki muddati o'tgan — `python drive_auth.py` ni qayta ishga "
                    "tushiring. (OAuth ilovasi «Testing» holatida bo'lsa token 7 kunda o'chadi — "
                    "«In production» ga o'tkazing)")
        if "invalid_client" in e.reason:
            return "GOOGLE_OAUTH_CLIENT_ID yoki GOOGLE_OAUTH_CLIENT_SECRET noto'g'ri"
        if e.status == 404:
            return "GOOGLE_DRIVE_ROOT_FOLDER_ID papkasi topilmadi (yoki shu Google akkauntga tegishli emas)"
        if e.status == 403:
            return f"ruxsat yo'q ({e.reason}) — Google Cloud'da Drive API yoqilganini tekshiring"
        return str(e)
    return type(e).__name__


async def _access_token(client: httpx.AsyncClient) -> str:
    global _token
    if _token[0] and time.time() < _token[1] - 60:
        return _token[0]
    r = await client.post(TOKEN_URL, data={
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID, "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
        "refresh_token": config.GOOGLE_OAUTH_REFRESH_TOKEN, "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise DriveError(r.status_code, _reason(r))
    body = r.json()
    _token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
    return _token[0]


def _client() -> httpx.AsyncClient:
    # read=120: katta fayl oxirgi bo'lagidan keyin Google javobni biroz kechiktirib beradi
    return httpx.AsyncClient(timeout=httpx.Timeout(30, read=120, write=300))


async def check() -> str | None:
    """Token va papkani tekshiradi. Muammo bo'lsa — sababi, hammasi joyida bo'lsa None."""
    if not enabled():
        return "sozlanmagan: " + ", ".join(missing())
    try:
        async with _client() as c:
            tok = await _access_token(c)
            r = await c.get(f"{API}/files/{config.GOOGLE_DRIVE_ROOT_FOLDER_ID}",
                            params={"fields": "id,name,mimeType", "supportsAllDrives": "true"},
                            headers={"Authorization": f"Bearer {tok}"})
            if r.status_code != 200:
                raise DriveError(r.status_code, _reason(r))
            if r.json().get("mimeType") != "application/vnd.google-apps.folder":
                return "GOOGLE_DRIVE_ROOT_FOLDER_ID papka emas"
    except Exception as e:
        return explain(e)
    return None


CHUNK = 8 * 1024 * 1024   # resumable bo'lak hajmi — Google talabi: 256 KB ga karrali
MAX_RETRIES = 6           # tarmoq uzilsa — shuncha marta uzilgan joydan davom etadi


def _range_end(r: httpx.Response) -> int:
    """308 javobidagi "Range: bytes=0-N" -> keyingi yuboriladigan bayt (N+1). Hech narsa olinmagan bo'lsa 0."""
    rng = r.headers.get("range", "")
    try:
        return int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
    except (IndexError, ValueError):
        return 0


async def _resume_point(c: httpx.AsyncClient, url: str, total: int, auth: dict) -> tuple[int, dict | None]:
    """Google qancha qismini olganini so'raydi: (davom etish joyi, tugagan bo'lsa — fayl ma'lumoti)."""
    r = await c.put(url, content=b"", headers={**auth, "Content-Range": f"bytes */{total}"})
    if r.status_code in (200, 201):
        return total, r.json()
    if r.status_code == 308:
        return _range_end(r), None
    raise DriveError(r.status_code, _reason(r))


async def _put_file(c: httpx.AsyncClient, url: str, path: str, mime: str, auth: dict) -> dict:
    """Diskdagi faylni 8 MB lik bo'laklarda yuklaydi (xotiraga to'liq o'qimaydi). Internet uzilsa yoki
    Google 5xx qaytarsa — kutib, Google olgan joydan davom etadi (boshidan emas)."""
    total = os.path.getsize(path)
    if total == 0:
        r = await c.put(url, content=b"", headers={**auth, "Content-Type": mime})
        if r.status_code not in (200, 201):
            raise DriveError(r.status_code, _reason(r))
        return r.json()
    offset, failures = 0, 0
    with open(path, "rb") as f:
        while True:
            f.seek(offset)
            chunk = f.read(CHUNK)
            head = {**auth, "Content-Type": mime,
                    "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{total}"}
            try:
                r = await c.put(url, content=chunk, headers=head)
            except httpx.TransportError as e:
                r, problem = None, type(e).__name__
            else:
                problem = f"{r.status_code}" if r.status_code >= 500 or r.status_code == 429 else ""
            if r is not None and r.status_code in (200, 201):
                return r.json()
            if r is not None and r.status_code == 308:
                offset, failures = _range_end(r), 0
                continue
            if not problem:  # 4xx — qayta urinish foyda bermaydi
                raise DriveError(r.status_code, _reason(r))
            failures += 1
            if failures > MAX_RETRIES:
                raise DriveError(r.status_code if r is not None else 0, f"yuklash uzildi ({problem})")
            log.info("Drive'ga yuklash uzildi (%s) — %d-urinish, davom ettiriladi", problem, failures)
            await asyncio.sleep(min(2 ** failures, 60))
            try:
                offset, done = await _resume_point(c, url, total, auth)
            except httpx.TransportError:
                continue  # hali ham internet yo'q — keyingi urinishda yana so'raladi
            if done is not None:
                return done


async def upload(name: str, data: bytes | None = None, mime: str = "text/html", file_id: str | None = None,
                 parent: str | None = None, path: str | None = None) -> dict:
    """Faylni yaratadi yoki (file_id bo'lsa) yangilaydi. {"id", "webViewLink"} qaytaradi.
    data — baytlar yoki path — diskdagi fayl (katta video uchun). parent — papka (standart: asosiy papka).
    Resumable yuklash — katta fayllar uchun ham ishlaydi."""
    async with _client() as c:
        tok = await _access_token(c)
        auth = {"Authorization": f"Bearer {tok}"}
        params = {"uploadType": "resumable", "fields": FIELDS, "supportsAllDrives": "true"}
        head = {**auth, "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime}
        r = None
        if file_id:
            r = await c.patch(f"{UPLOAD}/files/{file_id}", params=params, headers=head,
                              content=json.dumps({"name": name}))
            if r.status_code == 404:  # Drive'da o'chirib yuborilgan — yangisini yaratamiz
                log.info("Drive fayli topilmadi — qayta yaratiladi")
                r = None
        if r is None:
            meta = {"name": name, "parents": [parent or config.GOOGLE_DRIVE_ROOT_FOLDER_ID], "mimeType": mime}
            r = await c.post(f"{UPLOAD}/files", params=params, headers=head, content=json.dumps(meta))
        if r.status_code != 200 or "location" not in r.headers:
            raise DriveError(r.status_code, _reason(r))
        if path is not None:
            return await _put_file(c, r.headers["location"], path, mime, auth)
        put = await c.put(r.headers["location"], content=data or b"", headers={**auth, "Content-Type": mime})
        if put.status_code not in (200, 201):
            raise DriveError(put.status_code, _reason(put))
        return put.json()


def _q(value: str) -> str:
    """Drive qidiruv so'rovi uchun qo'shtirnoqlarni qochirish."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def ensure_folder(name: str, parent: str | None = None) -> str:
    """Papkani topadi (bo'lmasa yaratadi) va id sini qaytaradi."""
    parent = parent or config.GOOGLE_DRIVE_ROOT_FOLDER_ID
    async with _client() as c:
        auth = {"Authorization": f"Bearer {await _access_token(c)}"}
        q = (f"name = '{_q(name)}' and '{_q(parent)}' in parents and "
             "mimeType = 'application/vnd.google-apps.folder' and trashed = false")
        r = await c.get(f"{API}/files", headers=auth, params={
            "q": q, "fields": "files(id)", "pageSize": "1",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
        if r.status_code != 200:
            raise DriveError(r.status_code, _reason(r))
        found = r.json().get("files") or []
        if found:
            return found[0]["id"]
        r = await c.post(f"{API}/files", headers=auth, params={"fields": "id", "supportsAllDrives": "true"},
                         json={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]})
        if r.status_code != 200:
            raise DriveError(r.status_code, _reason(r))
        return r.json()["id"]
