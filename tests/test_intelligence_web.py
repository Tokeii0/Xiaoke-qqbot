from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from test_runtime_config import BASE
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.intelligence_store import IntelligenceStore
from xiaoke_bot.web_admin import register_web_admin


class IntelligenceWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = RuntimeConfigStore(Path(folder.name) / "config.json", BASE)
        self.store = IntelligenceStore(Path(folder.name) / "intelligence.db")
        environment = patch.dict(os.environ, {"WEB_ADMIN_ENABLED":"true", "WEB_ADMIN_TOKEN":"test-admin"})
        environment.start()
        self.addCleanup(environment.stop)
        app = FastAPI()
        with patch("xiaoke_bot.web_admin.get_app", return_value=app):
            register_web_admin(self.config, None, None, None, None, None, intelligence=SimpleNamespace(store=self.store))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization":"Bearer test-admin"})
        self.addAsyncCleanup(self.http.aclose)

    async def test_read_and_mutation_require_authentication(self):
        self.assertEqual((await self.http.get("/admin/api/intelligence", headers={"Authorization":"Bearer wrong"})).status_code, 401)
        self.assertEqual((await self.http.post("/admin/api/intelligence/facts/1/close", headers={"Authorization":"Bearer wrong"})).status_code, 401)

    async def test_group_filter_and_close_record(self):
        for group in (100, 200):
            await self.store.save_fact(scope=f"agent:qq-group-{group}:group:{group}", user_id=7,
                display_name="测试", text=f"群 {group} 的记忆", source_event="1")
        response = await self.http.get("/admin/api/intelligence?group_id=100")
        self.assertEqual(response.status_code, 200)
        facts = response.json()["facts"]
        self.assertEqual(len(facts), 1)
        self.assertIn("100", facts[0]["text"])
        self.assertEqual((await self.http.post(f"/admin/api/intelligence/facts/{facts[0]['id']}/close")).status_code, 200)
        self.assertEqual((await self.http.post(f"/admin/api/intelligence/facts/{facts[0]['id']}/close")).status_code, 409)
        self.assertEqual((await self.http.post("/admin/api/intelligence/unsafe/1/close")).status_code, 404)

    async def test_all_five_flags_and_rules_survive_save_and_reload(self):
        config = self.config.public_dict()
        fields = ["jev_scene_enabled", "jev_continuity_enabled", "jev_memory_enabled", "jev_followup_enabled", "jev_tools_enabled", "jev_knowledge_enabled", "jev_feedback_enabled"]
        config.update({field:True for field in fields})
        config.update(jev_behavior_prompt="遇到服务器故障时一起排查", jev_followup_min_hours=24, jev_knowledge_max_age_days=60,
                      vision_mode="direct", vision_context_images=4)
        response = await self.http.put("/admin/api/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        reloaded = RuntimeConfigStore(self.config.path, BASE).snapshot()
        self.assertTrue(all(getattr(reloaded, field) for field in fields))
        self.assertEqual(reloaded.jev_followup_min_hours, 24)
        self.assertEqual(reloaded.jev_behavior_prompt, config["jev_behavior_prompt"])
        self.assertEqual(reloaded.vision_mode,"direct")
        self.assertEqual(reloaded.vision_context_images,4)
        self.assertEqual(reloaded.jev_knowledge_max_age_days,60)

    async def test_knowledge_sources_and_preference_admin_actions(self):
        source={"message_id":"123","user_id":7,"speaker":"测试","created_at":"2026-09-19T01:00:00+00:00","text":"已验证方法"}
        knowledge_id=await self.store.save_knowledge(scope="agent:qq-group-100:group:100",user_id=7,display_name="测试",
            question="问题",text="方法",sources=[source],source_event="123")
        preference=await self.store.save_preference(scope="agent:qq-group-100:group:100",user_id=7,target_user_id=0,
            display_name="管理员",text="群里只发文字",topic="all",mode="text",source_event="124")
        response=await self.http.get("/admin/api/intelligence?group_id=100")
        self.assertEqual(response.json()["knowledge"][0]["sources"][0]["message_id"],"123")
        for kind,record_id in (("knowledge",knowledge_id),("preferences",preference["id"])):
            self.assertEqual((await self.http.post(f"/admin/api/intelligence/{kind}/{record_id}/close",headers={"Authorization":"Bearer wrong"})).status_code,401)
            self.assertEqual((await self.http.post(f"/admin/api/intelligence/{kind}/{record_id}/close")).status_code,200)

    async def test_routine_preview_requires_auth_and_settings_survive_save(self):
        self.assertEqual((await self.http.get("/admin/api/routine", headers={"Authorization":"Bearer wrong"})).status_code, 401)
        self.assertEqual((await self.http.get("/admin/api/routine?offset=2")).status_code, 422)
        config = self.config.public_dict()
        config["routine_enabled"] = True
        config["routine_variation"] = "natural"
        custom = "00:00 | 睡觉 | 睡觉\n08:00 | 学习 | 准备考试"
        config["routine_weekday_schedule"] = custom
        response = await self.http.put("/admin/api/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(RuntimeConfigStore(self.config.path, BASE).snapshot().routine_weekday_schedule, custom)
        self.assertEqual(RuntimeConfigStore(self.config.path, BASE).snapshot().routine_variation, "natural")
        today = (await self.http.get("/admin/api/routine")).json()
        tomorrow = (await self.http.get("/admin/api/routine?offset=1")).json()
        self.assertTrue(today["enabled"])
        self.assertEqual(today["variation"], "natural")
        self.assertTrue(today["theme"])
        self.assertIn("events", today)
        self.assertIsNotNone(today["current"])
        self.assertIsNone(tomorrow["current"])
        self.assertNotEqual(today["date"], tomorrow["date"])
        config["routine_weekday_schedule"] = "bad schedule"
        self.assertEqual((await self.http.put("/admin/api/config", json=config)).status_code, 422)
        self.assertEqual(self.config.snapshot().routine_weekday_schedule, custom)
        config["routine_weekday_schedule"] = custom
        config["routine_variation"] = "surprise"
        self.assertEqual((await self.http.put("/admin/api/config", json=config)).status_code, 422)
        self.assertEqual(self.config.snapshot().routine_variation, "natural")


if __name__ == "__main__":
    unittest.main()
