#!/usr/bin/env python3
"""Check KISA's official security-notice RSS feed and push new items to Telegram."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


RSS_URL = "https://www.boho.or.kr/kr/rss.do?bbsId=B0000133"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
STATE_PATH = Path("data/state.json")
MAX_SEEN_IDS = 500
MAX_NOTICES_PER_RUN = 10
HEARTBEAT_DAYS = 30


def fail(message: str) -> None:
    raise RuntimeError(message)


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"Required secret {name} is empty or missing.")
    return value


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def compact(value: str | None) -> str:
    return " ".join((value or "").split())


def element_text(item: ET.Element, name: str) -> str:
    for child in item:
        if child.tag.rsplit("}", 1)[-1] == name:
            return compact(child.text)
    return ""


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"initialized": False, "last_heartbeat": "", "seen_ids": []}

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot read {STATE_PATH}: {error}")

    if not isinstance(state, dict):
        fail(f"{STATE_PATH} must contain a JSON object.")
    state.setdefault("initialized", False)
    state.setdefault("last_heartbeat", "")
    state.setdefault("seen_ids", [])
    if not isinstance(state["seen_ids"], list):
        fail(f"{STATE_PATH} has an invalid seen_ids value.")
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def fetch_items() -> list[dict[str, str]]:
    request = urllib.request.Request(
        RSS_URL,
        headers={"User-Agent": "kisa-security-alert/2.0 (+GitHub Actions)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            document = response.read()
    except urllib.error.URLError as error:
        fail(f"Could not fetch KISA RSS: {error}")

    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        fail(f"KISA RSS was not valid XML: {error}")

    items: list[dict[str, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "item":
            continue
        title = element_text(element, "title")
        link = element_text(element, "link")
        guid = element_text(element, "guid")
        published = element_text(element, "pubDate")
        item_id = guid or link
        if title and link and item_id:
            items.append(
                {"id": item_id, "title": title, "link": link, "published": published}
            )

    if not items:
        fail("KISA RSS contained no readable notice items; refusing to overwrite state.")
    return items


def published_timestamp(item: dict[str, str]) -> float:
    try:
        return parsedate_to_datetime(item["published"]).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0


def send_telegram(text: str, link: str) -> None:
    token = required_env("TELEGRAM_BOT_TOKEN")
    payload = {
        "chat_id": required_env("TELEGRAM_CHAT_ID"),
        "text": text,
        "disable_notification": False,
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {
            "inline_keyboard": [[{"text": "공지 열기", "url": link}]],
        },
    }
    request = urllib.request.Request(
        TELEGRAM_SEND_URL.format(token=token),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "kisa-security-alert/2.0 (+GitHub Actions)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        safe_body = error.read().decode("utf-8", errors="replace")[:500]
        fail(f"Telegram API returned HTTP {error.code}: {safe_body}")
    except urllib.error.URLError as error:
        fail(f"Could not reach Telegram API: {error}")

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        fail("Telegram API returned non-JSON data.")
    if not isinstance(result, dict) or result.get("ok") is not True:
        fail(f"Telegram message was rejected: {json.dumps(result, ensure_ascii=False)[:500]}")


def heartbeat_due(last_heartbeat: str) -> bool:
    try:
        return (date.today() - date.fromisoformat(last_heartbeat)).days >= HEARTBEAT_DAYS
    except ValueError:
        return True


def persist(state: dict[str, Any], changed: bool) -> None:
    if changed:
        save_state(state)
    write_output("state_changed", "true" if changed else "false")


def main() -> None:
    state = load_state()
    items = fetch_items()
    changed = False

    # The existing state survives the notification-channel migration. A brand-new
    # deployment seeds current items and does not flood the user with old notices.
    if not state["initialized"]:
        state["initialized"] = True
        state["seen_ids"] = [item["id"] for item in items][:MAX_SEEN_IDS]
        state["last_heartbeat"] = date.today().isoformat()
        changed = True
        print(
            f"Initialized state from {len(items)} current KISA RSS items; "
            "no historical notices sent."
        )
    else:
        known_ids = {str(item_id) for item_id in state["seen_ids"]}
        new_items = [item for item in items if item["id"] not in known_ids]
        new_items.sort(key=published_timestamp)
        pending_items = new_items[:MAX_NOTICES_PER_RUN]

        if pending_items:
            delivered_ids: list[str] = []
            try:
                for item in pending_items:
                    message = f"🚨 KISA 보안공지\n{item['title']}"
                    if item["published"]:
                        message += f"\n게시: {item['published']}"
                    send_telegram(message, item["link"])
                    delivered_ids.append(item["id"])
                    print(f"Sent to Telegram: {item['title']}")
            except Exception:
                if delivered_ids:
                    state["seen_ids"] = (delivered_ids + state["seen_ids"])[
                        :MAX_SEEN_IDS
                    ]
                    state["last_heartbeat"] = date.today().isoformat()
                    persist(state, True)
                raise

            state["seen_ids"] = (delivered_ids + state["seen_ids"])[:MAX_SEEN_IDS]
            state["last_heartbeat"] = date.today().isoformat()
            changed = True
            if len(new_items) > MAX_NOTICES_PER_RUN:
                print(
                    f"{len(new_items) - MAX_NOTICES_PER_RUN} further notice(s) "
                    "remain for the next run."
                )
        elif heartbeat_due(str(state["last_heartbeat"])):
            state["last_heartbeat"] = date.today().isoformat()
            changed = True
            print("Updated monthly GitHub activity heartbeat.")
        else:
            print("No new KISA security notices.")

    if os.environ.get("SEND_TEST", "").lower() == "true":
        send_telegram(
            "✅ KISA 보안공지 Telegram 연결이 정상입니다.",
            "https://www.boho.or.kr/kr/bbs/list.do?bbsId=B0000133&menuNo=205020",
        )
        print("Sent Telegram connection-test message.")

    persist(state, changed)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write_output("state_changed", "true" if STATE_PATH.exists() else "false")
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
