from __future__ import annotations

import os
import unittest

from xiaoke_bot.config import get_settings
from xiaoke_bot.permissions import (
    Command,
    can_manage,
    contains_trigger_keyword,
    is_allowed_context,
    parse_command,
    strip_bot_name,
)


class PermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["BOT_SUPERUSERS"] = "10001"
        os.environ["BOT_ALLOWED_GROUPS"] = "20004"
        get_settings.cache_clear()
        cls.settings = get_settings()

    def test_group_whitelist(self) -> None:
        self.assertTrue(
            is_allowed_context(user_id=123, group_id=20004, settings=self.settings)
        )
        self.assertFalse(
            is_allowed_context(user_id=123, group_id=111, settings=self.settings)
        )
        self.assertFalse(
            is_allowed_context(user_id=10001, group_id=111, settings=self.settings)
        )

    def test_private_is_superuser_only(self) -> None:
        self.assertTrue(
            is_allowed_context(user_id=10001, group_id=None, settings=self.settings)
        )
        self.assertFalse(is_allowed_context(user_id=123, group_id=None, settings=self.settings))

    def test_group_admin_can_manage(self) -> None:
        self.assertTrue(can_manage(user_id=123, sender_role="admin", settings=self.settings))
        self.assertFalse(can_manage(user_id=123, sender_role="member", settings=self.settings))

    def test_command_parser(self) -> None:
        parsed = parse_command("/小可 记忆 喜欢什么")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.command, Command.MEMORY)
        self.assertEqual(parsed.argument, "喜欢什么")

    def test_name_only_message_has_no_query(self) -> None:
        self.assertEqual(strip_bot_name("小可", "小可"), "")
        self.assertEqual(strip_bot_name(" 小可：你好 ", "小可"), "你好")

    def test_keyword_can_trigger_anywhere_in_message(self) -> None:
        self.assertTrue(contains_trigger_keyword("今天小可在吗", ("小可",)))
        self.assertTrue(contains_trigger_keyword("今天 Ｘ I A O K E 在吗", ("xiaoke",)))
        self.assertFalse(contains_trigger_keyword("今天有人在吗", ("小可",)))


if __name__ == "__main__":
    unittest.main()
