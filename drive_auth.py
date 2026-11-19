"""Google Drive uchun GOOGLE_OAUTH_REFRESH_TOKEN olish (bir marta, kompyuterda).

    python drive_auth.py

Brauzer ochiladi -> Google akkauntingizni tanlang -> «Ruxsat berish». Token terminalda chiqadi va
(xohlasangiz) .env ga yoziladi. Render'da: Environment -> GOOGLE_OAUTH_REFRESH_TOKEN ga qo'ying.
Token MAXFIY — Drive'ingizga to'liq kirish beradi, hech kimga bermang!

OAuth client turi «Desktop app» bo'lishi kerak. «Web application» bo'lsa — Google Cloud -> Credentials
-> client -> Authorized redirect URIs ga  http://127.0.0.1:8765/  qo'shing.
"""
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

import config
import gdrive

PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"


def _wait_for_code(state: str) -> str:
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state and ("code" in q or "error" in q):
                result.update(q)
                text = "Tayyor! Terminalga qayting." if "code" in q else f"Xato: {q.get('error')}"
            else:
                text = "Kutilmagan so'rov"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{text}</h2>".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=lambda: [server.handle_request() for _ in range(5) if not result],
                              daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if "code" not in result:
        sys.exit(f"Ruxsat olinmadi: {result.get('error', 'vaqt tugadi (5 daqiqa)')}")
    return result["code"]


def _save_to_env(token: str) -> None:
    path = config.BASE_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines()
    key = "GOOGLE_OAUTH_REFRESH_TOKEN"
    lines = [ln for ln in lines if not ln.strip().startswith(key + "=")]
    lines.append(f"{key}={token}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f".env ga yozildi ({key}).")


def main() -> None:
    config.require("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET")
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": gdrive.SCOPE, "access_type": "offline", "prompt": "consent", "state": state})
    cid = config.GOOGLE_OAUTH_CLIENT_ID
    print(f"Client ID: {cid[:12]}…{cid[-30:]}\n"
          f"Redirect URI: {REDIRECT}\n"
          "«redirect_uri_mismatch» chiqsa: Google Console -> Credentials da AYNAN shu Client ID ni oching.\n"
          "  - turi «Desktop app» bo'lsa — hech narsa qo'shish shart emas;\n"
          f"  - turi «Web application» bo'lsa — Authorized redirect URIs ga {REDIRECT} qo'shing (SAVE, 5 daqiqa kuting).\n")
    print("Brauzerda Google ruxsat sahifasi ochiladi. Ochilmasa — shu havolani o'zingiz oching:\n\n" + url + "\n")
    webbrowser.open(url)
    code = _wait_for_code(state)
    r = httpx.post(gdrive.TOKEN_URL, data={
        "code": code, "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET, "redirect_uri": REDIRECT,
        "grant_type": "authorization_code"}, timeout=30)
    body = r.json()
    token = body.get("refresh_token")
    if not token:
        sys.exit(f"Token olinmadi: {body.get('error')} {body.get('error_description', '')}")
    if "--save" in sys.argv:  # python drive_auth.py --save — terminalga chiqarmasdan .env ga yozadi
        _save_to_env(token)
        return
    print("\nGOOGLE_OAUTH_REFRESH_TOKEN (MAXFIY!):\n\n" + token + "\n")
    if input(".env ga yozib qo'yaymi? (ha/yo'q): ").strip().lower() in ("ha", "h", "yes"):
        _save_to_env(token)


if __name__ == "__main__":
    main()
