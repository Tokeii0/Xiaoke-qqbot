from __future__ import annotations

import asyncio
import tempfile
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

from xiaoke_bot.config import (
    DEFAULT_JEV_ADDRESSED_PROMPT,
    DEFAULT_JEV_INTRUSION_LEVELS,
    DEFAULT_JEV_WORTH_PROMPT,
    LEGACY_JEV_ADDRESSED_PROMPTS,
    RuntimeConfigStore,
    get_settings,
)
from xiaoke_bot.judge import (
    INTRUSION_MAX,
    JevClient,
    JevVerdict,
    JudgeLogEntry,
    JudgeLogStore,
    build_questions,
    build_state,
    decide_reply,
    is_offensive,
    is_unsolicited,
    recent_from_history,
)


SETTINGS = replace(
    get_settings(),
    bot_name="小可",
    jev_enabled=True,
    jev_gate_enabled=True,
    jev_offense_enabled=True,
    jev_addressed_threshold=0.80,
    jev_worth_threshold=0.70,
    jev_offense_threshold=0.85,
    jev_max_intrusion=1.30,
    offense_prompt="对方对你进行人身攻击、辱骂、恶意骚扰或严重冒犯",
)


def verdict(
    addressed: float = 0.0,
    worth: float = 0.0,
    offensive: float = 0.0,
    intrusion: float = 0.0,
    confidence: float = 0.5,
    interested: float = 0.0,
) -> JevVerdict:
    return JevVerdict(
        addressed=addressed,
        worth=worth,
        offensive=offensive,
        intrusion=intrusion,
        intrusion_confidence=confidence,
        interested=interested,
    )


class DecideReplyTests(unittest.TestCase):
    """Cases and numbers below are real Jev answers observed during calibration."""

    def test_direct_name_call_replies(self) -> None:
        # "小可 今天天气怎么样"
        should, reason = decide_reply(verdict(addressed=0.97, worth=0.77, intrusion=0.24), SETTINGS)
        self.assertTrue(should)
        self.assertIn("addressed", reason)

    def test_following_up_on_bot_reply_replies(self) -> None:
        # "那日志在哪个目录" right after the bot suggested checking logs
        should, _ = decide_reply(verdict(addressed=0.81, worth=0.87, intrusion=1.38), SETTINGS)
        self.assertTrue(should)

    def test_unanswered_question_replies_unsolicited(self) -> None:
        # "有人知道这个报错怎么解决吗 TypeError..." -- nobody addressed the bot
        answer = verdict(addressed=0.20, worth=0.82, intrusion=0.95)
        should, reason = decide_reply(answer, SETTINGS)
        self.assertTrue(should)
        self.assertIn("worth", reason)
        self.assertTrue(is_unsolicited(answer, SETTINGS))

    def test_serious_human_discussion_stays_silent(self) -> None:
        # Two people negotiating a budget: worth is moderate but interrupting is rude.
        should, _ = decide_reply(verdict(addressed=0.16, worth=0.64, intrusion=1.97), SETTINGS)
        self.assertFalse(should)

    def test_high_worth_still_blocked_by_intrusion(self) -> None:
        # The intrusion gate must be able to veto the worth branch on its own.
        should, _ = decide_reply(verdict(addressed=0.10, worth=0.95, intrusion=1.90), SETTINGS)
        self.assertFalse(should)

    def test_spam_stays_silent(self) -> None:
        # "哈哈哈哈哈哈"
        should, _ = decide_reply(verdict(addressed=0.14, worth=0.05, intrusion=0.90), SETTINGS)
        self.assertFalse(should)

    def test_addressed_ignores_intrusion(self) -> None:
        # Someone asked the bot directly; staying silent would be the rude move.
        should, _ = decide_reply(
            verdict(addressed=0.97, worth=0.10, intrusion=INTRUSION_MAX), SETTINGS
        )
        self.assertTrue(should)

    def test_thresholds_are_inclusive(self) -> None:
        at_threshold = verdict(addressed=SETTINGS.jev_addressed_threshold)
        self.assertTrue(decide_reply(at_threshold, SETTINGS)[0])
        edge = verdict(worth=SETTINGS.jev_worth_threshold, intrusion=SETTINGS.jev_max_intrusion)
        self.assertTrue(decide_reply(edge, SETTINGS)[0])

    def test_retuning_thresholds_changes_outcome_without_new_inference(self) -> None:
        answer = verdict(addressed=0.20, worth=0.82, intrusion=0.95)
        self.assertTrue(decide_reply(answer, SETTINGS)[0])
        strict = replace(SETTINGS, jev_worth_threshold=0.9)
        self.assertFalse(decide_reply(answer, strict)[0])
        shy = replace(SETTINGS, jev_max_intrusion=0.5)
        self.assertFalse(decide_reply(answer, shy)[0])

    def test_is_unsolicited_false_when_addressed(self) -> None:
        self.assertFalse(is_unsolicited(verdict(addressed=0.97), SETTINGS))


