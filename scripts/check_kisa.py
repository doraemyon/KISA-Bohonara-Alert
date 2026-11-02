#!/usr/bin/env python3
"""Check KISA's official security-notice RSS feed and send new items to KakaoTalk.

No third-party Python packages are required.  State contains only public KISA item
identifiers; OAuth credentials are read exclusively from GitHub Actions secrets.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


RSS_URL = "https://www.boho.or.kr/kr/rss.do?bbsId=B0000133"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
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
    """Expose an output to the workflow without printing credentials."""
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
        headers={"User-Agent": "kisa-kakao-notice-checker/1.0 (+GitHub Actions)"},
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


def form_post(url: str, fields: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "User-Agent": "kisa-kakao-notice-checker/1.0 (+GitHub Actions)",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        safe_body = error.read().decode("utf-8", errors="replace")[:500]
        fail(f"Kakao API returned HTTP {error.code}: {safe_body}")
    except urllib.error.URLError as error:
        fail(f"Could not reach Kakao API: {error}")

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        fail("Kakao API returned non-JSON data.")
    if not isinstance(result, dict):
        fail("Kakao API returned an unexpected JSON value.")
    return result


def replace_refresh_token(new_refresh_token: str) -> None:
    """Persist a Kakao-rotated token as an Actions secret using a scoped PAT."""
    pat = os.environ.get("GH_SECRETS_PAT", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not pat:
        fail(
            "Kakao rotated KAKAO_REFRESH_TOKEN, but GH_SECRETS_PAT is missing. "
            "Add a fine-grained PAT with this repository's Actions secrets: Read and write."
        )
    if not repository:
        fail("GITHUB_REPOSITORY is unavailable; cannot update KAKAO_REFRESH_TOKEN.")
    if not shutil.which("gh"):
        fail("GitHub CLI (gh) is unavailable; cannot update KAKAO_REFRESH_TOKEN.")

    environment = os.environ.copy()
    environment["GH_TOKEN"] = pat
    result = subprocess.run(
        [
            "gh",
            "secret",
            "set",
            "KAKAO_REFRESH_TOKEN",
            "--repo",
            repository,
            "--body",
            new_refresh_token,
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(
            "Kakao returned a new refresh token, but GitHub could not save it. "
            f"gh exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
    print("Saved Kakao's rotated refresh token to GitHub Actions secrets.")


def get_access_token() -> str:
    result = form_post(
        KAKAO_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": required_env("KAKAO_REST_API_KEY"),
            "client_secret": required_env("KAKAO_CLIENT_SECRET"),
            "refresh_token": required_env("KAKAO_REFRESH_TOKEN"),
        },
    )
    access_token = result.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        fail("Kakao token refresh response did not contain access_token.")

    rotated_token = result.get("refresh_token")
    if isinstance(rotated_token, str) and rotated_token:
        # Kakao invalidates the previous refresh token on rotation, so save this first.
        replace_refresh_token(rotated_token)
    return access_token


def send_text(access_token: str, text: str, link: str, button_title: str) -> None:
    template = {
        "object_type": "text",
        "text": text[:200],
        "link": {"web_url": link, "mobile_web_url": link},
        "button_title": button_title,
    }
    result = form_post(
        KAKAO_SEND_URL,
        {"template_object": json.dumps(template, ensure_ascii=False)},
        {"Authorization": f"Bearer {access_token}"},
    )
    if result.get("result_code") != 0:
        fail(f"Kakao message was rejected: {json.dumps(result, ensure_ascii=False)[:500]}")


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

    # First scheduled run establishes a baseline instead of sending every historical notice.
    if not state["initialized"]:
        state["initialized"] = True
        state["seen_ids"] = [item["id"] for item in items][:MAX_SEEN_IDS]
        state["last_heartbeat"] = date.today().isoformat()
        changed = True
        print(f"Initialized state from {len(items)} current KISA RSS items; no historical notices sent.")
    else:
        known_ids = {str(item_id) for item_id in state["seen_ids"]}
        new_items = [item for item in items if item["id"] not in known_ids]
        new_items.sort(key=published_timestamp)  # Alert in oldest-to-newest order.
        pending_items = new_items[:MAX_NOTICES_PER_RUN]

        if pending_items:
            access_token = get_access_token()
            delivered_ids: list[str] = []
            try:
                for item in pending_items:
                    message = f"[KISA 보안공지]\n{item['title']}"
                    if item["published"]:
                        message += f"\n게시: {item['published']}"
                    send_text(access_token, message, item["link"], "공지 열기")
                    delivered_ids.append(item["id"])
                    print(f"Sent: {item['title']}")
            except Exception:
                # Save IDs that were confirmed as delivered before the error.
                if delivered_ids:
                    state["seen_ids"] = (delivered_ids + state["seen_ids"])[:MAX_SEEN_IDS]
                    state["last_heartbeat"] = date.today().isoformat()
                    persist(state, True)
                raise

            state["seen_ids"] = (delivered_ids + state["seen_ids"])[:MAX_SEEN_IDS]
            state["last_heartbeat"] = date.today().isoformat()
            changed = True
            if len(new_items) > MAX_NOTICES_PER_RUN:
                print(f"{len(new_items) - MAX_NOTICES_PER_RUN} further notice(s) remain for the next run.")
        elif heartbeat_due(str(state["last_heartbeat"])):
            # A public repository's scheduled workflow is disabled after 60 inactive days.
            # This harmless monthly state commit keeps the repository active.
            state["last_heartbeat"] = date.today().isoformat()
            changed = True
            print("Updated monthly GitHub activity heartbeat.")
        else:
            print("No new KISA security notices.")

    if os.environ.get("SEND_TEST", "").lower() == "true":
        access_token = get_access_token()
        send_text(
            access_token,
            "[KISA 보안공지] GitHub Actions와 카카오톡 연결이 정상입니다.",
            "https://www.boho.or.kr/kr/bbs/list.do?bbsId=B0000133&menuNo=205020",
            "보안공지 열기",
        )
        print("Sent KakaoTalk connection-test message.")

    persist(state, changed)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write_output("state_changed", "true" if STATE_PATH.exists() else "false")
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
