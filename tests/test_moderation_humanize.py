from __future__ import annotations

from dataclasses import replace
import unittest

from test_runtime_config import BASE
from xiaoke_bot.humanize import (
    humanize_reply,
    should_probability_reply,
    should_quote_reply,
    split_reply_segments,
    segment_delay,
)
from xiaoke_bot.moderation import find_blocked_keyword


class ModerationAndHumanizeTests(unittest.TestCase):
    def test_segment_cadence_grows_smoothly_and_is_bounded(self):
        settings = replace(BASE, segment_delay_min=0.35, segment_delay_max=0.9)
        values = [segment_delay("字" * n, settings) for n in (1, 10, 30, 60, 120, 1000)]
        self.assertEqual(values, sorted(set(values)))
        self.assertGreater(values[1], 1)
        self.assertGreater(values[3], 4)
        self.assertLessEqual(values[-1], 10)
        self.assertEqual(segment_delay("你 好\n", settings), segment_delay("你好", settings))
        self.assertEqual(segment_delay("字" * 2000, replace(settings, segment_delay_min=8, segment_delay_max=10)), 10)
        self.assertEqual(segment_delay("字" * 2000, replace(settings, segment_delay_min=0, segment_delay_max=0)), 0)
        self.assertEqual(segment_delay("字" * 30, settings), segment_delay("字" * 30, replace(settings, segment_delay_min=0.9, segment_delay_max=0.35)))

    def test_keyword_matching_normalizes_case_width_and_spaces(self) -> None:
        self.assertEqual(find_blocked_keyword("这里有 Ａ B C 信息", ("abc",)), "abc")
        self.assertIsNone(find_blocked_keyword("普通内容", ("abc",)))

    def test_probability_reply_boundary(self) -> None:
        self.assertTrue(should_probability_reply(True, 0.3, sample=0.29))
        self.assertFalse(should_probability_reply(True, 0.3, sample=0.3))
        self.assertFalse(should_probability_reply(False, 1.0, sample=0.0))

    def test_quote_reply_switch_and_probability(self) -> None:
        settings = replace(BASE, quote_reply_enabled=True, quote_reply_probability=0.4)
        self.assertTrue(should_quote_reply(settings, sample=0.39))
        self.assertFalse(should_quote_reply(settings, sample=0.4))
        self.assertFalse(
            should_quote_reply(replace(settings, quote_reply_enabled=False), sample=0.0)
        )

    def test_humanize_preserves_urls_and_code(self) -> None:
        settings = replace(
            BASE,
            humanize_remove_punctuation=True,
            humanize_newline_to_space=True,
        )
        content = "你好，世界！\n访问 https://example.com/a.b\n```python\nprint('x!')\n```"
        result = humanize_reply(content, settings)
        self.assertIn("你好世界 访问", result)
        self.assertIn("https://example.com/a.b", result)
        self.assertIn("print('x!')", result)

    def test_probability_segment_send_respects_spaces_and_limit(self) -> None:
        settings = replace(
            BASE,
            segment_send_enabled=True,
            segment_probability=0.5,
            segment_max_parts=2,
        )
        result = split_reply_segments(
            "北京今天天气 真不错啊 适合散步",
            settings,
            samples=[0.2, 0.1],
        )
        self.assertEqual(result, ["北京今天天气", "真不错啊 适合散步"])

    def test_segment_send_can_keep_one_message(self) -> None:
        settings = replace(BASE, segment_send_enabled=True, segment_probability=0.2)
        self.assertEqual(
            split_reply_segments("北京今天天气 真不错啊", settings, samples=[0.9]),
            ["北京今天天气 真不错啊"],
        )

    def test_segment_send_keeps_kaomoji_together(self) -> None:
        # Even when every boundary wants to split, a 颜文字 with internal spaces stays whole.
        settings = replace(
            BASE,
            segment_send_enabled=True,
            segment_probability=1.0,
            segment_max_parts=6,
        )
        result = split_reply_segments(
            "下午好呀 咋这个点才冒泡 (・ ω ・) 走一个",
            settings,
            samples=[0.0] * 10,
        )
        kaomoji_parts = [part for part in result if "(" in part]
        self.assertEqual(len(kaomoji_parts), 1)
        self.assertIn("(・ ω ・)", kaomoji_parts[0])


if __name__ == "__main__":
    unittest.main()
