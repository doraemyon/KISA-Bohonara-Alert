#!/usr/bin/env python3
"""Check KISA's official security-notice RSS feed and push new items to Telegram."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


RSS_URL = "https://www.boho.or.kr/kr/rss.do?bbsId=B0000133"
KISA_BOARD_URL = "https://www.boho.or.kr/kr/bbs/list.do?bbsId=B0000133&menuNo=205020"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
GITHUB_WORKFLOW_DISPATCH_URL = (
    "https://api.github.com/repos/{repository}/actions/workflows/"
    "process-notice.yml/dispatches"
)
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


def notice_id_from_link(link: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    notice_ids = query.get("nttId", [])
    if len(notice_ids) != 1 or not re.fullmatch(r"\d{1,20}", notice_ids[0]):
        fail("KISA notice link has no valid numeric nttId.")
    return notice_ids[0]


class AttachmentRows(HTMLParser):
    """Read attachment flags from the KISA board's notice rows."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.row_id = ""
        self.in_attachment_cell = False
        self.has_attachment = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self.row_id = ""
            self.in_attachment_cell = False
            self.has_attachment = False
        elif tag == "td":
            self.in_attachment_cell = "attach" in (attributes.get("class") or "").split()
        elif tag == "a":
            href = attributes.get("href") or ""
            if href.startswith("/kr/bbs/view.do?"):
                self.row_id = notice_id_from_link(href)
        elif tag == "img" and self.in_attachment_cell and attributes.get("alt") == "첨부파일":
            self.has_attachment = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self.in_attachment_cell = False
        elif tag == "tr" and self.row_id and self.has_attachment:
            self.ids.add(self.row_id)


def fetch_attachment_ids() -> set[str]:
    request = urllib.request.Request(
        KISA_BOARD_URL,
        headers={"User-Agent": "kisa-security-alert/2.1 (+GitHub Actions)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.geturl() != KISA_BOARD_URL:
                return set()
            document = response.read(512_001)
        if len(document) <= 512_000:
            parser = AttachmentRows()
            parser.feed(document.decode("utf-8-sig"))
            return parser.ids
    except (OSError, ValueError):
        # The optional indicator must never prevent the standard alert.
        pass
    return set()


def extension_notice_root(link: str, repository: str, public_base_url: str = "") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        fail("Optional notice extension repository has an invalid format.")

    if public_base_url:
        parsed = urllib.parse.urlsplit(public_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            fail("NOTICE_EXTENSION_PUBLIC_BASE_URL must be a plain HTTPS URL.")
        base_url = public_base_url.rstrip("/")
    else:
        owner, name = repository.split("/", 1)
        base_url = (
            f"https://{owner.lower()}.github.io/"
            f"{urllib.parse.quote(name, safe='._-')}"
        )
    return f"{base_url}/notices/{notice_id_from_link(link)}"


def telegram_keyboard(link: str, include_extension: bool) -> list[list[dict[str, str]]]:
    keyboard = [[{"text": "공지 열기", "url": link}]]
    if not include_extension:
        return keyboard

    repository = os.environ.get("NOTICE_EXTENSION_REPOSITORY", "").strip()
    token = os.environ.get("NOTICE_EXTENSION_TOKEN", "").strip()
    if not repository or not token:
        return keyboard

    try:
        root = extension_notice_root(
            link,
            repository,
            os.environ.get("NOTICE_EXTENSION_PUBLIC_BASE_URL", "").strip(),
        )
    except RuntimeError as error:
        print(f"WARNING: {error} Sending the standard button only.", file=sys.stderr)
        return keyboard

    keyboard.append(
        [
            {"text": "국문 공지", "url": f"{root}/ko/"},
            {"text": "영문 공지", "url": f"{root}/en/"},
        ]
    )
    return keyboard


def send_telegram(text: str, link: str, *, include_extension: bool = False) -> int:
    token = required_env("TELEGRAM_BOT_TOKEN")
    payload = {
        "chat_id": required_env("TELEGRAM_CHAT_ID"),
        "text": text,
        "disable_notification": False,
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {"inline_keyboard": telegram_keyboard(link, include_extension)},
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

    message = result.get("result")
    message_id = message.get("message_id") if isinstance(message, dict) else None
    if not isinstance(message_id, int):
        fail("Telegram accepted the message but returned no numeric message_id.")
    return message_id


def dispatch_extension(item: dict[str, str]) -> None:
    """Best-effort dispatch to an optional notice-processing workflow."""
    repository = os.environ.get("NOTICE_EXTENSION_REPOSITORY", "").strip()
    token = os.environ.get("NOTICE_EXTENSION_TOKEN", "").strip()
    if not repository and not token:
        return
    if not repository or not token:
        print(
            "WARNING: Optional notice extension is only partly configured; "
            "the standard Telegram alert was still delivered.",
            file=sys.stderr,
        )
        return
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        print(
            "WARNING: Optional notice extension repository has an invalid format; "
            "skipping it.",
            file=sys.stderr,
        )
        return

    payload = {
        "ref": "main",
        "inputs": {
            "notice_url": item["link"],
            "notice_title": item["title"],
        },
    }
    request = urllib.request.Request(
        GITHUB_WORKFLOW_DISPATCH_URL.format(repository=repository),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "kisa-security-alert/2.1 (+GitHub Actions)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 204:
                fail(f"Optional notice extension returned HTTP {response.status}.")
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        # The user already has the fast, standard alert. Do not fail the polling
        # run and resend a duplicate notification on the next minute.
        print(
            f"WARNING: Could not start optional notice extension: {error}. "
            "The standard Telegram alert was still delivered.",
            file=sys.stderr,
        )
        return
    print("Started optional notice extension.")


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
            attachment_ids = fetch_attachment_ids()
            delivered_ids: list[str] = []
            try:
                for item in pending_items:
                    clip = " 📎" if notice_id_from_link(item["link"]) in attachment_ids else ""
                    message = f"[KISA 보안공지]\n{item['title']}{clip}"
                    if item["published"]:
                        message += f"\n게시: {item['published']}"
                    send_telegram(message, item["link"], include_extension=True)
                    dispatch_extension(item)
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

    if os.environ.get("SEND_EXTENSION_TEST", "").lower() == "true":
        sample = {
            "id": "72263",
            "title": "Ivanti 제품 보안 업데이트 권고",
            "link": (
                "https://www.boho.or.kr/kr/bbs/view.do?bbsId=B0000133"
                "&menuNo=205020&nttId=72263"
            ),
            "published": "",
        }
        send_telegram(
            f"🧪 KISA 공지 확장 연결 테스트\n{sample['title']}",
            sample["link"],
            include_extension=True,
        )
        dispatch_extension(sample)
        print("Sent optional notice-extension test message.")

    persist(state, changed)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write_output("state_changed", "true" if STATE_PATH.exists() else "false")
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
