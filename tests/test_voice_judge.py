from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_runtime_config import BASE
from xiaoke_bot.judge import JevClient, JevVoiceVerdict
from xiaoke_bot.voice import LiveVoiceClient, VoiceClip, maybe_send_voice_reply, plan_voice_reply


SETTINGS = replace(BASE, jev_enabled=True, voice_enabled=True, voice_api_key="test-voice-key",
                   voice_jev_enabled=True, voice_jev_gate_enabled=True, voice_name="gleam")


def response(suitable=0.9, style="gentle", confidence=0.9):
    return SimpleNamespace(answers={
        "voice_suitable": SimpleNamespace(noul=suitable),
        "voice_style": SimpleNamespace(choice=style, confidence=confidence),
    })


class VoiceJudgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = JevClient()
        self.sdk = AsyncMock()
        self.sdk.system_one.return_value = response()
        self.client._client = self.sdk

    async def judge(self, settings=SETTINGS):
        return await self.client.judge_voice(
            reply_text="慢慢说，我在听。", recent=[{"speaker": "朋友", "text": "今天很累"}], settings=settings,
        )

    async def test_one_request_carries_both_judgments_and_no_credentials(self):
        self.assertEqual(await self.judge(), JevVoiceVerdict(suitable=0.9, style="gentle"))
        self.sdk.system_one.assert_awaited_once()
        request = self.sdk.system_one.call_args.kwargs
        self.assertEqual(set(request["questions"]), {"voice_suitable", "voice_style"})
        self.assertEqual(request["questions"]["voice_suitable"].type, "noul")
        self.assertEqual(request["questions"]["voice_style"].type, "choice")
        self.assertEqual(request["state"]["recent_messages"][0]["text"], "今天很累")
        self.assertNotIn("test-voice-key", str(request))

    async def test_disabled_judgments_do_not_call_jev(self):
        for settings in (replace(SETTINGS, jev_enabled=False),
                         replace(SETTINGS, voice_jev_enabled=False, voice_jev_gate_enabled=False)):
            self.assertIsNone(await self.judge(settings))
        self.sdk.system_one.assert_not_awaited()

    async def test_only_enabled_questions_are_sent(self):
        await self.judge(replace(SETTINGS, voice_jev_enabled=False))
        self.assertEqual(set(self.sdk.system_one.call_args.kwargs["questions"]), {"voice_suitable"})
        verdict = await self.judge(replace(SETTINGS, voice_jev_gate_enabled=False))
        self.assertEqual(set(self.sdk.system_one.call_args.kwargs["questions"]), {"voice_style"})
        self.assertIsNone(verdict.suitable)

    async def test_unknown_or_uncertain_style_keeps_suitability(self):
        for style, confidence in (("gentle", 0.4), ("untrusted-instructions", 1), ("sweet", float("nan"))):
            self.sdk.system_one.return_value = response(style=style, confidence=confidence)
            self.assertEqual(await self.judge(), JevVoiceVerdict(suitable=0.9))

    async def test_failed_missing_and_invalid_answers_fall_back(self):
        for result in (SimpleNamespace(answers={}), response(suitable=float("nan")), response(suitable=2)):
            self.sdk.system_one.return_value = result
            self.assertIsNone(await self.judge())
        self.sdk.system_one.side_effect = RuntimeError("test SDK failure")
        self.assertIsNone(await self.judge())

    async def test_timeout_bounds_additional_judgment(self):
        async def slow(**kwargs):
            await asyncio.sleep(2)
        self.sdk.system_one.side_effect = slow
        self.assertIsNone(await self.judge(replace(SETTINGS, jev_timeout=0.01)))

    async def test_missing_key_does_not_call_service(self):
        self.client._client = None
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}):
            self.assertIsNone(await self.judge())
        self.sdk.system_one.assert_not_awaited()

    async def test_voice_judgment_receives_current_routine_when_enabled(self):
        await self.judge(replace(SETTINGS, routine_enabled=True))
        request = self.sdk.system_one.call_args.kwargs
        self.assertIn("current", request["state"]["bot_routine"])
        self.assertIn("theme", request["state"]["bot_routine"])
        self.assertIn("events", request["state"]["bot_routine"])
        self.assertIn("睡前温和", request["questions"]["voice_style"].instructions)


class VoicePlanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.jev = AsyncMock(spec=JevClient)
        self.jev.judge_voice.return_value = JevVoiceVerdict(suitable=0.95, style="gentle")
        self.voice = AsyncMock(spec=LiveVoiceClient)
        self.voice.generate.return_value = VoiceClip(b"RIFF-test", "我在听。", 1, 1)
        self.send_text, self.send_audio = AsyncMock(), AsyncMock()

    async def deliver(self, settings=SETTINGS, raw="慢慢说，我在听。"):
        return await maybe_send_voice_reply(
            text=raw, raw_text=raw, settings=settings, client=self.voice, jev_client=self.jev,
            recent=[{"speaker": "朋友", "text": "今天很累"}],
            send_text=self.send_text, send_audio=self.send_audio,
        )

    async def test_style_changes_only_ephemeral_expression_and_pace(self):
        self.assertTrue(await self.deliver())
        applied = self.voice.generate.call_args.args[1]
        self.assertEqual(applied.voice_name, "gleam")
        self.assertEqual(applied.voice_api_key, SETTINGS.voice_api_key)
        self.assertEqual(applied.voice_pace, "slow")
        self.assertIn("温柔", applied.voice_instructions)
        self.assertEqual(SETTINGS.voice_pace, "natural")

    async def test_gate_rejection_uncertainty_or_unavailable_requests_text_without_live(self):
        for verdict in (JevVoiceVerdict(suitable=0.1), JevVoiceVerdict(suitable=0.69), None):
            self.jev.judge_voice.return_value = verdict
            self.assertFalse(await self.deliver())
        self.voice.generate.assert_not_awaited()
        self.send_audio.assert_not_awaited()
        self.send_text.assert_not_awaited()

    async def test_disabled_master_requires_text_when_gate_enabled(self):
        self.assertFalse(await self.deliver(replace(SETTINGS, jev_enabled=False)))
        self.jev.judge_voice.assert_not_awaited()
        self.voice.generate.assert_not_awaited()

    async def test_style_only_outage_keeps_fixed_voice_and_pace(self):
        self.jev.judge_voice.return_value = None
        settings = replace(SETTINGS, voice_jev_gate_enabled=False)
        plan = await plan_voice_reply("你好", settings, self.jev)
        self.assertTrue(plan.allowed)
        self.assertIs(plan.settings, settings)

    async def test_skipped_replies_cost_no_jev_or_live_requests(self):
        for settings, text in ((replace(SETTINGS, voice_enabled=False), "你好"),
                               (replace(SETTINGS, voice_reply_probability=0), "你好"),
                               (SETTINGS, "长" * 301), (SETTINGS, "```python\nprint(1)\n```")):
            self.assertFalse(await self.deliver(settings, text))
        self.jev.judge_voice.assert_not_awaited()
        self.voice.generate.assert_not_awaited()

    async def test_jev_sees_links_before_speech_markdown_cleanup(self):
        raw = "请打开[地址](https://example.com)"
        self.jev.judge_voice.return_value = JevVoiceVerdict(suitable=0.1)
        self.assertFalse(await self.deliver(raw=raw))
        self.assertEqual(self.jev.judge_voice.call_args.kwargs["reply_text"], raw)


if __name__ == "__main__":
    unittest.main()