class OffenceTests(unittest.TestCase):
    def test_real_insult_flagged(self) -> None:
        # "你个傻逼机器人滚出去"
        self.assertTrue(is_offensive(verdict(offensive=0.96), SETTINGS))

    def test_joking_complaint_not_flagged(self) -> None:
        # "笑死，小可又在胡说八道了" -- the distinction the old prompt kept fumbling
        self.assertFalse(is_offensive(verdict(offensive=0.20), SETTINGS))

    def test_threshold_is_inclusive_and_tunable(self) -> None:
        self.assertTrue(is_offensive(verdict(offensive=0.85), SETTINGS))
        lenient = replace(SETTINGS, jev_offense_threshold=0.99)
        self.assertFalse(is_offensive(verdict(offensive=0.96), lenient))


class BuildQuestionsTests(unittest.TestCase):
    def test_asks_all_four_together(self) -> None:
        questions = build_questions(SETTINGS)
        self.assertEqual(
            set(questions), {"addressed", "worth", "offensive", "intrusion"}
        )

    def test_bot_name_and_offence_criteria_reach_the_model(self) -> None:
        questions = build_questions(replace(SETTINGS, bot_name="测试机器人"))
        self.assertIn("测试机器人", str(questions["addressed"].instructions))
        self.assertIn("辱骂", str(questions["offensive"].instructions))

    def test_blank_offence_prompt_falls_back_to_a_usable_criterion(self) -> None:
        questions = build_questions(replace(SETTINGS, offense_prompt="   "))
        self.assertTrue(str(questions["offensive"].instructions).strip())

    def test_intrusion_levels_are_ordered_low_to_high(self) -> None:
        criteria = build_questions(SETTINGS)["intrusion"].criteria
        self.assertEqual(len(criteria), int(INTRUSION_MAX) + 1)
        self.assertIn("不会打扰", str(criteria[0]))
        self.assertIn("打断", str(criteria[-1]))


class ConfigurableWordingTests(unittest.TestCase):
    """The questions are editable config, not hardcoded bot-centric text."""

    def test_topic_trigger_replaces_the_default_worth_question(self) -> None:
        topic = "群里是否正在聊服务器部署或报错排查，并且存在没被解答的技术疑问？"
        questions = build_questions(replace(SETTINGS, jev_worth_prompt=topic))
        self.assertEqual(str(questions["worth"].instructions), topic)
        # nothing about the bot leaks into a topic-scoped trigger
        self.assertNotIn("机器人", str(questions["worth"].instructions))

    def test_blank_wording_falls_back_to_defaults(self) -> None:
        questions = build_questions(
            replace(SETTINGS, jev_worth_prompt="  ", jev_addressed_prompt="")
        )
        self.assertEqual(str(questions["worth"].instructions), DEFAULT_JEV_WORTH_PROMPT)
        self.assertTrue(str(questions["addressed"].instructions).strip())

    def test_bot_name_placeholder_is_substituted(self) -> None:
        questions = build_questions(
            replace(
                SETTINGS,
                bot_name="阿强",
                jev_addressed_prompt="这条消息是在叫 {bot_name} 吗？",
            )
        )
        self.assertEqual(str(questions["addressed"].instructions), "这条消息是在叫 阿强 吗？")

    def test_stray_brace_is_kept_verbatim_instead_of_crashing(self) -> None:
        # An operator typing a lone { must not take the reply gate down.
        questions = build_questions(
            replace(SETTINGS, jev_addressed_prompt="在叫 {不是占位符} 吗？")
        )
        self.assertIn("不是占位符", str(questions["addressed"].instructions))

    def test_custom_intrusion_levels_are_used(self) -> None:
        levels = ("安静", "轻松", "严肃")
        questions = build_questions(replace(SETTINGS, jev_intrusion_levels=levels))
        self.assertEqual(list(questions["intrusion"].criteria), list(levels))

    def test_wrong_level_count_falls_back_to_defaults(self) -> None:
        # jev_max_intrusion's 0-2 range assumes exactly 3 levels, so a bad list
        # must not silently rescale the score.
        questions = build_questions(replace(SETTINGS, jev_intrusion_levels=("a", "b")))
        self.assertEqual(
            list(questions["intrusion"].criteria), list(DEFAULT_JEV_INTRUSION_LEVELS)
        )


