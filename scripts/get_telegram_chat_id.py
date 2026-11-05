#!/usr/bin/env python3
"""Print the private Telegram chat ID after the user sends the bot /start."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN before running this script.")

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        headers={"User-Agent": "kisa-security-alert/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Telegram returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach Telegram: {error}") from error

    if data.get("ok") is not True:
        raise RuntimeError(
            f"Telegram rejected getUpdates: {data.get('description', 'unknown error')}"
        )

    chat_ids: list[str] = []
    for update in data.get("result", []):
        message = update.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat.get("type") == "private" and chat_id is not None:
            chat_ids.append(str(chat_id))

    unique_ids = list(dict.fromkeys(chat_ids))
    if not unique_ids:
        raise RuntimeError(
            "No private chat found. Open your bot in Telegram, send /start, and run again."
        )

    print("Use this value for the TELEGRAM_CHAT_ID GitHub secret:")
    print(unique_ids[-1])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
