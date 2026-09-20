from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xiaoke_bot.daily_summary import (
    SummaryStore,
    estimate_height,
    parse_summary_json,
    render_summary_html,
)

STATS = {"count": 120, "members": 9, "top_speakers": [("小王", 40), ("小李", 22)], "peak_hour": "21点"}


class SummaryParseTests(unittest.TestCase):
    def test_parses_fenced_json_and_bounds(self) -> None:
        data = parse_summary_json(
            """```json
            {"title": "摸鱼大会", "overview": "今天很欢乐",
             "topics": [{"name": "开黑", "detail": "组队打游戏"}],
             "highlights": ["小王连赢五把"],
             "quotes": [{"speaker": "小李", "text": "我裂开了"}],
             "mvp": {"name": "小王", "reason": "话最多"}, "mood": "欢乐"}
            ```"""
        )
        self.assertEqual(data["title"], "摸鱼大会")
        self.assertEqual(len(data["topics"]), 1)
        self.assertEqual(data["mvp"]["name"], "小王")

    def test_caps_list_lengths(self) -> None:
        data = parse_summary_json(
            '{"title":"x","topics":' + str([{"name": f"t{i}", "detail": "d"} for i in range(20)]).replace("'", '"') + "}"
        )
        self.assertLessEqual(len(data["topics"]), 6)

    def test_rejects_non_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_summary_json("这不是 JSON")

    def test_missing_fields_get_defaults(self) -> None:
        data = parse_summary_json('{"overview":"仅有概览"}')
        self.assertEqual(data["title"], "今日群聊总结")
        self.assertEqual(data["topics"], [])
        self.assertEqual(data["timeline"], [])
        self.assertEqual(data["mvp"]["name"], "")

    def test_parses_timeline_with_people(self) -> None:
        data = parse_summary_json(
            '{"title":"x","timeline":[{"time":"20:00-21:00","people":["小王","小李"],"detail":"聊了开黑"}]}'
        )
        self.assertEqual(len(data["timeline"]), 1)
        self.assertEqual(data["timeline"][0]["people"], ["小王", "小李"])
        self.assertEqual(data["timeline"][0]["detail"], "聊了开黑")

    def test_timeline_renders_participants_and_no_emoji(self) -> None:
        data = parse_summary_json(
            '{"title":"x","overview":"y","timeline":[{"time":"20:00","people":["小王"],"detail":"聊天"}]}'
        )
        html = render_summary_html("小可", 1, "2026-07-14", data, STATS)
        self.assertIn("对话脉络", html)
        self.assertIn("@小王", html)
        self.assertIn("<svg", html)
        self.assertFalse(any(e in html for e in ("🔥", "✨", "💬", "🏆", "📊", "🗣️")))


class SummaryRenderTests(unittest.TestCase):
    def test_escapes_html(self) -> None:
        data = parse_summary_json('{"title":"<script>alert(1)</script>","overview":"a & b <tag>"}')
        html = render_summary_html("小可", 123, "2026-07-14", data, STATS)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("a &amp; b", html)

    def test_height_is_bounded(self) -> None:
        data = parse_summary_json('{"title":"x","overview":"y"}')
        height = estimate_height(data, STATS)
        self.assertGreaterEqual(height, 720)
        self.assertLessEqual(height, 4000)


class SummaryStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_stats_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SummaryStore(Path(directory) / "s.db")
            for i in range(5):
                await store.record_message(20004, 100 + (i % 2), f"用户{i % 2}", f"消息{i}")
            msgs = await store.messages_between(20004, "2000-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00")
            self.assertEqual(len(msgs), 5)
            stats = await store.stats_between(20004, "2000-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", "Asia/Shanghai")
            self.assertEqual(stats["count"], 5)
            self.assertEqual(stats["members"], 2)
            self.assertFalse(await store.has_summary(20004, "2026-07-14"))
            await store.save_summary(20004, "2026-07-14", "<html>hi</html>", "")
            self.assertTrue(await store.has_summary(20004, "2026-07-14"))
            latest = await store.get_latest_summary(20004)
            assert latest is not None
            self.assertEqual(latest["html"], "<html>hi</html>")


if __name__ == "__main__":
    unittest.main()
