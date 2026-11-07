from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_kisa  # noqa: E402


NOTICE_URL = (
    "https://www.boho.or.kr/kr/bbs/view.do?bbsId=B0000133"
    "&menuNo=205020&nttId=72289"
)


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class CheckKisaTests(unittest.TestCase):
    def test_extension_page_root_uses_default_github_pages_url(self) -> None:
        root = check_kisa.extension_notice_root(
            NOTICE_URL, "doraemyon/KISA-Bohonara-Support"
        )

        self.assertEqual(
            root,
            "https://doraemyon.github.io/KISA-Bohonara-Support/notices/72289",
        )

    def test_initial_keyboard_contains_language_buttons_when_extension_is_configured(self) -> None:
        environment = {
            "NOTICE_EXTENSION_REPOSITORY": "doraemyon/KISA-Bohonara-Support",
            "NOTICE_EXTENSION_TOKEN": "test-token",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            keyboard = check_kisa.telegram_keyboard(NOTICE_URL, True)

        self.assertEqual([button["text"] for button in keyboard[0]], ["공지 열기"])
        self.assertEqual(
            [button["text"] for button in keyboard[1]], ["국문 공지", "영문 공지"]
        )
        self.assertTrue(keyboard[1][0]["url"].endswith("/notices/72289/ko/"))
        self.assertTrue(keyboard[1][1]["url"].endswith("/notices/72289/en/"))

    def test_standard_keyboard_is_kept_when_extension_is_not_configured(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            keyboard = check_kisa.telegram_keyboard(NOTICE_URL, True)

        self.assertEqual(keyboard, [[{"text": "공지 열기", "url": NOTICE_URL}]])

    def test_extension_dispatch_no_longer_sends_a_telegram_message_id(self) -> None:
        observed_request = None

        def fake_urlopen(request, timeout=0):
            nonlocal observed_request
            observed_request = request
            self.assertEqual(timeout, 20)
            return FakeResponse()

        environment = {
            "NOTICE_EXTENSION_REPOSITORY": "doraemyon/KISA-Bohonara-Support",
            "NOTICE_EXTENSION_TOKEN": "test-token",
        }
        item = {"link": NOTICE_URL, "title": "제품 보안 업데이트 권고"}
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(
                check_kisa.urllib.request, "urlopen", side_effect=fake_urlopen
            ),
        ):
            check_kisa.dispatch_extension(item)

        payload = json.loads(observed_request.data.decode("utf-8"))
        self.assertEqual(payload["inputs"]["notice_url"], NOTICE_URL)
        self.assertNotIn("telegram_message_id", payload["inputs"])


if __name__ == "__main__":
    unittest.main()
