from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_campus_photo import SETTINGS
from xiaoke_bot.routine_diary import RoutineDiary, diary_prompt


SCHEDULE = ("00:00 | 睡觉 | 在宿舍睡觉\n09:00 | 学习 | 在图书馆整理课程笔记\n"
            "12:00 | 吃饭 | 食堂吃午饭\n14:00 | 运动 | 在操场慢跑\n"
            "18:00 | 吃饭 | 食堂吃晚饭\n19:00 | 娱乐 | 在宿舍听歌\n23:00 | 睡觉 | 睡觉")
DIARY_SETTINGS = replace(SETTINGS, routine_weekday_schedule=SCHEDULE, routine_weekend_schedule=SCHEDULE,
                         api_key="test-chat-key")
DINNER = datetime(2026, 9, 21, 18)
EVENING = DINNER.replace(hour=21)


class RoutineDiaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_clock_enriches_each_started_event_once_and_keeps_facts_after_restart(self):
        model = SimpleNamespace(complete=AsyncMock(return_value='{"details":["配了一小碟青菜","饮品是豆浆"]}'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diary.db"
            diary = RoutineDiary(path)
            before = await diary.prepare(DIARY_SETTINGS, model, DINNER.replace(hour=17, minute=59))
            self.assertFalse(any(row["label"] == "晚饭" for row in before))
            model.complete.reset_mock()
            first = await diary.prepare(DIARY_SETTINGS, model, DINNER)
            dinner = next(row for row in first if row["label"] == "晚饭")
            self.assertEqual(dinner["status"], "正在进行")
            self.assertIn("豆浆", dinner["details"])
            self.assertEqual(diary.get(dinner["event_id"])["sealed"], 1)
            again = await asyncio.gather(*(diary.prepare(DIARY_SETTINGS, model, DINNER) for _ in range(3)))
            self.assertTrue(all(rows == first for rows in again))
            model.complete.assert_awaited_once()
            reloaded = RoutineDiary(path)
            await reloaded.prepare(DIARY_SETTINGS, model, DINNER)
            model.complete.assert_awaited_once()
            later = reloaded.observe(DIARY_SETTINGS, EVENING)
            saved = next(row for row in later if row["label"] == "晚饭")
            self.assertEqual(saved["details"], dinner["details"])
            self.assertEqual(saved["status"], "已结束")
            self.assertEqual(json.loads(reloaded.get(saved["event_id"])["snapshot"])["local_time"], "18:00")

    async def test_failed_or_invalid_enrichment_freezes_baseline_instead_of_changing_later(self):
        for output in (RuntimeError("offline"), "invalid JSON", '{"details":[{}]}'):
            with self.subTest(output=output):
                diary = RoutineDiary()
                complete = AsyncMock(side_effect=output) if isinstance(output, Exception) else AsyncMock(return_value=output)
                model = SimpleNamespace(complete=complete)
                initial = await diary.prepare(DIARY_SETTINGS, model, DINNER)
                complete.side_effect = None
                complete.return_value = '{"details":["不该覆盖原内容"]}'
                self.assertEqual(initial, await diary.prepare(DIARY_SETTINGS, model, DINNER))
                complete.assert_awaited_once()
                self.assertNotIn("不该覆盖", diary_prompt(initial))

    async def test_startup_past_records_use_stable_food_and_settings_changes_do_not_rewrite(self):
        diary = RoutineDiary()
        model = SimpleNamespace(complete=AsyncMock(return_value='{"details":["手边放着水杯"]}'))
        first = await diary.prepare(DIARY_SETTINGS, model, EVENING)
        dinner = next(row for row in first if row["label"] == "晚饭")
        self.assertIn("这次吃的是", dinner["details"])
        self.assertNotIn("水杯", dinner["details"])
        model.complete.assert_awaited_once()
        changed = replace(DIARY_SETTINGS, routine_weekday_schedule=SCHEDULE.replace("食堂吃晚饭", "吃披萨"))
        second = await diary.prepare(changed, model, EVENING)
        self.assertEqual(first, second)
        model.complete.assert_awaited_once()

    async def test_disabled_and_read_only_preview_never_create_records_or_call_model(self):
        diary = RoutineDiary()
        model = SimpleNamespace(complete=AsyncMock())
        self.assertEqual(diary.entries(DIARY_SETTINGS, EVENING), [])
        self.assertEqual(await diary.prepare(replace(DIARY_SETTINGS, routine_enabled=False), model, EVENING), [])
        self.assertEqual(diary.entries(DIARY_SETTINGS, EVENING), [])
        model.complete.assert_not_awaited()

    async def test_sleep_and_unconfigured_model_preserve_baseline_without_remote_calls(self):
        diary = RoutineDiary()
        model = SimpleNamespace(complete=AsyncMock())
        sleeping = await diary.prepare(DIARY_SETTINGS, model, DINNER.replace(hour=2))
        self.assertEqual(len(sleeping), 1)
        self.assertEqual(sleeping[0]["kind"], "睡觉")
        await diary.prepare(replace(DIARY_SETTINGS, api_key=""), model, DINNER)
        model.complete.assert_not_awaited()

    def test_new_day_new_identity_and_prompt_does_not_include_future(self):
        diary = RoutineDiary()
        first = diary.observe(DIARY_SETTINGS, DINNER.replace(hour=10))
        second = diary.observe(DIARY_SETTINGS, DINNER.replace(day=22, hour=10))
        self.assertTrue(set(row["event_id"] for row in first).isdisjoint(row["event_id"] for row in second))
        prompt = diary_prompt(first)
        self.assertNotIn("晚饭", prompt)
        self.assertIn("未来安排仍只是计划", prompt)
        self.assertIn("没有记录的细节不要临时编造", prompt)

    def test_inserted_and_resumed_slots_cannot_collide_with_template_clock_times(self):
        from xiaoke_bot.routine import routine_snapshot
        from test_routine import SETTINGS as RANDOM_SETTINGS
        diary = RoutineDiary()
        for offset in range(90):
            now = DINNER.replace(hour=23, minute=59) + timedelta(days=offset)
            plan = routine_snapshot(RANDOM_SETTINGS, now)
            events = diary.observe(RANDOM_SETTINGS, snapshot=plan)
            self.assertEqual(len(events), len(plan["slots"]), f"Lost event on {plan['date']}")
            self.assertEqual([(row["start"], row["end"], row["activity"]) for row in events],
                             [(row["start"], row["end"], row["activity"]) for row in plan["slots"]])
