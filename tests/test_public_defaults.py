from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from xiaoke_bot.config import get_settings
from xiaoke_bot.permissions import is_allowed_context


class PublicDefaultsTests(unittest.TestCase):
    def tearDown(self):
        get_settings.cache_clear()

    def check_closed_permissions(self):
        get_settings.cache_clear()
        settings = get_settings()
        self.assertFalse(settings.superusers)
        self.assertFalse(settings.allowed_groups)
        self.assertFalse(settings.join_gate_enabled)
        self.assertEqual(settings.join_gate_group, 0)
        self.assertEqual(settings.join_gate_api_url, "")
        self.assertFalse(is_allowed_context(user_id=10001, group_id=None, settings=settings))
        self.assertFalse(is_allowed_context(user_id=10001, group_id=20001, settings=settings))

    def test_missing_local_config_grants_no_accounts_access(self):
        with patch.dict(os.environ, {}, clear=True):
            self.check_closed_permissions()

    def test_unedited_example_has_no_credentials_or_preapproved_accounts(self):
        example = Path(__file__).resolve().parents[1] / ".env.example"
        values = {key: value or "" for key, value in dotenv_values(example).items()}
        for key, value in values.items():
            if key.endswith(("_API_KEY", "_TOKEN")):
                self.assertEqual(value, "", key)
        with patch.dict(os.environ, values, clear=True):
            self.check_closed_permissions()
