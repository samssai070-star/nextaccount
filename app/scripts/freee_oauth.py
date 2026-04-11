"""
NextAccount v2 — scripts/freee_oauth.py
freee OAuth 2.0 初回認証スクリプト。

このスクリプトは一度だけ実行する。
実行後、トークンが /tmp/freee_token.json に保存される。

実行方法:
  python scripts/freee_oauth.py
"""

import os
import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.environ["FREEE_CLIENT_ID"]
CLIENT_SECRET = os.environ["FREEE_CLIENT_SECRET"]
REDIRECT_URI  = "http://localhost:8765/callback"
TOKEN_CACHE   = "/tmp/freee_token.json"

AUTH_URL  = "https://accounts.freee.co.jp/public_api/authorize"
TOKEN_URL = "https://accounts.freee.co.jp/public_api/token"

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>freee 認証完了！このウィンドウを閉じてください。</h1>")

    def log_message(self, *args):
        pass


def main():
    url = (
        f"{AUTH_URL}"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
    )
    print(f"ブラウザを開いて認証します: {url}")
    webbrowser.open(url)

    server = HTTPServer(("localhost", 8765), CallbackHandler)
    print("認証コードを待機中...")
    server.handle_request()

    if not auth_code:
        print("❌ 認証コードが取得できませんでした")
        return

    # トークン取得
    resp = requests.post(TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          auth_code,
        "redirect_uri":  REDIRECT_URI,
    })
    resp.raise_for_status()
    token = resp.json()

    import time
    token["created_at"] = time.time()

    with open(TOKEN_CACHE, "w") as f:
        json.dump(token, f, indent=2)

    print(f"✅ トークンを保存しました: {TOKEN_CACHE}")
    print(f"   access_token  : {token['access_token'][:20]}...")
    print(f"   refresh_token : {token['refresh_token'][:20]}...")
    print(f"   expires_in    : {token.get('expires_in')} 秒")


if __name__ == "__main__":
    main()
