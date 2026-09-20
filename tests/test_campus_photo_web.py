from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from test_runtime_config import BASE
from xiaoke_bot.campus_photo import CampusPhoto, CampusPhotoClient
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.routine_diary import RoutineDiary
from xiaoke_bot.web_admin import register_web_admin


class CampusPhotoWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = RuntimeConfigStore(Path(self.folder.name) / "config.json", BASE)
        self.store.set_routine_photo_api_key("saved-photo-secret")
        self.client = AsyncMock(spec=CampusPhotoClient)
        self.client.diary = RoutineDiary()
        self.client.generate.return_value = CampusPhoto(b"jpeg", "1024x768", "在校园小路散步", "campus", "2026-09-21 10:00")
        environment = patch.dict(os.environ, {"WEB_ADMIN_ENABLED": "true", "WEB_ADMIN_TOKEN": "test-admin"})
        environment.start()
        self.addCleanup(environment.stop)
        app = FastAPI()
        with patch("xiaoke_bot.web_admin.get_app", return_value=app):
            register_web_admin(self.store, None, None, None, None, None, campus_photo_client=self.client)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": "Bearer test-admin"})
        self.addAsyncCleanup(self.http.aclose)

    async def test_preview_auth_unsaved_options_and_secret_isolation(self):
        self.assertEqual((await self.http.post("/admin/api/routine/photo/preview", json={},
            headers={"Authorization": "Bearer wrong"})).status_code, 401)
        self.client.generate.assert_not_awaited()
        response = await self.http.post("/admin/api/routine/photo/preview", json={
            "routine_photo_api_key": "preview-only-secret", "routine_photo_ratio": "3:4", "scene": "dining",
            "routine_photo_campus": "有很多榕树的校园"})
        self.assertEqual(response.status_code, 200, response.text)
        settings, snapshot, scene = self.client.generate.call_args.args
        self.assertEqual(settings.routine_photo_key, "preview-only-secret")
        self.assertEqual(settings.routine_photo_ratio, "3:4")
        self.assertEqual(scene, "dining")
        self.assertIn("食堂", snapshot["current"]["activity"])
        self.assertEqual(self.store.snapshot().routine_photo_api_key, "saved-photo-secret")
        self.assertNotIn("secret", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_config_save_keeps_key_private_and_restores_all_photo_options(self):
        config = self.store.public_dict()
        config.update(routine_enabled=True, routine_sleep_silent=True, routine_photo_enabled=True,
            routine_photo_api_key="new-photo-secret", routine_photo_ratio="3:4", routine_photo_quality="medium",
            routine_photo_trigger="always", routine_photo_cooldown_minutes=45)
        response = await self.http.put("/admin/api/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("new-photo-secret", response.text)
        self.assertNotIn("new-photo-secret", self.store.path.read_text(encoding="utf-8"))
        read = (await self.http.get("/admin/api/config")).json()
        self.assertTrue(read["routine_photo_configured"])
        self.assertTrue(read["routine_sleep_silent"])
        self.assertEqual(read["routine_photo_cooldown_minutes"], 45)
        saved = RuntimeConfigStore(self.store.path, BASE).snapshot()
        self.assertEqual(saved.routine_photo_api_key, "new-photo-secret")
        self.assertEqual(saved.routine_photo_ratio, "3:4")
        self.assertEqual(saved.routine_photo_trigger, "always")

    async def test_invalid_photo_settings_never_generate(self):
        for data in ({"routine_photo_ratio": "16:9"}, {"routine_photo_model": "some-chat-model"},
                     {"routine_photo_api_base_url": "https://private:secret@example.com"},
                     {"routine_photo_quality": "max"}, {"routine_photo_campus": ""}):
            self.assertEqual((await self.http.post("/admin/api/routine/photo/preview", json=data)).status_code, 422)
        self.client.generate.assert_not_awaited()

    async def test_routine_preview_reads_saved_diary_without_generating_or_exposing_image_bytes(self):
        from datetime import datetime
        from test_routine_diary import SCHEDULE
        self.store.update({"routine_enabled": True, "routine_variation": "fixed", "routine_weekday_schedule": SCHEDULE})
        with patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 21)):
            self.assertEqual((await self.http.get("/admin/api/routine")).json()["diary"], [])
            events = self.client.diary.observe(self.store.snapshot())
            dinner = next(row for row in events if row["label"] == "晚饭")
            self.client.diary.seal(dinner["event_id"], "食堂吃土豆牛肉盖饭，配青菜")
            self.client.diary.save_photo(dinner["event_id"], CampusPhoto(b"private-image-bytes", "1024x768", "晚饭", "dining", "2026-09-21 18:00"))
            response = await self.http.get("/admin/api/routine")
            self.assertEqual(response.status_code, 200)
            rows = response.json()["diary"]
            self.assertEqual(len(rows), 1)
            self.assertIn("牛肉", rows[0]["details"])
            self.assertTrue(rows[0]["has_photo"])
            self.assertNotIn("private-image-bytes", response.text)
            self.assertEqual((await self.http.get("/admin/api/routine?offset=1")).json()["diary"], [])
            self.assertEqual((await self.http.get("/admin/api/routine", headers={"Authorization": "Bearer wrong"})).status_code, 401)
        self.client.generate.assert_not_awaited()
