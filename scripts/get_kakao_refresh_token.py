#!/usr/bin/env python3
"""One-time local OAuth helper for the KISA-to-Kakao GitHub Action.

Register http://localhost:4312/callback as the Kakao Login Redirect URI, then run
this script locally.  It prints a refresh token once; copy it straight into the
KAKAO_REFRESH_TOKEN GitHub Actions secret and do not save it in a file.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


REDIRECT_URI = "http://localhost:4312/callback"
AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} before running this script.")
    return value


class CallbackHandler(BaseHTTPRequestHandler):
    expected_state = ""
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 (required handler name)
        parsed = urllib.parse.urlparse(self.path)
        values = urllib.parse.parse_qs(parsed.query)
        state = values.get("state", [""])[0]
        if parsed.path != "/callback" or state != self.expected_state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write("Invalid OAuth callback.".encode("utf-8"))
            return
        self.result = {key: value[0] for key, value in values.items() if value}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<h1>카카오 인증 완료</h1><p>터미널로 돌아가세요. 이 창은 닫아도 됩니다.</p>".encode(
                "utf-8"
            )
        )

    def log_message(self, format: str, *args: object) -> None:
        # Avoid writing the authorization-code URL to terminal history.
        return


def main() -> None:
    rest_api_key = required_env("KAKAO_REST_API_KEY")
    client_secret = required_env("KAKAO_CLIENT_SECRET")
    oauth_state = secrets.token_urlsafe(32)
    CallbackHandler.expected_state = oauth_state

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": rest_api_key,
            "redirect_uri": REDIRECT_URI,
            "scope": "talk_message",
            "state": oauth_state,
        }
    )
    login_url = f"{AUTH_URL}?{query}"
    print("Opening Kakao login in your browser. Approve the '카카오톡 메시지 전송' consent.")
    print("If it does not open, paste this address into a browser:")
    print(login_url)

    server = HTTPServer(("127.0.0.1", 4312), CallbackHandler)
    server.timeout = 300
    webbrowser.open(login_url)
    server.handle_request()
    callback = CallbackHandler.result
    if "error" in callback:
        raise RuntimeError(f"Kakao authorization failed: {callback['error']}")
    authorization_code = callback.get("code")
    if not authorization_code:
        raise RuntimeError("No authorization code was received within five minutes.")

    request_data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": rest_api_key,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "code": authorization_code,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=request_data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        token_data = json.loads(response.read().decode("utf-8"))
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError("Kakao did not return a refresh token.")

    print("\nCopy this value immediately into the KAKAO_REFRESH_TOKEN GitHub secret:")
    print(refresh_token)
    print("Do not commit it, save it in a file, or paste it into an issue/chat.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
