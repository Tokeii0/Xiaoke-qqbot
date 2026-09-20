from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from test_runtime_config import BASE
from xiaoke_bot.judge import JevClient, JevVerdict, build_questions, short_reply_prefixes
from xiaoke_bot.prompts import build_reply_style_prompt
from xiaoke_bot.semantic import reply_depth, reply_generation_settings

SETTINGS = replace(BASE, jev_enabled=True, jev_scene_enabled=True)
MINIMAL = JevVerdict(1, 1, 0, 0, 1, scene="joke", reply_depth="minimal")


class ReplyStyleTests(TestCase):
    def test_short_reaction_and_detailed_requests_have_distinct_guidance(self):
        self.assertEqual(reply_depth(MINIMAL, SETTINGS), "minimal")
        short = build_reply_style_prompt(reply_depth(MINIMAL, SETTINGS))
        self.assertIn("一个简短反应", short)
        self.assertIn("被问是否机器人时如实回答", short)
        detailed = build_reply_style_prompt(reply_depth(replace(MINIMAL, reply_depth="detailed"), SETTINGS))
        self.assertIn("完整内容", detailed)
        self.assertNotIn("2–12", detailed)

    def test_uncertain_disabled_and_missing_judgments_do_not_force_one_sentence(self):
        for verdict, settings in ((None, SETTINGS), (MINIMAL, replace(SETTINGS, jev_enabled=False)),
                                  (MINIMAL, BASE), (replace(MINIMAL, reply_depth="invalid"), SETTINGS)):
            self.assertEqual(reply_depth(verdict, settings), "normal")

    def test_serious_context_tools_and_search_override_a_conflicting_minimal_vote(self):
        for change in ({"scene": "explain"}, {"scene": "comfort"}, {"tool_intent": "reminder_create"},
                       {"feedback_kind": "voice"}, {"search_score": 1}, {"routine_reaction": "share"}):
            self.assertEqual(reply_depth(replace(MINIMAL, **change), SETTINGS), "normal")
        self.assertEqual(reply_depth(MINIMAL, SETTINGS, needs_detail=True), "normal")

    def test_only_minimal_generation_reduces_excessive_randomness_without_cutting_tokens(self):
        settings = replace(SETTINGS, temperature=2, max_tokens=1200)
        tuned = reply_generation_settings("minimal", settings)
        self.assertEqual(tuned.temperature, .9)
        self.assertEqual(tuned.max_tokens, settings.max_tokens)
        self.assertIs(reply_generation_settings("detailed", settings), settings)
        self.assertEqual(reply_generation_settings("minimal", replace(settings, temperature=.2)).temperature, .2)


class ReplyStyleJevTests(IsolatedAsyncioTestCase):
    async def test_trim_uses_only_confident_existing_prefix_and_keeps_uncertain_answers(self):
        original = "被发现了 摸一下怎么了"
        for complete, tail, expected in ((.95, .96, "被发现了"), (.8, .5, "被发现了"), (.79, .99, original), (.99, .49, original), (1.1, .99, original)):
            sdk = SimpleNamespace(system_one=AsyncMock(return_value=SimpleNamespace(answers={
                "reply_complete_0": SimpleNamespace(noul=complete), "reply_tail_0": SimpleNamespace(noul=tail)})))
            client = JevClient()
            client._client = sdk
            self.assertEqual(await client.trim_short_reply(query="又摸鱼", reply=original, recent=[], settings=SETTINGS), expected)
            sdk.system_one.assert_awaited_once()
            self.assertEqual(sdk.system_one.call_args.kwargs["state"]["reply"], original)

    async def test_trim_failure_or_no_boundary_preserves_original(self):
        client = JevClient()
        sdk = SimpleNamespace(system_one=AsyncMock(side_effect=TimeoutError))
        client._client = sdk
        for text in ("是呀", "小事。", "https://example.com/a b", "```a b```", "a " * 90):
            self.assertFalse(short_reply_prefixes(text))
            self.assertEqual(await client.trim_short_reply(query="测试", reply=text, recent=[], settings=SETTINGS), text)
        sdk.system_one.assert_not_awaited()
        original = "可以 但尚未配置"
        self.assertEqual(await client.trim_short_reply(query="测试", reply=original, recent=[], settings=BASE), original)
        sdk.system_one.assert_not_awaited()
        self.assertEqual(await client.trim_short_reply(query="测试", reply=original, recent=[], settings=SETTINGS), original)
        sdk.system_one.assert_awaited_once()

    async def test_selection_is_batched_and_uncertain_or_old_responses_fall_back(self):
        for choice, confidence, expected in (("minimal", .95, "minimal"), ("minimal", .79, "normal"),
                                              ("detailed", .96, "detailed"), ("invalid", .99, "normal"),
                                              (None, 0, "normal")):
            with self.subTest(choice=choice, confidence=confidence):
                answers = {key: SimpleNamespace(noul=.9) for key in ("addressed", "worth", "offensive")}
                answers["intrusion"] = SimpleNamespace(score=.1, confidence=.99)
                if choice:
                    answers["reply_depth"] = SimpleNamespace(choice=choice, confidence=confidence)
                sdk = SimpleNamespace(system_one=AsyncMock(return_value=SimpleNamespace(answers=answers)))
                client = JevClient()
                client._client = sdk
                result = await client.judge(current_text="又摸鱼", current_speaker="测试", recent=[], settings=SETTINGS)
                self.assertEqual(result.reply_depth, expected)
                sdk.system_one.assert_awaited_once()
                self.assertIn("reply_depth", sdk.system_one.call_args.kwargs["questions"])
        self.assertNotIn("reply_depth", build_questions(BASE))
