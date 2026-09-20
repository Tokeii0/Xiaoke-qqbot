from __future__ import annotations

import unittest

from xiaoke_bot.offense import build_offense_prompt, detect_offense


class OffenseTests(unittest.TestCase):
    def test_build_offense_prompt_includes_criteria_and_marker(self) -> None:
        prompt = build_offense_prompt("辱骂机器人")
        self.assertIn("辱骂机器人", prompt)
        self.assertIn("[[OFFENSE]]", prompt)
        # empty criteria still yields a usable instruction with the marker
        self.assertIn("[[OFFENSE]]", build_offense_prompt(""))

    def test_detect_offense_absent(self) -> None:
        offended, cleaned = detect_offense("正常回复")
        self.assertFalse(offended)
        self.assertEqual(cleaned, "正常回复")

    def test_detect_offense_present_and_stripped(self) -> None:
        offended, cleaned = detect_offense("你这样说不太好\n[[OFFENSE]]")
        self.assertTrue(offended)
        self.assertEqual(cleaned, "你这样说不太好")
        self.assertNotIn("OFFENSE", cleaned.upper())

    def test_detect_offense_case_insensitive(self) -> None:
        offended, cleaned = detect_offense("回复内容 [[offense]]")
        self.assertTrue(offended)
        self.assertNotIn("offense", cleaned.lower())


if __name__ == "__main__":
    unittest.main()
