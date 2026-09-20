from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from test_runtime_config import BASE
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.judge import JevClient, JevVoiceVerdict
from xiaoke_bot.voice import LiveVoiceClient, VoiceClip
from xiaoke_bot.web_admin import register_web_admin


class VoiceWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = RuntimeConfigStore(Path(folder.name) / "config.json", BASE)
        self.store.set_voice_api_key("saved-voice-secret")
        self.voice = AsyncMock(spec=LiveVoiceClient)
        self.voice.generate.return_value = VoiceClip(b"RIFF-test", "你好呀", 2, 3)
        self.jev = AsyncMock(spec=JevClient)
        self.jev.judge_voice.return_value = JevVoiceVerdict(suitable=0.95, style="gentle")
        environment = patch.dict(os.environ, {"WEB_ADMIN_ENABLED": "true", "WEB_ADMIN_TOKEN": "test-admin"})
        environment.start()
        self.addCleanup(environment.stop)
        app = FastAPI()
        with patch("xiaoke_bot.web_admin.get_app", return_value=app):
            register_web_admin(self.store, None, None, None, None, None, self.voice, self.jev)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                      headers={"Authorization": "Bearer test-admin"})
        self.addAsyncCleanup(self.http.aclose)

    async def test_preview_requires_admin(self):
        response = await self.http.post("/admin/api/voice/preview", json={"text": "你好"}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)
        self.voice.generate.assert_not_awaited()

    async def test_preview_uses_unsaved_form_and_does_not_persist_secrets(self):
        response = await self.http.post("/admin/api/voice/preview", json={
            "text": "你好呀", "voice_name": "gleam", "voice_instructions": "甜美自然",
            "voice_api_key": "preview-only-secret",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(base64.b64decode(result["audio_base64"]), b"RIFF-test")
        self.assertEqual(result["transcript"], "你好呀")
        settings = self.voice.generate.call_args.args[1]
        self.assertEqual(settings.voice_api_key, "preview-only-secret")
        self.assertEqual(settings.voice_name, "gleam")
        self.assertEqual(self.store.snapshot().voice_name, "marin")
        self.assertEqual(self.store.snapshot().voice_api_key, "saved-voice-secret")
        self.assertFalse(self.store.snapshot().voice_enabled)
        self.assertNotIn("secret", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_preview_clear_key_and_invalid_model(self):
        response = await self.http.post("/admin/api/voice/preview", json={"text": "你好", "clear_voice_api_key": True})
        self.assertEqual(response.status_code, 502)
        self.voice.generate.assert_not_awaited()
        self.jev.judge_voice.assert_not_awaited()
        response = await self.http.post("/admin/api/voice/preview", json={"text": "你好", "voice_model": "gpt-realtime-2.1"})
        self.assertEqual(response.status_code, 422)
        self.voice.generate.assert_not_awaited()

    async def test_config_save_and_read_never_echo_voice_key(self):
        values = self.store.public_dict()
        values.update(voice_enabled=True, voice_name="gleam", voice_api_key="new-private-key")
        response = await self.http.put("/admin/api/config", json=values)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["config"]["voice_configured"])
        self.assertNotIn("new-private-key", response.text)
        reloaded = RuntimeConfigStore(self.store.path, BASE)
        self.assertEqual(reloaded.snapshot().voice_api_key, "new-private-key")
        self.assertEqual(reloaded.snapshot().voice_name, "gleam")
        self.assertNotIn("new-private-key", (await self.http.get("/admin/api/config")).text)

    async def test_preview_jev_uses_context_and_unsaved_rules_without_changing_voice(self):
        self.store.update({"jev_enabled": True})
        response = await self.http.post("/admin/api/voice/preview", json={
            "text": "慢慢说，我在听。", "context": "今天很累",
            "voice_name": "gleam", "voice_jev_enabled": True, "voice_jev_gate_enabled": True,
            "voice_jev_prompt": "低落时温柔", "voice_jev_gate_prompt": "安慰适合语音",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["voice_style"], "Jev · 温柔安慰")
        self.assertTrue(response.json()["voice_allowed"])
        settings = self.voice.generate.call_args.args[1]
        self.assertEqual(settings.voice_name, "gleam")
        self.assertEqual(settings.voice_pace, "slow")
        self.assertEqual(settings.voice_jev_prompt, "低落时温柔")
        self.assertEqual(self.jev.judge_voice.call_args.kwargs["recent"][0]["text"], "今天很累")
        self.assertFalse(self.store.snapshot().voice_jev_enabled)
        self.assertFalse(self.store.snapshot().voice_jev_gate_enabled)

    async def test_preview_unsuitable_or_unavailable_does_not_call_live(self):
        self.store.update({"jev_enabled": True})
        for verdict in (JevVoiceVerdict(suitable=0.2, style="serious"), None):
            self.jev.judge_voice.return_value = verdict
            response = await self.http.post("/admin/api/voice/preview", json={
                "text": "复制这个地址", "voice_jev_gate_enabled": True,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["voice_allowed"])
            self.assertNotIn("audio_base64", response.json())
        self.voice.generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
