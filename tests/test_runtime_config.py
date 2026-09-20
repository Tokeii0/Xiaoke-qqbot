from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xiaoke_bot.config import KeywordPromptRule, RuntimeConfigStore, Settings


BASE = Settings(
    bot_name="小可",
    superusers=frozenset({10001}),
    allowed_groups=frozenset({20004}),
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
    api_base_url="https://example.com/v1",
    api_key="secret",
    model="test-model",
    temperature=0.7,
    max_tokens=1200,
    top_p=1.0,
    presence_penalty=0.0,
    frequency_penalty=0.0,
    seed=None,
    reasoning_effort="",
    response_format="text",
    stop_sequences=(),
    request_timeout=90.0,
    extra_body_json="{}",
    prompt_identity="你是小可",
    prompt_personality="自然友善",
    prompt_speaking_style="日常中文",
    prompt_group_behavior="分清不同发言者",
    prompt_response_preferences="先直接回答",
    prompt_boundaries="不泄露秘密",
    context_timezone="Asia/Taipei",
    keyword_prompt_rules=(),
    system_prompt="默认提示词",
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


class RuntimeConfigTests(unittest.TestCase):
    def test_update_persists_and_keeps_secrets_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot_config.json"
            store = RuntimeConfigStore(path, BASE)
            store.update(
                {
                    "bot_name": "新名字",
                    "allowed_groups": [10001, 10002],
                    "trigger_keywords": ["小可", "小可同学"],
                    "quote_reply_enabled": False,
                    "quote_reply_probability": 0.25,
                    "system_prompt": "新的默认提示词",
                }
            )
            reloaded = RuntimeConfigStore(path, BASE).snapshot()
            self.assertEqual(reloaded.bot_name, "新名字")
            self.assertEqual(reloaded.allowed_groups, frozenset({10001, 10002}))
            self.assertEqual(reloaded.trigger_keywords, ("小可", "小可同学"))
            self.assertFalse(reloaded.quote_reply_enabled)
            self.assertEqual(reloaded.quote_reply_probability, 0.25)
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))

    def test_rejects_empty_superusers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RuntimeConfigStore(Path(directory) / "bot_config.json", BASE)
            with self.assertRaises(ValueError):
                store.update({"superusers": []})

    def test_rejects_invalid_model_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RuntimeConfigStore(Path(directory) / "bot_config.json", BASE)
            with self.assertRaises(ValueError):
                store.update({"api_base_url": "file:///etc/passwd"})

    def test_timezone_persists_and_rejects_invalid_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot_config.json"
            store = RuntimeConfigStore(path, BASE)
            store.update({"context_timezone": "Asia/Shanghai"})
            self.assertEqual(
                RuntimeConfigStore(path, BASE).snapshot().context_timezone,
                "Asia/Shanghai",
            )
            with self.assertRaises(ValueError):
                store.update({"context_timezone": "UTC+8"})

    def test_api_key_is_written_only_to_secret_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "bot_config.json"
            secret_path = root / "bot_secrets.json"
            store = RuntimeConfigStore(config_path, BASE, secret_path)
            store.set_api_key("new-secret-key")
            self.assertTrue(store.public_dict()["api_key_configured"])
            self.assertNotIn("api_key", store.public_dict())
            self.assertEqual(
                RuntimeConfigStore(config_path, BASE, secret_path).snapshot().api_key,
                "new-secret-key",
            )

    def test_keyword_prompt_rules_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot_config.json"
            store = RuntimeConfigStore(path, BASE)
            store.update(
                {
                    "keyword_prompt_rules": [
                        {
                            "name": "天气场景",
                            "keywords": ["天气", "下雨"],
                            "prompt": "像朋友一样聊天气",
                            "enabled": True,
                            "probability": 0.7,
                        }
                    ]
                }
            )
            rule = RuntimeConfigStore(path, BASE).snapshot().keyword_prompt_rules[0]
            self.assertEqual(rule, KeywordPromptRule("天气场景", ("天气", "下雨"), "像朋友一样聊天气", True, 0.7))


if __name__ == "__main__":
    unittest.main()
