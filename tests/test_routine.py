from __future__ import annotations

import tempfile
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_runtime_config import BASE
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.judge import JevClient, JevVerdict, build_questions
from xiaoke_bot.prompts import build_runtime_context_prompt
from xiaoke_bot.routine import (DEFAULT_WEEKDAY_SCHEDULE, DEFAULT_WEEKEND_SCHEDULE, LEGACY_WEEKDAY_SCHEDULE,
    LEGACY_WEEKEND_SCHEDULE, build_routine_prompt, day_plan, parse_schedule, routine_context, routine_snapshot)
from xiaoke_bot.semantic import behavior_enabled, scene_prompt

SETTINGS = replace(BASE, routine_enabled=True, context_timezone="Asia/Shanghai", jev_enabled=True)
MONDAY = datetime(2026, 9, 21, 9, 0)


class RoutineTests(unittest.TestCase):
    def test_same_day_and_reloaded_settings_produce_identical_plans(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = RuntimeConfigStore(path, BASE)
            config.update({"routine_enabled": True, "routine_weekday_schedule": SETTINGS.routine_weekday_schedule})
            first = day_plan(config.snapshot(), MONDAY.date())
            reloaded = RuntimeConfigStore(path, BASE).snapshot()
            self.assertEqual(first, day_plan(reloaded, MONDAY.date()))
            self.assertEqual(first, routine_snapshot(config.snapshot(), MONDAY.replace(hour=22))["slots"])

    def test_daily_choices_vary_but_weekly_classes_stay_consistent(self):
        first = day_plan(SETTINGS, MONDAY.date())
        next_week = day_plan(SETTINGS, MONDAY.date() + timedelta(days=7))
        self.assertEqual([row for row in first if row["kind"] == "上课"][0]["activity"],
                         [row for row in next_week if row["kind"] == "上课"][0]["activity"])
        self.assertNotEqual([row["activity"] for row in first], [row["activity"] for row in next_week])

    def test_weekdays_weekends_and_boundaries_cover_every_minute(self):
        for day in (date(2026, 9, 21), date(2026, 9, 20)):
            plan = day_plan(SETTINGS, day)
            self.assertEqual(plan[0]["start"], "00:00")
            self.assertEqual(plan[-1]["end"], "24:00")
            self.assertTrue(all(a["end"] == b["start"] for a, b in zip(plan, plan[1:])))
            for row in plan:
                moment = datetime.fromisoformat(f"{day.isoformat()}T{row['start']}")
                self.assertEqual(routine_snapshot(SETTINGS, moment)["current"], row)
        self.assertEqual(routine_snapshot(SETTINGS, MONDAY)["current"]["kind"], "上课")
        self.assertEqual(routine_snapshot(replace(SETTINGS, routine_variation="fixed"),
                         datetime(2026, 9, 20, 8, 30))["current"]["kind"], "睡觉")

    def test_local_midnight_rolls_over_without_restart(self):
        before = routine_snapshot(SETTINGS, datetime(2026, 9, 18, 15, 59, tzinfo=timezone.utc))
        after = routine_snapshot(SETTINGS, datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc))
        self.assertEqual((before["date"], before["day_type"]), ("2026-09-18", "工作日"))
        self.assertEqual((after["date"], after["day_type"]), ("2026-09-19", "周末"))
        self.assertEqual(before["next"]["date"], after["date"])
        self.assertEqual(after["previous"]["date"], before["date"])

    def test_tomorrow_preview_has_no_fake_current_activity(self):
        plan = routine_snapshot(SETTINGS, MONDAY, offset=1)
        self.assertEqual(plan["date"], "2026-09-22")
        self.assertIsNone(plan["current"])
        self.assertIsNone(plan["current_index"])

    def test_disabled_routine_adds_no_context_or_jev_questions(self):
        self.assertEqual(build_routine_prompt(BASE, MONDAY), "")
        self.assertIsNone(routine_context(BASE))
        self.assertNotIn("routine_reaction", build_questions(BASE))
        self.assertTrue(behavior_enabled(SETTINGS))

    def test_prompt_uses_the_same_clock_and_distinguishes_plans_from_events(self):
        prompt = build_runtime_context_prompt(SETTINGS, chat_type="group", group_id=100,
            user_id=7, display_name="测试", now=MONDAY)
        self.assertIn("当前时间：09:00:00", prompt)
        self.assertIn("08:15–09:55", prompt)
        self.assertIn("后面的活动只说计划", prompt)
        self.assertIn("不要每条回复都汇报行程", prompt)

    def test_invalid_templates_are_rejected_and_saved_config_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            config = RuntimeConfigStore(Path(folder) / "config.json", BASE)
            for text in ([], "", "07:00 | 休息 | 起床", "00:00 | 睡觉 | a\n00:00 | 娱乐 | b",
                         "00:00 | 睡觉 | a\n25:00 | 娱乐 | b", "00:00 | 睡觉 | a\n08:00 | 未知 | b",
                         "00:00 | 睡觉 | a\n08:00 | 娱乐 | b / "):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    config.update({"routine_weekday_schedule": text})
                self.assertEqual(config.snapshot().routine_weekday_schedule, BASE.routine_weekday_schedule)

    def test_custom_schedule_controls_current_activity(self):
        custom = "00:00 | 睡觉 | 休息\n09:00 | 学习 | 准备考试\n18:00 | 娱乐 | 听歌"
        current = replace(SETTINGS, routine_weekday_schedule=custom, routine_variation="fixed")
        self.assertEqual(routine_snapshot(current, MONDAY)["current"]["activity"], "准备考试")
        self.assertEqual(len(parse_schedule(custom)), 3)

    def test_varied_plans_over_a_year_preserve_time_course_and_event_constraints(self):
        def minutes(stamp):
            hours, mins = map(int, stamp.split(":"))
            return hours * 60 + mins

        themes = {False: set(), True: set()}
        events, timing_patterns, activity_patterns, classes = set(), set(), set(), {}
        for mode in ("natural", "rich"):
            settings = replace(SETTINGS, routine_variation=mode)
            for offset in range(365):
                day = date(2026, 1, 1) + timedelta(days=offset)
                with self.subTest(mode=mode, day=day):
                    snapshot = routine_snapshot(settings, datetime.combine(day, datetime.min.time()))
                    plan = snapshot["slots"]
                    template = parse_schedule(settings.routine_weekend_schedule if day.weekday() >= 5 else settings.routine_weekday_schedule)
                    self.assertEqual((plan[0]["start"], plan[-1]["end"]), ("00:00", "24:00"))
                    self.assertTrue(all(a["end"] == b["start"] for a, b in zip(plan, plan[1:])))
                    self.assertTrue(all(row["start"] < row["end"] for row in plan))
                    self.assertTrue(all(minutes(row["end"]) - minutes(row["start"]) <= 90
                                        for row in plan if row["kind"] == "吃饭"))
                    originals = [row for row in plan if row["base_start"] is not None]
                    self.assertEqual(len(originals), len(template))
                    limit = (30 if day.weekday() >= 5 else 15) * (2 if mode == "rich" else 1)
                    for index, (row, slot) in enumerate(zip(originals, template)):
                        self.assertEqual(minutes(row["base_start"]), slot.minute)
                        self.assertLessEqual(abs(minutes(row["start"]) - slot.minute), limit)
                        if slot.kind == "上课":
                            self.assertEqual(minutes(row["start"]), slot.minute)
                            self.assertEqual(minutes(row["end"]), template[index + 1].minute)
                            key = (day.weekday(), slot.minute)
                            self.assertEqual(row["activity"], classes.setdefault(key, row["activity"]))
                    self.assertLessEqual(len(snapshot["events"]), 3 if mode == "rich" else 1)
                    for index, row in enumerate(plan):
                        if row["is_event"]:
                            duration = minutes(row["end"]) - minutes(row["start"])
                            self.assertTrue(10 <= duration <= 25)
                            self.assertTrue("09:00" <= row["start"] < row["end"] <= "22:00")
                            before, after = plan[index - 1], plan[index + 1]
                            self.assertEqual(before["activity"], after["activity"])
                            self.assertIn("宿舍", before["activity"])
                            self.assertIn(before["kind"], {"休息", "娱乐"})
                            for neighbor in (before, after):
                                self.assertGreaterEqual(minutes(neighbor["end"]) - minutes(neighbor["start"]), 15)
                            events.add(row["activity"])
                    themes[day.weekday() >= 5].add(snapshot["theme"])
                    timing_patterns.add(tuple(row["start"] for row in originals))
                    activity_patterns.add(tuple(row["kind"] for row in originals))
        self.assertEqual([len(themes[False]), len(themes[True])], [5, 6])
        self.assertGreaterEqual(len(events), 10)
        self.assertGreater(len(timing_patterns), 100)
        self.assertGreater(len(activity_patterns), 6)

    def test_fixed_mode_keeps_template_times_and_has_no_extra_events(self):
        settings = replace(SETTINGS, routine_variation="fixed")
        for offset in range(14):
            day = MONDAY.date() + timedelta(days=offset)
            plan = day_plan(settings, day)
            slots = parse_schedule(settings.routine_weekend_schedule if day.weekday() >= 5 else settings.routine_weekday_schedule)
            self.assertEqual(len(plan), len(slots))
            for row, slot in zip(plan, slots):
                self.assertEqual(row["start"], row["base_start"])
                self.assertEqual(row["kind"], slot.kind)
                self.assertIn(row["activity"], slot.choices)
                self.assertFalse(row["is_event"])

    def test_dense_custom_schedules_keep_order_courses_and_custom_activities(self):
        custom = ("00:00 | 睡觉 | 睡觉\n07:20 | 休息 | 起床\n07:21 | 吃饭 | 自定义早餐\n"
                  "08:15 | 上课 | 自定义课程\n08:16 | 上课 | 下一门课\n08:18 | 学习 | 准备考试\n"
                  "08:19 | 学习 | 自定义练习\n18:00 | 娱乐 | 自定义爱好 / 另一个爱好\n23:59 | 睡觉 | 睡觉")
        template = parse_schedule(custom)
        settings = replace(SETTINGS, routine_weekday_schedule=custom, routine_weekend_schedule=custom)
        for offset in range(60):
            day = MONDAY.date() + timedelta(days=offset)
            plan = day_plan(settings, day)
            self.assertEqual(len(plan), len(template))
            for row, slot in zip(plan, template):
                self.assertIn(row["activity"], slot.choices)
                self.assertEqual(row["kind"], slot.kind)
                self.assertLess(row["start"], row["end"])
                self.assertFalse(row["is_event"])
            courses = [row for row in plan if row["kind"] == "上课"]
            self.assertEqual([(row["start"], row["end"]) for row in courses], [("08:15", "08:16"), ("08:16", "08:18")])
            self.assertTrue(all(a["end"] == b["start"] for a, b in zip(plan, plan[1:])))
        self.assertEqual(routine_snapshot(settings, MONDAY)["theme"], "自定义日常")

    def test_only_legacy_default_templates_are_upgraded_and_variation_is_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"routine_weekday_schedule": LEGACY_WEEKDAY_SCHEDULE,
                "routine_weekend_schedule": LEGACY_WEEKEND_SCHEDULE}), encoding="utf-8")
            config = RuntimeConfigStore(path, BASE)
            self.assertEqual(config.snapshot().routine_weekday_schedule, DEFAULT_WEEKDAY_SCHEDULE)
            self.assertEqual(config.public_dict()["routine_weekend_schedule"], DEFAULT_WEEKEND_SCHEDULE)
            custom = LEGACY_WEEKDAY_SCHEDULE.replace("鸡腿饭", "蔬菜饭")
            config.update({"routine_weekday_schedule": custom, "routine_variation": "natural"})
            reloaded = RuntimeConfigStore(path, BASE).snapshot()
            self.assertEqual(reloaded.routine_weekday_schedule, custom)
            self.assertEqual(reloaded.routine_variation, "natural")
            for invalid in ("invalid", "", None, []):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    config.update({"routine_variation": invalid})
            self.assertEqual(config.snapshot().routine_variation, "natural")
            # Saving an old default from a tab left open during the upgrade is safe too.
            config.update({"routine_weekend_schedule": LEGACY_WEEKEND_SCHEDULE})
            self.assertEqual(config.snapshot().routine_weekend_schedule, DEFAULT_WEEKEND_SCHEDULE)


class RoutineJudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_breakfast_event_requires_valid_suitability_score_at_configured_threshold(self):
        from xiaoke_bot.routine_diary import RoutineDiary
        schedule = "00:00 | 睡觉 | 宿舍睡觉\n07:00 | 吃饭 | 食堂吃早餐\n09:00 | 娱乐 | 在宿舍听歌\n23:00 | 睡觉 | 睡觉"
        settings = replace(SETTINGS, routine_photo_enabled=True, routine_variation="fixed", routine_photo_threshold=0.8,
            routine_weekday_schedule=schedule, routine_weekend_schedule=schedule)
        moment = datetime(2026, 9, 21, 10)
        events = RoutineDiary().observe(settings, moment)
        breakfast = next(row for row in events if row["kind"] == "吃饭")
        client, sdk = JevClient(), AsyncMock()
        for score, expected in ((0.79, "none"), (0.8, "dining"), (1, "dining"), (None, "none"), (float("nan"), "none"), (float("inf"), "none")):
            answers = {"addressed": SimpleNamespace(noul=1), "worth": SimpleNamespace(noul=1),
                "offensive": SimpleNamespace(noul=0), "intrusion": SimpleNamespace(score=0, confidence=1),
                "routine_photo_event": SimpleNamespace(choice=breakfast["event_id"], confidence=0.99)}
            if score is not None:
                answers["routine_photo_suitable"] = SimpleNamespace(noul=score)
            sdk.system_one.return_value = SimpleNamespace(answers=answers)
            with patch.object(client, "_ensure", AsyncMock(return_value=sdk)), patch("xiaoke_bot.routine.local_now", return_value=moment):
                verdict = await client.judge(current_text="小可今早吃什么", current_speaker="测试", recent=[], settings=settings, routine_events=events)
            self.assertEqual(verdict.routine_photo, expected)

    async def test_diary_event_and_photo_reference_reach_jev_and_only_valid_ids_are_accepted(self):
        from test_routine_diary import DIARY_SETTINGS, EVENING
        from xiaoke_bot.routine_diary import RoutineDiary
        from xiaoke_bot.judge import photo_event_choices, recent_from_history
        events = RoutineDiary().observe(DIARY_SETTINGS, EVENING)
        dinner = next(row for row in events if row["label"] == "晚饭")
        recent = recent_from_history([{"role": "assistant", "content": "今天吃了盖饭", "photo": {
            "activity": dinner["details"], "event_id": dinner["event_id"]}}], bot_name=DIARY_SETTINGS.bot_name, limit=2)
        client, sdk = JevClient(), AsyncMock()
        for selection, confidence, expected in ((dinner["event_id"], 0.99, dinner["event_id"]),
                                                ("unknown", 1, "none"), (dinner["event_id"], 0.2, "none")):
            sdk.system_one.return_value = SimpleNamespace(answers={
                "addressed": SimpleNamespace(noul=1), "worth": SimpleNamespace(noul=1),
                "offensive": SimpleNamespace(noul=0), "intrusion": SimpleNamespace(score=0, confidence=1),
                "routine_photo": SimpleNamespace(choice="none" if expected != "none" else "dining", confidence=0.99),
                "routine_photo_event": SimpleNamespace(choice=selection, confidence=confidence),
                "routine_photo_suitable": SimpleNamespace(noul=0.98),
            })
            with patch.object(client, "_ensure", AsyncMock(return_value=sdk)):
                verdict = await client.judge(current_text="刚才那张再发一次", current_speaker="测试", recent=recent,
                    settings=DIARY_SETTINGS, routine_events=events)
            self.assertEqual(verdict.routine_photo_event, expected)
            self.assertEqual(verdict.routine_photo, "dining" if expected == dinner["event_id"] else "none")
        state = sdk.system_one.call_args.kwargs["state"]
        self.assertEqual(state["bot_diary"], events)
        self.assertEqual(state["recent_messages"][0]["photo"]["event_id"], dinner["event_id"])
        choices = photo_event_choices(events)
        self.assertIn(dinner["event_id"], choices)
        for event in events:
            if "宿舍" in event["activity"] or event["kind"] == "睡觉":
                self.assertNotIn(event["event_id"], choices)

    async def test_daily_context_and_reaction_are_batched_in_existing_jev_request(self):
        client = JevClient()
        sdk = AsyncMock()
        sdk.system_one.return_value = SimpleNamespace(answers={
            "addressed": SimpleNamespace(noul=1), "worth": SimpleNamespace(noul=1),
            "offensive": SimpleNamespace(noul=0), "intrusion": SimpleNamespace(score=0, confidence=1),
            "routine_reaction": SimpleNamespace(choice="share", confidence=0.95),
        })
        with patch.object(client, "_ensure", AsyncMock(return_value=sdk)):
            verdict = await client.judge(current_text="小可在干嘛？", current_speaker="测试", recent=[], settings=SETTINGS)
        self.assertEqual(verdict.routine_reaction, "share")
        sdk.system_one.assert_awaited_once()
        request = sdk.system_one.call_args.kwargs
        self.assertIn("routine_reaction", request["questions"])
        self.assertIn("current", request["state"]["bot_routine"])
        self.assertIn("theme", request["state"]["bot_routine"])
        self.assertIn("focus", request["state"]["bot_routine"])
        self.assertIn("events", request["state"]["bot_routine"])
        self.assertNotIn("slots", request["state"]["bot_routine"])
        self.assertIn("当前活动", scene_prompt(verdict, SETTINGS))

    async def test_serious_help_overrides_idle_or_busy_routine_even_without_scene_feature(self):
        verdict = JevVerdict(1, 1, 0, 0, 1, routine_reaction="focus")
        prompt = scene_prompt(verdict, replace(SETTINGS, jev_scene_enabled=False))
        self.assertIn("专心回应当前问题", prompt)
        self.assertIn("不要用上课、作业或困倦拒绝帮助", prompt)


if __name__ == "__main__":
    unittest.main()
