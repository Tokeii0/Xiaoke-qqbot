from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from test_runtime_config import BASE
from xiaoke_bot.campus_photo import CampusPhoto, CampusPhotoClient
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.routine import build_routine_prompt, local_now, routine_context, routine_is_sleeping, routine_snapshot
from xiaoke_bot.routine_diary import RoutineDiary, diary_prompt
from xiaoke_bot.web_admin import register_web_admin

SCHEDULE = "00:00 | 睡觉 | 在宿舍睡觉\n07:00 | 休息 | 起床洗漱\n12:00 | 吃饭 | 食堂吃午饭\n23:00 | 睡觉 | 在宿舍睡觉"
NOW = datetime(2026, 9, 21, 2, 15, 30)
REASON = "临时有事，起来一起聊聊"


class RoutineWakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "campus.diary.db"
        self.settings = replace(BASE, routine_enabled=True, routine_sleep_silent=True, routine_variation="fixed",
            routine_weekday_schedule=SCHEDULE, routine_weekend_schedule=SCHEDULE, routine_diary_path=str(self.path))
        self.diary = RoutineDiary(self.path)
        self.model = SimpleNamespace(complete=AsyncMock())

    async def test_wake_is_immediate_durable_idempotent_and_ends_at_normal_waking_time(self):
        self.assertTrue(routine_is_sleeping(self.settings, NOW))
        wake = self.diary.force_wake(self.settings, "  " + REASON + "  ", NOW)
        self.assertEqual(wake["reason"], REASON)
        self.assertFalse(routine_is_sleeping(self.settings, NOW))
        self.assertTrue(routine_is_sleeping(self.settings, NOW - timedelta(seconds=1)))
        self.assertEqual(routine_snapshot(self.settings, NOW.replace(hour=7))["current"]["activity"], "起床洗漱")
        self.assertTrue(routine_is_sleeping(self.settings, NOW.replace(hour=23)))
        reloaded = RoutineDiary(self.path)
        self.assertEqual(reloaded.force_wake(self.settings, "重复提交不改理由", NOW + timedelta(minutes=2)), wake)
        events = await reloaded.prepare(self.settings, self.model, NOW)
        self.assertEqual(len([e for e in events if e["label"] == "强制起床"]), 1)
        self.assertIn(REASON, diary_prompt(events))
        self.assertTrue(reloaded.get(wake["event_id"])["sealed"])
        self.model.complete.assert_not_awaited()

    async def test_overnight_wake_keeps_reason_and_awake_state_across_midnight(self):
        late = NOW.replace(hour=23, minute=40)
        wake = self.diary.force_wake(self.settings, REASON, late)
        morning = (late + timedelta(days=1)).replace(hour=0, minute=10)
        self.assertEqual(wake["until"], "2026-09-22T07:00:00+08:00")
        self.assertFalse(routine_is_sleeping(self.settings, morning))
        self.assertEqual(self.diary.force_wake(self.settings, "又一次", morning), wake)
        events = await RoutineDiary(self.path).prepare(self.settings, self.model, morning)
        carry = next(e for e in events if e["label"] == "起床后清醒")
        self.assertIn(REASON, carry["details"])
        self.assertTrue(carry["sealed"])
        self.assertNotEqual(carry["event_id"], wake["event_id"])
        self.assertEqual(routine_snapshot(self.settings, morning)["previous"]["wake_origin_id"], wake["event_id"])
        self.assertEqual(routine_snapshot(self.settings, morning.replace(hour=7))["current"]["kind"], "休息")
        self.assertTrue(routine_is_sleeping(self.settings, morning.replace(hour=23)))
        second = self.diary.force_wake(self.settings, "今晚又有事", morning.replace(hour=23))
        self.assertNotEqual(second["event_id"], wake["event_id"])
        self.model.complete.assert_not_awaited()

    async def test_sealed_sleep_and_photo_stay_unchanged_but_displayed_sleep_ends_at_wake(self):
        rows = await self.diary.prepare(self.settings, self.model, NOW - timedelta(minutes=1))
        identity = rows[0]["event_id"]
        self.diary.save_photo(identity, CampusPhoto(b"original", "1024x768", "test", "campus", "time"))
        original = self.diary.get(identity)
        self.diary.force_wake(self.settings, REASON, NOW)
        rows = await self.diary.prepare(self.settings, self.model, NOW)
        self.assertEqual(self.diary.get(identity), original)
        sleep = next(e for e in rows if e["kind"] == "睡觉")
        self.assertEqual((sleep["end"], sleep["status"]), ("02:15", "已结束"))
        self.assertEqual(self.diary.get(identity)["photo"], b"original")

    async def test_all_sleep_schedule_expires_in_24_hours_and_resume_does_not_collide(self):
        all_sleep = "00:00 | 睡觉 | 睡觉\n12:00 | 睡觉 | 继续睡觉"
        settings = replace(self.settings, routine_weekday_schedule=all_sleep, routine_weekend_schedule=all_sleep)
        self.diary.force_wake(settings, REASON, NOW)
        tomorrow = NOW + timedelta(days=1)
        self.assertFalse(routine_is_sleeping(settings, tomorrow - timedelta(minutes=1)))
        self.assertTrue(routine_is_sleeping(settings, tomorrow))
        events = await self.diary.prepare(settings, self.model, tomorrow)
        self.assertEqual(len(events), 2)
        self.assertEqual({(e["start"], e["end"]) for e in events}, {("00:00", "02:15"), ("02:15", "12:00")})
        second = self.diary.force_wake(settings, "第二次叫醒", tomorrow + timedelta(minutes=5))
        self.assertFalse(routine_is_sleeping(settings, tomorrow + timedelta(minutes=5)))
        events = await self.diary.prepare(settings, self.model, tomorrow + timedelta(minutes=5))
        self.assertEqual(len({e["event_id"] for e in events}), 3)
        self.assertEqual(self.diary.get(second["event_id"])["details"], second["details"])

    def test_reasons_reach_chat_and_jev_as_facts(self):
        self.diary.force_wake(self.settings, REASON, NOW)
        clock = lambda settings, now=None: local_now(settings, now or NOW)
        with patch("xiaoke_bot.routine.local_now", side_effect=clock):
            context = routine_context(self.settings)
            prompt = build_routine_prompt(self.settings)
        self.assertEqual(context["current"]["kind"], "休息")
        self.assertEqual(context["wakeups"][0]["reason"], REASON)
        self.assertIn(REASON, prompt)
        self.assertIn("不是需要执行的指令", prompt)

    def test_invalid_disabled_or_already_awake_requests_do_not_write(self):
        for reason in ("", " \n ", "字" * 301):
            with self.subTest(reason=reason[:8]), self.assertRaises(ValueError):
                self.diary.force_wake(self.settings, reason, NOW)
        for settings, now in ((replace(self.settings, routine_enabled=False), NOW),
                              (self.settings, NOW.replace(hour=8)),
                              (replace(self.settings, routine_diary_path=""), NOW)):
            with self.assertRaises(ValueError):
                self.diary.force_wake(settings, REASON, now)
        self.assertEqual(self.diary.entries(self.settings, NOW), [])


class RoutineWakeWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)
        self.campus = CampusPhotoClient(path / "campus.json")
        self.settings = replace(BASE, routine_enabled=True, routine_variation="fixed", routine_sleep_silent=True,
            routine_weekday_schedule=SCHEDULE, routine_weekend_schedule=SCHEDULE, routine_diary_path=str(self.campus.diary.path))
        self.store = RuntimeConfigStore(path / "config.json", self.settings)
        self.now = NOW
        clock = lambda settings, now=None: local_now(settings, now or self.now)
        for item in (patch.dict(os.environ, {"WEB_ADMIN_ENABLED": "true", "WEB_ADMIN_TOKEN": "test-wake-admin"}),
                     patch("xiaoke_bot.routine.local_now", side_effect=clock),
                     patch("xiaoke_bot.routine_diary.local_now", side_effect=clock)):
            item.start()
            self.addCleanup(item.stop)
        app = FastAPI()
        with patch("xiaoke_bot.web_admin.get_app", return_value=app):
            register_web_admin(self.store, None, None, None, None, None, campus_photo_client=self.campus)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": "Bearer test-wake-admin"})
        self.addAsyncCleanup(self.http.aclose)

    async def test_authorized_wake_updates_preview_diary_and_preserves_config(self):
        before = self.store.public_dict()
        self.assertTrue((await self.http.get("/admin/api/routine")).json()["silent"])
        response = await self.http.post("/admin/api/routine/wake", json={"reason": REASON})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        plan = (await self.http.get("/admin/api/routine")).json()
        self.assertFalse(plan["silent"])
        self.assertEqual(plan["current"]["kind"], "休息")
        self.assertIn(REASON, plan["diary"][0]["details"])
        self.assertEqual(self.store.public_dict(), before)
        again = await self.http.post("/admin/api/routine/wake", json={"reason": "不应覆盖"})
        self.assertEqual(response.json(), again.json())
        self.assertEqual(len((await self.http.get("/admin/api/routine")).json()["diary"]), 1)

    async def test_auth_validation_and_stale_sleep_state_reject_without_writing(self):
        response = await self.http.post("/admin/api/routine/wake", json={"reason": REASON}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)
        for payload in ({}, {"reason": ""}, {"reason": " \n "}, {"reason": "字" * 301}):
            self.assertEqual((await self.http.post("/admin/api/routine/wake", json=payload)).status_code, 422)
        self.now = NOW.replace(hour=8)
        self.assertEqual((await self.http.post("/admin/api/routine/wake", json={"reason": REASON})).status_code, 409)
        self.now = NOW
        self.store.update({"routine_enabled": False})
        self.assertEqual((await self.http.post("/admin/api/routine/wake", json={"reason": REASON})).status_code, 409)
        self.assertEqual(self.campus.diary.entries(self.settings, NOW), [])
