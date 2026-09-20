from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from test_runtime_config import BASE
from test_campus_photo import SETTINGS as PHOTO_SETTINGS, NOW, JPEG
from xiaoke_bot.campus_photo import CampusPhotoClient
from xiaoke_bot.clients import ChatClient, VisionClient
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.judge import JevClient
from xiaoke_bot.request_log import RequestLogStore, call_scope, packed, trace_request
from xiaoke_bot.routine import routine_snapshot
from xiaoke_bot.voice import LiveVoiceClient, VoiceClip
from xiaoke_bot.web_admin import register_web_admin

SETTINGS = replace(BASE, jev_enabled=True, jev_log_enabled=True, jev_log_retention=500,
    api_key="private-chat-key", api_base_url="https://model.test:8443/v1", model="main-model")


class RequestLogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = RequestLogStore()

    def detail(self, kind=""):
        return self.store.detail(self.store.recent(kind=kind)["items"][0]["id"])

    async def test_redacts_secrets_media_and_reasoning_but_keeps_useful_payloads(self):
        request = {"messages": [{"role": "user", "content": "hello private-chat-key"}],
            "image_url": {"url": "data:image/jpeg;base64,ABC"}, "Authorization": "Bearer hidden",
            "api_key": "arbitrary-secret", "number": float("nan"), "reasoning_content": "private-reasoning"}
        async with trace_request(self.store, kind="chat", settings=SETTINGS, model="model", request=request,
                endpoint="https://username:password@model.test:8443/v1/chat?api_key=private-chat-key") as trace:
            trace.response = {"text": "OK", "audio": "raw-audio", "wav": b"raw-wav"}
            trace.usage = {"total_tokens": 25}
        detail = self.detail()
        serialized = json.dumps(detail)
        for secret in ("private-chat-key", "ABC", "arbitrary-secret", "private-reasoning", "raw-audio", "raw-wav", "password@"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(detail["endpoint"], "https://model.test:8443/v1/chat")
        self.assertEqual(detail["usage"], {"total_tokens": 25})
        self.assertIsNone(detail["request"]["number"])
        self.assertEqual(detail["status"], "success")
        self.assertIsNotNone(detail["trace_id"])

    async def test_concurrent_scopes_keep_group_and_trace_separate(self):
        async def run(group):
            with call_scope("memory", group_id=group, user_id=group + 10):
                for name in ("first", "second"):
                    async with trace_request(self.store, kind="jev", settings=SETTINGS, model="jev", request={"name": name}):
                        await asyncio.sleep(0)
        await asyncio.gather(run(1), run(2))
        rows = self.store.recent()["items"]
        for group in (1, 2):
            items = [r for r in rows if r["group_id"] == group]
            self.assertEqual(len(items), 2)
            self.assertEqual(len({r["trace_id"] for r in items}), 1)
            self.assertTrue(all(r["user_id"] == group + 10 and r["feature"] == "memory" for r in items))
        self.assertEqual(len({r["trace_id"] for r in rows}), 2)
        async with trace_request(self.store, kind="chat", settings=SETTINGS, model="model", request={}):
            pass
        self.assertIsNone(self.detail()["group_id"])
        self.assertEqual(self.detail()["feature"], "reply")

    async def test_failure_cancellation_and_log_outage_do_not_change_call_outcome(self):
        with self.assertRaises(RuntimeError):
            async with trace_request(self.store, kind="jev", settings=SETTINGS, model="jev", request={}):
                raise RuntimeError("failure private-chat-key")
        self.assertEqual(self.detail()["status"], "error")
        self.assertNotIn("private-chat-key", self.detail()["error"])
        with self.assertRaises(asyncio.CancelledError):
            async with trace_request(self.store, kind="jev", settings=SETTINGS, model="jev", request={}):
                raise asyncio.CancelledError()
        self.assertEqual(self.detail()["status"], "cancelled")
        with patch.object(self.store, "begin", side_effect=OSError("disk unavailable")):
            async with trace_request(self.store, kind="chat", settings=SETTINGS, model="model", request={}) as trace:
                trace.response = "call still succeeds"

    async def test_retention_filters_pagination_and_disabled_jev_logging(self):
        settings = replace(SETTINGS, jev_log_retention=3)
        for n in range(6):
            with call_scope("knowledge", group_id=n % 2):
                async with trace_request(self.store, kind="jev", settings=settings, model="jev", request={"n": n}):
                    pass
        self.assertEqual(self.store.recent()["total"], 3)
        result = self.store.recent(group_id=1, feature="knowledge", status="success", limit=1, offset=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["stats"]["success"], 2)
        self.assertEqual(self.store.recent(query='"n": 5')["total"], 1)
        async with trace_request(self.store, kind="jev", settings=replace(settings, jev_log_enabled=False), model="jev", request={}):
            pass
        self.assertEqual(self.store.recent()["total"], 3)
        self.assertEqual(self.store.recent(status="error")["total"], 0)

    def test_disk_restart_preserves_detail_and_marks_interrupted_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requests.db"
            store = RequestLogStore(path)
            identity = store.begin(kind="chat", feature="reply", model="test", endpoint="", context={}, request=packed({"hello": "world"}), retention=5)
            recovered = RequestLogStore(path).detail(identity)
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual(recovered["request"], {"hello": "world"})

    async def test_chat_fallback_has_two_records_and_only_visible_answer(self):
        client = ChatClient(self.store)
        self.addAsyncCleanup(client.close)
        response_error = httpx.Response(503, json={"error": {"message": "outage"}})
        response_ok = httpx.Response(200, json={"choices": [{"message": {"content": "<think>hidden</think>答案", "reasoning_content": "hidden"}, "finish_reason": "stop"}], "usage": {"total_tokens": 123}})
        settings = replace(SETTINGS, fallback_enabled=True, fallback_api_base_url="https://fallback.test/v1", fallback_api_key="fallback-secret", fallback_model="fallback")
        with call_scope("routine_diary"), patch.object(client.http, "post", AsyncMock(side_effect=[response_error, response_ok])):
            answer = await client.complete([{"role": "user", "content": "今天的早餐 private-chat-key"}], settings)
        self.assertEqual(answer, "答案")
        rows = self.store.recent()["items"]
        self.assertEqual([r["status"] for r in rows], ["success", "error"])
        self.assertEqual([r["model"] for r in rows], ["fallback", "main-model"])
        self.assertEqual(len({r["trace_id"] for r in rows}), 1)
        self.assertEqual(self.detail()["response"]["text"], "答案")
        self.assertNotIn("hidden", json.dumps(self.detail()))
        self.assertNotIn("private-chat-key", json.dumps(self.detail()))
        self.assertEqual(self.detail()["feature"], "routine_diary")

    async def test_all_jev_entry_points_log_questions_scores_and_feature_sources(self):
        client, sdk = JevClient(self.store), AsyncMock()
        answers = {"addressed": SimpleNamespace(noul=0.9), "worth": SimpleNamespace(noul=0.8),
            "offensive": SimpleNamespace(noul=0), "intrusion": SimpleNamespace(score=0, confidence=1),
            "voice_suitable": SimpleNamespace(noul=0.9), "choice": SimpleNamespace(choice="yes", confidence=0.99)}
        sdk.system_one.return_value = SimpleNamespace(answers=answers)
        with patch.object(client, "_ensure", AsyncMock(return_value=sdk)):
            await client.judge(current_text="你好", current_speaker="测试", recent=[], settings=SETTINGS)
            await client.judge_voice(reply_text="你好", recent=[], settings=replace(SETTINGS, voice_jev_gate_enabled=True))
            for feature in ("memory", "memory_recall", "topic", "knowledge", "feedback", "preference"):
                answer = await client.classify(state={"query": "测试"}, questions={"choice": ("选择", {"yes": "是"})}, settings=SETTINGS, feature=feature)
                self.assertEqual(answer, {"choice": "yes"})
        rows = self.store.recent()["items"]
        self.assertEqual({r["feature"] for r in rows}, {"gate", "voice_judge", "memory", "memory_recall", "topic", "knowledge", "feedback", "preference"})
        detail = self.detail()
        self.assertEqual(detail["response"]["choice"]["confidence"], 0.99)
        self.assertEqual(detail["request"]["questions"]["choice"]["instructions"], "选择")

    async def test_jev_timeout_records_failed_attempt(self):
        client, sdk = JevClient(self.store), AsyncMock()
        sdk.system_one.side_effect = asyncio.TimeoutError()
        with patch.object(client, "_ensure", AsyncMock(return_value=sdk)):
            result = await client.classify(state={}, questions={"x": ("test", {"yes": "yes"})}, settings=SETTINGS, feature="memory")
        self.assertIsNone(result)
        self.assertEqual(self.detail()["status"], "error")
        self.assertIn("TimeoutError", self.detail()["error"])

    async def test_vision_voice_and_image_keep_metadata_without_binary_payloads(self):
        vision = VisionClient(self.store)
        self.addAsyncCleanup(vision.close)
        response = httpx.Response(200, json={"choices": [{"message": {"content": "校园"}}], "usage": {"total_tokens": 12}})
        settings = replace(SETTINGS, vision_enabled=True, vision_model="vision-model")
        with patch.object(vision.http, "post", AsyncMock(return_value=response)):
            await vision.describe(["base64://" + base64.b64encode(JPEG).decode()], "看图", settings)
        self.assertEqual(self.detail("vision")["response"]["text"], "校园")
        voice = LiveVoiceClient(self.store)
        with patch.object(voice, "_generate_checked", AsyncMock(return_value=VoiceClip(b"RAWVOICE", "你好", 2, 3))):
            await voice.generate("你好", SETTINGS)
        self.assertEqual(self.detail("voice")["usage"], {"seconds": 3})
        photo = CampusPhotoClient(request_log=self.store)
        response = httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(JPEG).decode()}], "usage": {"total_tokens": 50}})
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
            await photo.generate(replace(PHOTO_SETTINGS, jev_log_enabled=True), routine_snapshot(PHOTO_SETTINGS, NOW), "sport")
        self.assertIn("prompt", self.detail("image")["request"])
        self.assertEqual(self.detail("image")["usage"]["total_tokens"], 50)
        for kind in ("vision", "voice", "image"):
            serialized = json.dumps(self.detail(kind))
            self.assertNotIn(base64.b64encode(JPEG).decode(), serialized)
            self.assertNotIn("RAWVOICE", serialized)


class RequestLogWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_and_detail_are_authenticated_uncached_and_filterable(self):
        journal = RequestLogStore()
        async with trace_request(journal, kind="jev", settings=SETTINGS, model="jev", feature="knowledge", request={"query": "早餐"}):
            pass
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"WEB_ADMIN_ENABLED": "true", "WEB_ADMIN_TOKEN": "test-admin"}):
            app = FastAPI()
            config = RuntimeConfigStore(Path(folder) / "config.json", BASE)
            with patch("xiaoke_bot.web_admin.get_app", return_value=app):
                register_web_admin(config, None, None, None, None, None, request_log=journal)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                for path in ("/admin/api/requests", "/admin/api/requests/1"):
                    self.assertEqual((await http.get(path)).status_code, 401)
                http.headers["Authorization"] = "Bearer test-admin"
                response = await http.get("/admin/api/requests?kind=jev&feature=knowledge&limit=1")
                self.assertEqual(response.json()["total"], 1)
                self.assertEqual(response.headers["cache-control"], "no-store")
                response = await http.get("/admin/api/requests/1")
                self.assertEqual(response.json()["request"]["query"], "早餐")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual((await http.get("/admin/api/requests/999")).status_code, 404)
                self.assertEqual((await http.get("/admin/api/requests?kind=voice")).json()["total"], 0)
                payload = config.public_dict()
                payload["routine_photo_threshold"] = 0.85
                response = await http.put("/admin/api/config", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["config"]["routine_photo_threshold"], 0.85)
                payload["routine_photo_threshold"] = 1.5
                self.assertEqual((await http.put("/admin/api/config", json=payload)).status_code, 422)
