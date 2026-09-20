from __future__ import annotations

import json
import unittest
from dataclasses import replace

import httpx

from test_runtime_config import BASE
from xiaoke_bot.clients import ChatClient, ServiceError, strip_think_blocks
from xiaoke_bot.config import Settings


class ChatClientTests(unittest.IsolatedAsyncioTestCase):
    def test_think_blocks_are_removed(self) -> None:
        content = "<think>第一段\n推理</think>\n正式回答\n<think data-x='1'>第二段</think>\n结束"
        self.assertEqual(strip_think_blocks(content), "正式回答\n结束")

    def test_orphan_think_tags_are_removed_safely(self) -> None:
        self.assertEqual(strip_think_blocks("隐藏推理</think>最终答案"), "最终答案")
        self.assertEqual(strip_think_blocks("可见答案<think>未闭合推理"), "可见答案")

    def test_tag_matching_is_case_insensitive(self) -> None:
        self.assertEqual(strip_think_blocks("<THINK>x</THINK>answer"), "answer")

    async def test_advanced_parameters_are_sent(self) -> None:
        captured: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        settings = Settings(
            bot_name="小可",
            superusers=frozenset({1}),
            allowed_groups=frozenset({2}),
            allow_superuser_private_chat=False,
            respond_without_at=False,
            probability_reply_enabled=False,
            reply_probability=0.1,
            trigger_keywords=("小可",),
            history_messages=12,
            max_reply_chars=3500,
            quote_reply_enabled=True,
            quote_reply_probability=1.0,
            segment_send_enabled=False,
            segment_probability=0.35,
            segment_max_parts=3,
            segment_delay_min=0.35,
            segment_delay_max=0.9,
            humanize_remove_punctuation=False,
            humanize_newline_to_space=False,
            humanize_delay_enabled=False,
            humanize_delay_min=0.6,
            humanize_delay_max=1.8,
            moderation_enabled=False,
            moderation_keywords=(),
            moderation_exempt_admins=True,
            member_analysis_enabled=True,
            member_analysis_auto=True,
            member_analysis_min_messages=15,
            member_analysis_interval_messages=10,
            member_analysis_sample_limit=30,
            member_message_retention=200,
            member_profile_in_reply=True,
            mood_in_reply=True,
            mood_half_life_hours=6.0,
            mood_event_nudges_enabled=True,
            favorability_decay_enabled=True,
            favorability_half_life_days=30.0,
            proactive_enabled=False,
            proactive_quiet_start=23,
            proactive_quiet_end=8,
            proactive_hourly_cap=2,
            proactive_daily_cap=8,
            proactive_cooldown_seconds=1800,
            api_base_url="https://example.test/v1",
            api_key="secret",
            model="model-a",
            temperature=0.6,
            max_tokens=900,
            top_p=0.85,
            presence_penalty=0.3,
            frequency_penalty=0.2,
            seed=42,
            reasoning_effort="low",
            response_format="json_object",
            stop_sequences=("STOP",),
            request_timeout=45,
            extra_body_json='{"enable_thinking": false}',
            prompt_identity="你是小可",
            prompt_personality="自然友善",
            prompt_speaking_style="日常中文",
            prompt_group_behavior="分清不同发言者",
            prompt_response_preferences="先直接回答",
            prompt_boundaries="不泄露秘密",
            context_timezone="Asia/Taipei",
            keyword_prompt_rules=(),
            system_prompt="prompt",
            memory_url="http://127.0.0.1:8420",
            memory_api_key="memory-secret",
            vision_enabled=False,
            vision_api_base_url="",
            vision_api_key="",
            vision_model="",
            vision_max_images=3,
            vision_skip_stickers=True,
            vision_prompt="描述图片",
            vision_timeout=60.0,
            webhook_enabled=False,
            webhook_token="",
            webhook_target_group=0,
            webhook_prefix="",
            webhook_template="",
            fallback_enabled=False,
            fallback_api_base_url="",
            fallback_api_key="",
            fallback_model="",
            offense_guard_enabled=False,
            offense_prompt="对方对你进行人身攻击、辱骂、恶意骚扰",
            offense_action="mute",
            offense_mute_duration=600,
            offense_threshold=1,
            offense_include_admins=False,
            jev_enabled=False,
            jev_model="jev-latest",
            jev_timeout=6.0,
            jev_gate_enabled=True,
            jev_offense_enabled=True,
            jev_addressed_threshold=0.80,
            jev_worth_threshold=0.70,
            jev_offense_threshold=0.85,
            jev_max_intrusion=1.30,
            jev_min_text_length=4,
            jev_context_messages=6,
            jev_use_proactive_budget=True,
            jev_log_enabled=True,
            jev_log_retention=500,
            jev_addressed_prompt="",
            jev_worth_prompt="",
            jev_intrusion_levels=("a", "b", "c"),
            prompt_interests="",
            jev_interest_prompt="",
            jev_interest_threshold=0.70,
            join_gate_enabled=False,
            join_gate_group=20003,
            join_gate_api_url="https://auth.example.com/api",
            join_gate_token="",
            join_gate_min_licenses=2,
            join_gate_refresh_hours=24,
            join_gate_reject_reason="未检测到有效授权",
            summary_enabled=False,
            summary_hour=20,
            summary_min_messages=20,
            summary_send_enabled=True,
        )
        client = ChatClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.complete([{"role": "user", "content": "hi"}], settings)
        finally:
            await client.close()

        self.assertEqual(result, "ok")
        self.assertEqual(captured["top_p"], 0.85)
        self.assertEqual(captured["seed"], 42)
        self.assertEqual(captured["reasoning_effort"], "low")
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertEqual(captured["stop"], ["STOP"])
        self.assertEqual(captured["enable_thinking"], False)

    async def _run_with_handler(self, settings, handler) -> str:
        client = ChatClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.complete([{"role": "user", "content": "hi"}], settings)
        finally:
            await client.close()

    async def test_falls_back_to_backup_on_primary_failure(self) -> None:
        settings = replace(BASE, model="primary", fallback_enabled=True, fallback_model="backup")

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body["model"] == "primary":
                return httpx.Response(503, json={"error": {"message": "负载较高 (2064)"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "backup-ok"}}]})

        self.assertEqual(await self._run_with_handler(settings, handler), "backup-ok")

    async def test_uses_primary_when_it_succeeds(self) -> None:
        settings = replace(BASE, model="primary", fallback_enabled=True, fallback_model="backup")

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": f"from-{body['model']}"}}]}
            )

        self.assertEqual(await self._run_with_handler(settings, handler), "from-primary")

    async def test_combined_error_when_both_fail(self) -> None:
        settings = replace(BASE, model="primary", fallback_enabled=True, fallback_model="backup")

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": {"message": "boom"}})

        with self.assertRaises(ServiceError) as ctx:
            await self._run_with_handler(settings, handler)
        self.assertIn("备用", str(ctx.exception))

    async def test_no_fallback_when_disabled(self) -> None:
        settings = replace(BASE, model="primary", fallback_enabled=False, fallback_model="backup")

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": {"message": "boom"}})

        with self.assertRaises(ServiceError):
            await self._run_with_handler(settings, handler)


if __name__ == "__main__":
    unittest.main()