class StateTests(unittest.TestCase):
    """Regression guard for 「你xxx」 being misread as addressed to the bot.

    Every human used to be labelled "群成员", leaving the bot as the only named
    participant, so a bare second-person pronoun looked like it had to mean the bot.
    Real production scores on 「你」-prefixed member-to-member messages were 0.75-0.92.
    """

    HISTORY = [
        {"role": "user", "content": "[QQ:111 昵称:阿强] 我昨天把服务器搞挂了"},
        {"role": "user", "content": "[QQ:222 昵称:老王] 又是你？上次也是"},
        {"role": "assistant", "content": "那你得多练练"},
    ]

    def test_history_keeps_each_speaker_name(self) -> None:
        recent = recent_from_history(self.HISTORY, bot_name="小可", limit=10)
        self.assertEqual([m["speaker"] for m in recent], ["阿强", "老王", "小可"])
        self.assertEqual([m["is_bot"] for m in recent], [False, False, True])

    def test_speaker_prefix_is_stripped_from_text(self) -> None:
        recent = recent_from_history(self.HISTORY, bot_name="小可", limit=10)
        self.assertEqual(recent[0]["text"], "我昨天把服务器搞挂了")
        self.assertNotIn("QQ:", recent[0]["text"])

    def test_content_without_prefix_still_works(self) -> None:
        recent = recent_from_history(
            [{"role": "user", "content": "裸文本"}], bot_name="小可", limit=10
        )
        self.assertEqual(recent[0]["speaker"], "群成员")
        self.assertEqual(recent[0]["text"], "裸文本")

    def test_limit_keeps_the_newest(self) -> None:
        recent = recent_from_history(self.HISTORY, bot_name="小可", limit=1)
        self.assertEqual(len(recent), 1)
        self.assertTrue(recent[0]["is_bot"])
        self.assertEqual(recent_from_history(self.HISTORY, bot_name="小可", limit=0), [])

    def test_group_members_lists_humans_only(self) -> None:
        recent = recent_from_history(self.HISTORY, bot_name="小可", limit=10)
        state = build_state(
            bot_name="小可", recent=recent, current_speaker="Tokeii", current_text="你是傻逼不"
        )
        self.assertEqual(state["group_members"], ["阿强", "老王", "Tokeii"])
        self.assertNotIn("小可", state["group_members"])

    def test_group_members_deduplicates(self) -> None:
        recent = recent_from_history(
            [
                {"role": "user", "content": "[QQ:1 昵称:阿强] a"},
                {"role": "user", "content": "[QQ:1 昵称:阿强] b"},
            ],
            bot_name="小可",
            limit=10,
        )
        state = build_state(
            bot_name="小可", recent=recent, current_speaker="阿强", current_text="你好"
        )
        self.assertEqual(state["group_members"], ["阿强"])

    def test_bot_spoke_last_flag(self) -> None:
        recent = recent_from_history(self.HISTORY, bot_name="小可", limit=10)
        self.assertTrue(
            build_state(bot_name="小可", recent=recent, current_speaker="X", current_text="你说的对")[
                "bot_spoke_last"
            ]
        )
        humans = recent_from_history(self.HISTORY[:2], bot_name="小可", limit=10)
        self.assertFalse(
            build_state(bot_name="小可", recent=humans, current_speaker="X", current_text="你好")[
                "bot_spoke_last"
            ]
        )

    def test_empty_history_is_safe(self) -> None:
        state = build_state(
            bot_name="小可", recent=[], current_speaker="Tokeii", current_text="在吗"
        )
        self.assertFalse(state["bot_spoke_last"])
        self.assertEqual(state["group_members"], ["Tokeii"])

    def test_default_addressed_prompt_warns_about_pronouns(self) -> None:
        prompt = build_questions(SETTINGS)["addressed"].instructions
        text = str(prompt)
        self.assertIn("你", text)
        self.assertIn("group_members", text)
        self.assertIn("bot_spoke_last", text)


