from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from xiaoke_bot.member_analysis import MemberProfileStore, parse_member_analysis


class MemberAnalysisParserTests(unittest.TestCase):
    def test_parses_fenced_json_and_bounds_delta(self) -> None:
        result = parse_member_analysis(
            """```json
            {
              "profile_summary": "喜欢轻松聊天",
              "personality_traits": ["外向", "幽默"],
              "interests": ["游戏"],
              "communication_style": "简短直接",
              "interaction_advice": "用轻松口吻回应",
              "favorability_delta": 99,
              "favorability_reason": "经常友好互动"
            }
            ```"""
        )
        self.assertEqual(result["personality_traits"], ["外向", "幽默"])
        self.assertEqual(result["favorability_delta"], 5.0)

    def test_rejects_non_json_response(self) -> None:
        with self.assertRaises(ValueError):
            parse_member_analysis("没有结构化结果")


class MemberProfileStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_analyzes_updates_and_deletes_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemberProfileStore(Path(directory) / "members.db")
            first = await store.record_message(
                group_id=20004,
                user_id=12345,
                display_name="测试成员",
                content="大家好",
                retention=20,
                min_messages=3,
                interval_messages=2,
            )
            self.assertFalse(first)
            await store.record_message(
                group_id=20004,
                user_id=12345,
                display_name="测试成员",
                content="我喜欢玩游戏",
                retention=20,
                min_messages=3,
                interval_messages=2,
            )
            should_analyze = await store.record_message(
                group_id=20004,
                user_id=12345,
                display_name="新群名片",
                content="今天一起开黑吗",
                retention=20,
                min_messages=3,
                interval_messages=2,
            )
            self.assertTrue(should_analyze)

            source = await store.analysis_input(20004, 12345, 20, False)
            self.assertIsNotNone(source)
            assert source is not None
            self.assertEqual(source["display_name"], "新群名片")
            self.assertEqual(len(source["messages"]), 3)

            await store.save_analysis(
                20004,
                12345,
                {
                    "profile_summary": "喜欢游戏和群体活动",
                    "personality_traits": ["活跃"],
                    "interests": ["游戏"],
                    "communication_style": "轻松",
                    "interaction_advice": "可以聊游戏",
                    "favorability_delta": 2.5,
                    "favorability_reason": "互动友好",
                },
                source["max_message_id"],
                True,
            )
            member = await store.get_member(20004, 12345)
            self.assertIsNotNone(member)
            assert member is not None
            self.assertEqual(member["favorability"], 52.5)
            self.assertEqual(member["interests"], ["游戏"])

            updated = await store.update_member(20004, 12345, 88, "重点群友", "小王")
            self.assertEqual(updated["favorability"], 88.0)
            self.assertEqual(updated["admin_note"], "重点群友")
            self.assertEqual(updated["bot_nickname"], "小王")
            self.assertTrue(await store.delete_member(20004, 12345))
            self.assertIsNone(await store.get_member(20004, 12345))

    async def test_bot_nickname_column_added_to_legacy_db(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE member_profiles (group_id INTEGER, user_id INTEGER, display_name TEXT NOT NULL, "
                "message_count INTEGER NOT NULL DEFAULT 0, favorability REAL NOT NULL DEFAULT 50, "
                "profile_summary TEXT NOT NULL DEFAULT '', personality_traits TEXT NOT NULL DEFAULT '[]', "
                "interests TEXT NOT NULL DEFAULT '[]', communication_style TEXT NOT NULL DEFAULT '', "
                "interaction_advice TEXT NOT NULL DEFAULT '', favorability_reason TEXT NOT NULL DEFAULT '', "
                "admin_note TEXT NOT NULL DEFAULT '', analysis_status TEXT NOT NULL DEFAULT 'pending', "
                "analysis_error TEXT NOT NULL DEFAULT '', analyzed_message_count INTEGER NOT NULL DEFAULT 0, "
                "last_analyzed_message_id INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL, "
                "last_seen_at TEXT NOT NULL, last_analyzed_at TEXT, PRIMARY KEY (group_id, user_id))"
            )
            connection.execute(
                "INSERT INTO member_profiles(group_id, user_id, display_name, first_seen_at, last_seen_at) "
                "VALUES (1, 2, '张三', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            connection.commit()
            connection.close()

            store = MemberProfileStore(path)  # opening runs the migration
            member = await store.get_member(1, 2)
            assert member is not None
            self.assertEqual(member["bot_nickname"], "")
            self.assertEqual(member["offense_count"], 0)
            updated = await store.update_member(1, 2, 50, "", "阿三")
            self.assertEqual(updated["bot_nickname"], "阿三")
            self.assertEqual(await store.flag_offense(1, 2), 1)
            reset = await store.update_member(1, 2, 50, "", "阿三", 0)
            self.assertEqual(reset["offense_count"], 0)


if __name__ == "__main__":
    unittest.main()
