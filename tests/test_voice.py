from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from websockets.asyncio.server import serve

from test_runtime_config import BASE
from xiaoke_bot.clients import ServiceError
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.voice import (
    BYTES_PER_SECOND, LiveVoiceClient, PCMClip, VoiceClip,
    live_websocket_url, maybe_send_voice_reply, spoken_text,
)

VOICE = replace(BASE, voice_enabled=True, voice_api_key="voice-test-secret")
SPEECH = struct.pack("<h", 1500) * 4800


class VoiceConfigTests(unittest.TestCase):
    def test_settings_and_secret_survive_reload_without_disclosure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            store = RuntimeConfigStore(path, BASE)
            store.update({"voice_enabled": True, "voice_name": "gleam", "voice_pace": "slow",
                          "voice_instructions": "甜而自然", "voice_reply_probability": 0.3,
                          "voice_jev_enabled": True, "voice_jev_prompt": "安慰时温柔",
                          "voice_jev_gate_enabled": True, "voice_jev_gate_prompt": "代码用文字"})
            store.set_voice_api_key("private-voice-key")
            reloaded = RuntimeConfigStore(path, BASE)
            self.assertTrue(reloaded.snapshot().voice_configured)
            self.assertEqual(reloaded.snapshot().voice_name, "gleam")
            self.assertTrue(reloaded.snapshot().voice_jev_enabled)
            self.assertTrue(reloaded.snapshot().voice_jev_gate_enabled)
            self.assertEqual(reloaded.snapshot().voice_jev_prompt, "安慰时温柔")
            self.assertEqual(reloaded.snapshot().voice_jev_gate_prompt, "代码用文字")
            self.assertEqual(reloaded.snapshot().voice_api_key, "private-voice-key")
            self.assertNotIn("private-voice-key", json.dumps(reloaded.public_dict()))
            self.assertNotIn("private-voice-key", path.read_text(encoding="utf-8"))
            reloaded.set_voice_api_key("")
            self.assertFalse(RuntimeConfigStore(path, BASE).snapshot().voice_configured)

    def test_rejects_wrong_protocol_models_credentials_and_invalid_ranges(self):
        invalid = {"voice_model": "gpt-4o-mini-tts", "voice_name": "unknown",
                   "voice_pace": "fastest", "voice_timeout": float("nan"),
                   "voice_silence_seconds": 0, "voice_reply_probability": 1.1,
                   "voice_max_chars": 1001, "voice_api_base_url": "https://key@example.com/v1"}
        for key, value in invalid.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                RuntimeConfigStore._normalize(key, value)

    def test_disabled_by_default_and_does_not_reuse_chat_key(self):
        self.assertFalse(BASE.voice_enabled)
        self.assertFalse(replace(BASE, voice_enabled=True).voice_configured)


class VoiceAudioTests(unittest.TestCase):
    def test_boundary_requires_actual_audio_silence_and_ignores_leading_silence(self):
        pcm = PCMClip()
        pcm.append(bytes(BYTES_PER_SECOND * 4))
        self.assertFalse(pcm.has_ended(1))
        # Packet boundaries aren't frame boundaries.
        pcm.append(SPEECH[:100])
        pcm.append(SPEECH[100:] + bytes(BYTES_PER_SECOND // 2))
        self.assertFalse(pcm.has_ended(1))
        pcm.append(SPEECH + bytes(BYTES_PER_SECOND))
        self.assertTrue(pcm.has_ended(1))
        clip = pcm.finish("你好。", 7)
        with wave.open(io.BytesIO(clip.wav)) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 24000))
            self.assertLess(wav.getnframes() / wav.getframerate(), 2)

    def test_url_uses_live_path_without_realtime_query_parameters(self):
        self.assertEqual(live_websocket_url("https://api.openai.com/v1"), "wss://api.openai.com/v1/live/sessions")
        self.assertEqual(live_websocket_url("wss://example.com/v1/live/sessions/"), "wss://example.com/v1/live/sessions")
        with self.assertRaises(ServiceError):
            live_websocket_url("https://example.com/v1?api_key=private")

    def test_speech_keeps_punctuation_but_removes_hidden_markers(self):
        self.assertEqual(spoken_text("<think>秘密</think>**你好**，慢慢说。[[OFFENSE]]"), "你好，慢慢说。")


class LiveProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_websocket_roundtrip_audio_pump_and_graceful_close(self):
        observed = []

        async def handler(socket):
            observed.append(json.loads(await socket.recv()))
            self.assertEqual(socket.request.headers["Authorization"], "Bearer voice-test-secret")
            await socket.send(json.dumps({"type": "session.started", "session": {"id": "local-test"}}))
            heard_silence = instructed = False
            while not (heard_silence and instructed):
                message = json.loads(await socket.recv())
                observed.append(message)
                if message["type"] == "session.input_audio.append":
                    heard_silence = True
                    self.assertEqual(set(base64.b64decode(message["audio"])), {0})
                elif message["type"] == "session.instructions.append":
                    instructed = True
                    await socket.send(json.dumps({"type": "session.instructions.appended", "client_event_id": "read_reply"}))
            await socket.send(json.dumps({"type": "session.output_transcript.delta", "delta": "你好，"}))
            await socket.send(json.dumps({"type": "session.output_transcript.delta", "delta": "我在听。"}))
            for chunk in (SPEECH, bytes(BYTES_PER_SECOND)):
                await socket.send(json.dumps({"type": "session.output_audio.delta", "delta": base64.b64encode(chunk).decode()}))
            while True:
                message = json.loads(await socket.recv())
                observed.append(message)
                if message["type"] == "session.close":
                    await socket.send(json.dumps({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 2.1}}))
                    return

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            settings = replace(VOICE, voice_api_base_url=f"http://127.0.0.1:{port}/v1", voice_silence_seconds=1)
            clip = await LiveVoiceClient().generate("你好，我在听。", settings)
        self.assertEqual(observed[0]["session"]["model"], "gpt-live-1")
        self.assertEqual(observed[0]["session"]["delegation"], {"type": "client"})
        self.assertNotIn("speed", observed[0]["session"])
        self.assertEqual(clip.transcript, "你好，我在听。")
        self.assertEqual(clip.usage_seconds, 2.1)
        self.assertTrue(clip.wav.startswith(b"RIFF"))

    async def test_error_or_unexpected_close_never_returns_a_partial_clip(self):
        for event in ({"type": "error", "error": {"message": "bad voice-test-secret"}},
                      {"type": "session.closed", "reason": "content"}):
            socket = AsyncMock()
            socket.recv.side_effect = [json.dumps({"type": "session.started"}), json.dumps(event)]
            context = AsyncMock()
            context.__aenter__.return_value = socket
            with self.subTest(event=event), patch("xiaoke_bot.voice.connect", return_value=context):
                with self.assertRaises(ServiceError) as error:
                    await LiveVoiceClient().generate("你好", VOICE)
                self.assertNotIn("voice-test-secret", str(error.exception))
                self.assertIn({"type": "session.close"}, [json.loads(call.args[0]) for call in socket.send.call_args_list])

    async def test_timeout_is_an_error_and_requests_close(self):
        socket = AsyncMock()
        socket.recv.side_effect = [json.dumps({"type": "session.started"}), asyncio.TimeoutError()]
        context = AsyncMock()
        context.__aenter__.return_value = socket
        with patch("xiaoke_bot.voice.connect", return_value=context):
            with self.assertRaisesRegex(ServiceError, "超时"):
                await LiveVoiceClient().generate("你好", VOICE)
        self.assertIn({"type": "session.close"}, [json.loads(call.args[0]) for call in socket.send.call_args_list])


class VoiceDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncMock(spec=LiveVoiceClient)
        self.client.generate.return_value = VoiceClip(b"RIFF-audio", "你好，朋友。", 2, 3)
        self.text_sender = AsyncMock()
        self.audio_sender = AsyncMock()

    async def deliver(self, settings=VOICE, raw="你好，朋友。"):
        return await maybe_send_voice_reply(text="你好 朋友", raw_text=raw, settings=settings,
            client=self.client, send_text=self.text_sender, send_audio=self.audio_sender)

    async def test_success_uses_unmodified_punctuation_and_one_audio_message(self):
        self.assertTrue(await self.deliver())
        self.client.generate.assert_awaited_once_with("你好，朋友。", VOICE)
        self.audio_sender.assert_awaited_once_with(b"RIFF-audio")
        self.text_sender.assert_awaited_once_with("你好 朋友")

    async def test_voice_only_success_sends_no_text_label(self):
        self.assertTrue(await self.deliver(replace(VOICE, voice_send_text=False)))
        self.text_sender.assert_not_awaited()
        self.audio_sender.assert_awaited_once_with(b"RIFF-audio")

    async def test_disabled_long_code_and_probability_skip_without_request(self):
        for settings, raw in ((BASE, "你好"), (replace(VOICE, voice_reply_probability=0), "你好"),
                              (VOICE, "长" * 301), (VOICE, "```python\nprint(1)\n```")):
            self.assertFalse(await self.deliver(settings, raw))
        self.client.generate.assert_not_awaited()

    async def test_generation_failure_requests_original_text(self):
        self.client.generate.side_effect = ServiceError("unavailable")
        self.assertFalse(await self.deliver())
        self.text_sender.assert_not_awaited()
        self.audio_sender.assert_not_awaited()

    async def test_qq_failure_does_not_duplicate_already_sent_transcript(self):
        self.audio_sender.side_effect = RuntimeError("NapCat failed")
        self.assertTrue(await self.deliver())
        self.text_sender.assert_awaited_once()

    async def test_voice_only_qq_failure_requests_text_fallback(self):
        self.audio_sender.side_effect = RuntimeError("NapCat failed")
        self.assertFalse(await self.deliver(replace(VOICE, voice_send_text=False)))
        self.text_sender.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