class LegacyPromptTests(unittest.TestCase):
    """An old default written back by the admin UI must not shadow the fix."""

    def tearDown(self) -> None:
        directory = getattr(self, "_dir", None)
        if directory is not None:
            directory.cleanup()

    def _store(self, saved: str) -> "RuntimeConfigStore":
        import json

        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "bot_config.json"
        path.write_text(json.dumps({"jev_addressed_prompt": saved}), encoding="utf-8")
        return RuntimeConfigStore(path, get_settings(), secret_path=path.with_name("s.json"))

    def test_persisted_legacy_default_is_dropped(self) -> None:
        legacy = next(iter(LEGACY_JEV_ADDRESSED_PROMPTS))
        store = self._store(legacy)
        self.assertEqual(store.snapshot().jev_addressed_prompt, DEFAULT_JEV_ADDRESSED_PROMPT)

    def test_custom_wording_is_preserved(self) -> None:
        store = self._store("我自己写的判定说明")
        self.assertEqual(store.snapshot().jev_addressed_prompt, "我自己写的判定说明")


class InterestTests(unittest.TestCase):
    """Topics of interest are a third, independent reason to join in."""

    INTERESTED = replace(SETTINGS, prompt_interests="服务器部署、报错排查\n新出的 AI 模型", jev_interest_threshold=0.70)

    def test_interest_opens_the_gate_when_nobody_asked(self) -> None:
        # Low worth (no unanswered question), but it is a topic the bot follows.
        should, reason = decide_reply(
            verdict(addressed=0.12, worth=0.30, interested=0.91, intrusion=0.40),
            self.INTERESTED,
        )
        self.assertTrue(should)
        self.assertIn("interested", reason)

    def test_interest_still_respects_intrusion(self) -> None:
        # Enthusiasm is not a licence to interrupt a serious discussion.
        should, _ = decide_reply(
            verdict(addressed=0.12, worth=0.30, interested=0.95, intrusion=1.90),
            self.INTERESTED,
        )
        self.assertFalse(should)

    def test_interest_reply_is_unsolicited_and_charged_to_budget(self) -> None:
        answer = verdict(addressed=0.12, worth=0.30, interested=0.91, intrusion=0.40)
        self.assertTrue(is_unsolicited(answer, self.INTERESTED))

    def test_no_interests_configured_means_no_question_and_no_trigger(self) -> None:
        questions = build_questions(replace(SETTINGS, prompt_interests=""))
        self.assertNotIn("interested", questions)
        # default 0.0 can never reach the threshold on its own
        should, _ = decide_reply(verdict(addressed=0.1, worth=0.1), SETTINGS)
        self.assertFalse(should)

    def test_interest_question_embeds_the_configured_topics(self) -> None:
        questions = build_questions(self.INTERESTED)
        self.assertIn("interested", questions)
        text = str(questions["interested"].instructions)
        self.assertIn("服务器部署", text)
        self.assertIn("新出的 AI 模型", text)

    def test_template_without_placeholder_still_includes_topics(self) -> None:
        questions = build_questions(
            replace(self.INTERESTED, jev_interest_prompt="聊的是不是它关心的东西？")
        )
        text = str(questions["interested"].instructions)
        self.assertIn("聊的是不是它关心的东西？", text)
        self.assertIn("服务器部署", text)

    def test_stray_brace_in_template_does_not_crash(self) -> None:
        questions = build_questions(
            replace(self.INTERESTED, jev_interest_prompt="关心 {不是占位符} 吗")
        )
        self.assertIn("服务器部署", str(questions["interested"].instructions))

    def test_interests_reach_the_persona_prompt_too(self) -> None:
        from xiaoke_bot.prompts import build_base_system_prompt

        prompt = build_base_system_prompt(self.INTERESTED)
        self.assertIn("感兴趣的话题", prompt)
        self.assertIn("服务器部署", prompt)


class JudgeLogStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.store = JudgeLogStore(Path(self._dir.name) / "judge_log.db")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def entry(self, *, text="在吗", replied=True, offended=False, budget=False, group=1):
        return JudgeLogEntry(
            group_id=group, user_id=42, display_name="路人", text=text,
            verdict=verdict(addressed=0.9, worth=0.5, offensive=0.1, intrusion=0.4),
            hard_rule=False, replied=replied, offended=offended,
            budget_blocked=budget, reason="addressed=0.90",
        )

    def test_record_and_read_back(self) -> None:
        asyncio.run(self.store.record(self.entry(text="你好呀"), 500))
        rows = asyncio.run(self.store.recent())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "你好呀")
        self.assertEqual(rows[0]["replied"], 1)
        self.assertAlmostEqual(rows[0]["addressed"], 0.9)

    def test_retention_prunes_oldest(self) -> None:
        for i in range(12):
            asyncio.run(self.store.record(self.entry(text=f"消息{i}"), 5))
        rows = asyncio.run(self.store.recent(limit=500))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["text"], "消息11")  # newest kept

    def test_long_text_is_truncated(self) -> None:
        asyncio.run(self.store.record(self.entry(text="长" * 1000), 500))
        stored = asyncio.run(self.store.recent())[0]["text"]
        self.assertLessEqual(len(stored), JudgeLogStore.MAX_TEXT + 1)

    def test_filters(self) -> None:
        asyncio.run(self.store.record(self.entry(replied=True), 500))
        asyncio.run(self.store.record(self.entry(replied=False), 500))
        asyncio.run(self.store.record(self.entry(replied=True, offended=True), 500))
        self.assertEqual(len(asyncio.run(self.store.recent(replied=True))), 2)
        self.assertEqual(len(asyncio.run(self.store.recent(replied=False))), 1)
        self.assertEqual(len(asyncio.run(self.store.recent(offended=True))), 1)
        self.assertEqual(len(asyncio.run(self.store.recent(group_id=999))), 0)

    def test_stats_rollup(self) -> None:
        asyncio.run(self.store.record(self.entry(replied=True), 500))
        asyncio.run(self.store.record(self.entry(replied=False, budget=True), 500))
        asyncio.run(self.store.record(self.entry(replied=True, offended=True), 500))
        stats = asyncio.run(self.store.stats(24))
        self.assertEqual(stats["judged"], 3)
        self.assertEqual(stats["replied"], 2)
        self.assertEqual(stats["silent"], 1)
        self.assertEqual(stats["offended"], 1)
        self.assertEqual(stats["budget_blocked"], 1)

    def test_clear(self) -> None:
        asyncio.run(self.store.record(self.entry(), 500))
        self.assertEqual(asyncio.run(self.store.clear()), 1)
        self.assertEqual(asyncio.run(self.store.recent()), [])


class FailOpenTests(unittest.TestCase):
    """A TypeSafe outage must never mute the bot -- judge() returns None instead."""

    def test_missing_api_key_yields_none(self) -> None:
        client = JevClient()
        with unittest.mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}, clear=False):
            result = asyncio.run(
                client.judge(
                    current_text="在吗", current_speaker="路人", recent=[], settings=SETTINGS
                )
            )
        self.assertIsNone(result)

    def test_api_exception_yields_none(self) -> None:
        class Boom:
            async def system_one(self, **_kwargs):
                raise RuntimeError("rate limited")

        client = JevClient()
        client._client = Boom()
        result = asyncio.run(
            client.judge(
                current_text="在吗", current_speaker="路人", recent=[], settings=SETTINGS
            )
        )
        self.assertIsNone(result)

    def test_malformed_response_yields_none(self) -> None:
        class Weird:
            async def system_one(self, **_kwargs):
                class Response:
                    answers = {"addressed": object()}

                return Response()

        client = JevClient()
        client._client = Weird()
        result = asyncio.run(
            client.judge(
                current_text="在吗", current_speaker="路人", recent=[], settings=SETTINGS
            )
        )
        self.assertIsNone(result)

    def test_close_is_safe_when_never_used(self) -> None:
        asyncio.run(JevClient().close())


class ClientLifecycleTests(unittest.TestCase):
    def test_sdk_still_exposes_the_teardown_we_call(self) -> None:
        # The SDK has aclose(), not close(). Guards against calling the wrong name.
        from typesafe_sdk import AsyncTypeSafeClient

        self.assertTrue(hasattr(AsyncTypeSafeClient, "aclose"))

    def test_close_calls_aclose_and_releases_the_client(self) -> None:
        calls: list[str] = []

        class Fake:
            async def aclose(self) -> None:
                calls.append("aclose")

        client = JevClient()
        client._client = Fake()
        asyncio.run(client.close())
        self.assertEqual(calls, ["aclose"])
        self.assertIsNone(client._client)


if __name__ == "__main__":
    unittest.main()
