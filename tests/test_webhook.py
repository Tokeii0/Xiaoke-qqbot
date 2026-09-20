from __future__ import annotations

import json
import unittest

from xiaoke_bot.webhook import build_webhook_message


class WebhookMessageTests(unittest.TestCase):
    def test_auto_extracts_common_field(self) -> None:
        body = json.dumps({"text": "服务器告警"}).encode()
        self.assertEqual(build_webhook_message(body, "application/json", "", "", 500), "服务器告警")

    def test_template_with_nested_field_and_prefix(self) -> None:
        body = json.dumps({"level": "P1", "alert": {"name": "磁盘满"}}).encode()
        msg = build_webhook_message(body, "application/json", "[{level}] {alert.name}", "【监控】", 500)
        self.assertEqual(msg, "【监控】\n[P1] 磁盘满")

    def test_body_placeholder_for_non_json(self) -> None:
        msg = build_webhook_message("hello world", "text/plain", "收到：{_body}", "", 500)
        self.assertEqual(msg, "收到：hello world")

    def test_raw_text_fallback(self) -> None:
        self.assertEqual(build_webhook_message(b"plain alert", "text/plain", "", "", 500), "plain alert")

    def test_json_without_known_field_uses_compact_json(self) -> None:
        body = json.dumps({"foo": "bar"}).encode()
        self.assertEqual(
            build_webhook_message(body, "application/json", "", "", 500), '{"foo": "bar"}'
        )

    def test_unknown_placeholder_becomes_empty(self) -> None:
        body = json.dumps({"a": "1"}).encode()
        self.assertEqual(build_webhook_message(body, "application/json", "{a}{missing}", "", 500), "1")

    def test_truncates_to_max_len(self) -> None:
        self.assertEqual(build_webhook_message(b"x" * 50, "", "", "", 10), "x" * 10)


if __name__ == "__main__":
    unittest.main()
