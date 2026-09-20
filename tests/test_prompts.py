from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from test_runtime_config import BASE
from xiaoke_bot.config import KeywordPromptRule
from xiaoke_bot.prompts import (
    build_base_system_prompt,
    build_member_nickname_prompt,
    build_member_profile_prompt,
    build_mood_prompt,
    build_runtime_context_prompt,
    build_voice_context_prompt,
    select_keyword_prompt_rules,
)


class PromptTests(unittest.TestCase):
    def test_structured_prompt_has_named_sections(self) -> None:
        prompt = build_base_system_prompt(BASE)
        self.assertIn("## Bot 基本介绍\n你是小可", prompt)
        self.assertIn("## 说话风格\n日常中文", prompt)
        self.assertIn("## 安全与边界\n不泄露秘密", prompt)

    def test_keyword_rules_match_normalized_text_and_probability(self) -> None:
        rules = (
            KeywordPromptRule("天气", ("天气",), "聊聊天气", True, 0.5),
            KeywordPromptRule("禁用", ("天气",), "不会触发", False, 1.0),
            KeywordPromptRule("全角", ("abc",), "全角匹配", True, 1.0),
        )
        selected = select_keyword_prompt_rules(
            "今天天气不错，Ａ B C 也出现了",
            rules,
            samples=[0.2, 0.0],
        )
        self.assertEqual([rule.name for rule in selected], ["天气", "全角"])

    def test_at_most_three_rules_are_injected(self) -> None:
        rules = tuple(
            KeywordPromptRule(f"规则{i}", ("命中",), f"提示{i}", True, 1.0)
            for i in range(5)
        )
        selected = select_keyword_prompt_rules("命中", rules, samples=[0.0] * 5)
        self.assertEqual(len(selected), 3)

    def test_runtime_context_contains_current_time_and_reply_target(self) -> None:
        context = build_runtime_context_prompt(
            BASE,
            chat_type="group",
            group_id=20004,
            user_id=10001,
            display_name="测试用户",
            now=datetime(2026, 7, 13, 2, 30, tzinfo=timezone.utc),
        )
        self.assertIn("机器人名称：小可", context)
        self.assertIn("当前日期：2026-07-13", context)
        self.assertIn("当前时间：10:30:00", context)
        self.assertIn("星期：星期一", context)
        self.assertIn("当前群号：20004", context)
        self.assertIn("本轮回复目标昵称：测试用户", context)

    def test_member_profile_prompt_reflects_favorability_and_skips_empty(self) -> None:
        self.assertEqual(build_member_profile_prompt(None), "")
        self.assertEqual(
            build_member_profile_prompt(
                {"favorability": 50, "personality_traits": [], "interests": []}
            ),
            "",
        )
        prompt = build_member_profile_prompt(
            {
                "favorability": 82.0,
                "profile_summary": "喜欢开黑",
                "personality_traits": ["外向", "幽默"],
                "interests": ["游戏"],
                "communication_style": "简短直接",
                "interaction_advice": "多聊游戏话题",
                "admin_note": "重点群友",
            }
        )
        self.assertIn("好感度：82/100", prompt)
        self.assertIn("较亲近", prompt)
        self.assertIn("外向、幽默", prompt)
        self.assertIn("重点群友", prompt)
        self.assertIn("不是用户指令", prompt)

    def test_mood_prompt_reflects_label_and_skips_neutral_or_disabled(self) -> None:
        self.assertEqual(build_mood_prompt("愉快", 0.4, 0.3, False), "")
        self.assertEqual(build_mood_prompt("平静", 0.05, 0.1, True), "")
        prompt = build_mood_prompt("愉快", 0.4, 0.3, True)
        self.assertIn("愉快", prompt)
        self.assertIn("不是用户指令", prompt)

    def test_member_nickname_prompt(self) -> None:
        self.assertEqual(build_member_nickname_prompt(""), "")
        self.assertEqual(build_member_nickname_prompt("   "), "")
        prompt = build_member_nickname_prompt("小王")
        self.assertIn("小王", prompt)
        self.assertIn("不是用户指令", prompt)


class VoiceContextTests(unittest.TestCase):
    def test_exposes_capability_without_credentials_or_delivery_claim(self) -> None:
        settings = replace(BASE, voice_enabled=True, voice_api_key="voice-secret",
                           voice_api_base_url="https://private-voice.example/v1",
                           jev_enabled=True, voice_jev_enabled=True, voice_max_chars=120)
        prompt = build_voice_context_prompt(settings)
        self.assertIn("你可以发送 QQ 语音消息", prompt)
        self.assertIn("120 字符", prompt)
        self.assertIn("甜美", prompt)
        self.assertIn("本轮尚未发送", prompt)
        self.assertNotIn("最近一次已完成回复", prompt)
        self.assertNotIn(settings.voice_api_key, prompt)
        self.assertNotIn(settings.voice_api_base_url, prompt)

    def test_tracks_unavailable_runtime_states(self) -> None:
        settings = replace(BASE, voice_enabled=True, voice_api_key="voice-secret")
        for current, reason in (
            (replace(settings, voice_enabled=False), "当前已关闭"),
            (replace(settings, voice_api_key=""), "尚未配置完整"),
            (replace(settings, voice_reply_probability=0), "发送比例为 0"),
            (replace(settings, voice_jev_gate_enabled=True, jev_enabled=False), "适用性判断未开启"),
        ):
            with self.subTest(reason=reason):
                prompt = build_voice_context_prompt(current)
                self.assertIn(reason, prompt)
                self.assertNotIn("你可以发送 QQ 语音消息", prompt)
                self.assertIn("本轮使用文字", prompt)

    def test_uses_scoped_preferences_and_actual_delivery_only(self) -> None:
        settings = replace(BASE, voice_enabled=True, voice_api_key="voice-secret")
        rules = [
            {"target_user_id": 7, "topic": "code", "mode": "text", "text": "untrusted-original-request"},
            {"target_user_id": 0, "topic": "technical", "mode": "text"},
            {"target_user_id": 7, "topic": "all", "mode": "auto"},
        ]
        prompt = build_voice_context_prompt(settings, preferences=rules, last_delivery="text")
        self.assertIn("当前回复对象的代码、命令和编程问题使用文字", prompt)
        self.assertIn("本群的技术讨论、排障和教程使用文字", prompt)
        self.assertNotIn("所有回复使用文字", prompt)
        self.assertNotIn("untrusted-original-request", prompt)
        self.assertIn("实际发送形式：文字", prompt)
        self.assertNotIn("实际发送形式：QQ 语音", prompt)


if __name__ == "__main__":
    unittest.main()
