from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_runtime_config import BASE
from xiaoke_bot.config import RuntimeConfigStore
from xiaoke_bot.intelligence import IntelligenceService
from xiaoke_bot.intelligence_store import IntelligenceStore, utc_stamp
from xiaoke_bot.judge import JevVerdict, build_questions, choice_answer, decide_reply, is_offensive, is_unsolicited
from xiaoke_bot.reminders import parse_reminder
from xiaoke_bot.semantic import scene_prompt, semantic_mood_delta

NOW = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
SCOPE = "agent:qq-group-100:group:100"
SETTINGS = replace(BASE, jev_enabled=True, jev_scene_enabled=True, jev_continuity_enabled=True,
    jev_memory_enabled=True, jev_followup_enabled=True, jev_tools_enabled=True,
    allowed_groups=frozenset({100}), superusers=frozenset({7}), allow_superuser_private_chat=True)


def verdict(**kwargs):
    return replace(JevVerdict(0, 0, 0, 0, 1), **kwargs)


class SemanticTests(unittest.TestCase):
    def test_complaining_about_server_is_not_hostility_toward_bot(self):
        result = verdict(scene="explain", emotion_target="thing", offensive=0.95)
        self.assertFalse(is_offensive(result, SETTINGS))
        self.assertIsNone(semantic_mood_delta(result))
        self.assertIn("分析问题", scene_prompt(result, SETTINGS))

    def test_direct_hostility_and_thanks_change_mood(self):
        self.assertLess(semantic_mood_delta(verdict(emotion_target="bot", offensive=0.99))[0], 0)
        self.assertGreater(semantic_mood_delta(verdict(scene="thanks"))[0], 0)

    def test_followup_continues_but_resolved_or_human_conversation_stops(self):
        follow = verdict(continuity="continue", intrusion=2)
        self.assertTrue(decide_reply(follow, SETTINGS)[0])
        self.assertFalse(is_unsolicited(follow, SETTINGS))
        for state in ("resolved", "closing", "others"):
            self.assertFalse(decide_reply(verdict(continuity=state, addressed=1, worth=1), SETTINGS)[0])

    def test_questions_are_opt_in_and_batched(self):
        base = set(build_questions(BASE))
        self.assertEqual(set(build_questions(SETTINGS)) - base,
            {"scene", "emotion_target", "reply_depth", "continuity", "memory_kind", "topic_state", "tool_intent"})

    def test_equivalent_labels_preserve_confident_action_without_accepting_uncertain_facts(self):
        answer = SimpleNamespace(choice="correction", confidence=0.65, probabilities={"correction":0.73,"durable":0.27})
        self.assertEqual(choice_answer({"memory":answer}, "memory", {"correction","durable"}, "none", 0.8, ("correction","durable")), "correction")
        answer.probabilities = {"correction":0.4,"durable":0.1,"joke":0.5}
        self.assertEqual(choice_answer({"memory":answer}, "memory", {"correction","durable"}, "none", 0.8, ("correction","durable")), "none")


class ReminderParsingTests(unittest.TestCase):
    def test_tomorrow_chinese_morning_and_postfix_time(self):
        for text in ("明早九点提醒我更新证书", "提醒我明天上午九点更新证书"):
            result = parse_reminder(text, "Asia/Taipei", NOW)
            self.assertEqual(result.due_at, datetime(2026, 9, 20, 1, tzinfo=timezone.utc))
            self.assertEqual(result.text, "更新证书")

    def test_relative_half_hour_and_explicit_date(self):
        self.assertEqual(parse_reminder("半小时后提醒我喝水", "Asia/Taipei", NOW).due_at, NOW + timedelta(minutes=30))
        self.assertEqual(parse_reminder("请帮我半小时后提醒我喝水", "Asia/Taipei", NOW).due_at, NOW + timedelta(minutes=30))
        self.assertEqual(parse_reminder("2026-09-20 21:30提醒我检查备份", "Asia/Taipei", NOW).due_at,
                         datetime(2026, 9, 20, 13, 30, tzinfo=timezone.utc))

    def test_relative_duration_crosses_dst_without_changing_elapsed_time(self):
        for start in (datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc),
                      datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)):
            self.assertEqual(parse_reminder("一小时后提醒我喝水", "America/New_York", start).due_at,
                             start + timedelta(hours=1))

    def test_next_week_and_pm(self):
        self.assertEqual(parse_reminder("下周一下午三点半提醒我开会", "Asia/Taipei", NOW).due_at,
                         datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc))

    def test_ambiguous_past_recurring_quoted_and_invalid_times_are_rejected(self):
        cases = ["九点提醒我更新证书", "今天九点提醒我更新证书", "每天九点提醒我更新证书",
                 "他说明天九点提醒我更新证书", "明天25点提醒我更新证书", "明天九点99分提醒我更新证书",
                 "0分钟后提醒我喝水", "9999999999999天后提醒我喝水", "明天九点提醒我", "2026-02-30 09:00提醒我喝水"]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_reminder(text, "Asia/Taipei", NOW)

    def test_dst_nonexistent_time_rejected(self):
        with self.assertRaises(ValueError):
            parse_reminder("2026-03-08 02:30提醒我喝水", "America/New_York", datetime(2026, 3, 1, tzinfo=timezone.utc))


class IntelligenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = IntelligenceStore(Path(self.folder.name) / "intelligence.db")
        self.config = RuntimeConfigStore(Path(self.folder.name) / "config.json", SETTINGS)
        self.jev = SimpleNamespace(classify=AsyncMock(return_value={}))
        self.chat = SimpleNamespace(complete=AsyncMock(return_value="今天讨论了部署问题。"))
        self.summary = SimpleNamespace(messages_between=AsyncMock(return_value=[]))
        self.service = IntelligenceService(self.store, self.config, self.jev, self.chat, self.summary)

    async def observe(self, text, result, event="1", user_id=7, scope=SCOPE):
        return await self.service.observe(scope=scope, user_id=user_id, display_name="测试用户", text=text,
            source_event=event, verdict=result, recent=[], settings=SETTINGS)

    async def tool(self, intent, query, event="r1", user_id=7, scope=SCOPE):
        return await self.service.handle_tool(intent=intent, query=query, source_event=event,
            scope=scope, user_id=user_id, group_id=100, display_name="测试用户", settings=SETTINGS, now=NOW)

    async def test_long_term_correction_supersedes_only_same_user_and_scope(self):
        await self.observe("我一直用 Windows", verdict(memory_kind="durable"))
        old = (await self.store.rows("facts"))[0]["id"]
        await self.observe("我一直用 Windows", verdict(memory_kind="durable"), user_id=8, event="2")
        self.jev.classify.return_value = {f"fact_{old}": "supersede"}
        await self.observe("我现在改用 Linux 了", verdict(memory_kind="correction"), event="3")
        mine = await self.store.rows("facts", scope=SCOPE, user_id=7, status="active")
        self.assertEqual([row["text"] for row in mine], ["我现在改用 Linux 了"])
        self.assertEqual((await self.store.rows("facts", user_id=8))[0]["status"], "active")
        reloaded = IntelligenceStore(self.store.path)
        self.assertEqual((await reloaded.rows("facts", user_id=7, status="superseded"))[0]["superseded_by"], mine[0]["id"])

    async def test_jokes_temporary_plans_and_failed_judgments_do_not_create_facts(self):
        for kind in ("none", "temporary", "joke"):
            await self.observe("我是 Linux 的国王，开玩笑的", verdict(memory_kind=kind), event=kind)
        await self.observe("没有判定", None)
        self.assertEqual(await self.store.rows("facts"), [])

    async def test_correction_outage_preserves_old_fact(self):
        await self.observe("我用 Windows", verdict(memory_kind="durable"))
        self.jev.classify.return_value = None
        self.assertFalse(await self.observe("改用 Linux", verdict(memory_kind="correction"), event="2"))
        self.assertEqual(len(await self.store.rows("facts", status="active")), 1)

    async def test_memory_relevance_rejects_old_information_and_carries_forgotten_tombstone(self):
        await self.observe("我用 Windows", verdict(memory_kind="durable"))
        old = (await self.store.rows("facts"))[0]["id"]
        await self.store.close_record("facts", old)
        await self.observe("我改用 Linux", verdict(memory_kind="correction"), event="2")
        new = (await self.store.rows("facts", status="active"))[0]["id"]
        self.jev.classify.return_value = {f"fact_{new}": "keep", "old_0": "drop"}
        result = await self.service.memory_context(query="怎么安装软件", scope=SCOPE, user_id=7,
            recalled="用户用 Windows", settings=SETTINGS)
        self.assertIn("Linux", result)
        self.assertNotIn("Windows", result)
        state = self.jev.classify.call_args.kwargs["state"]
        self.assertEqual(state["retired_facts"][0]["status"], "forgotten")

    async def test_topics_close_without_losing_history(self):
        await self.observe("昨天部署失败还没弄好", verdict(topic_state="open"))
        topic = (await self.store.rows("topics"))[0]
        self.jev.classify.return_value = {"topic": str(topic["id"])}
        await self.observe("刚刚修好了", verdict(topic_state="resolved"), event="2")
        self.assertEqual(await self.store.rows("topics", status="open"), [])
        self.assertEqual((await self.store.rows("topics"))[0]["status"], "resolved")

    async def test_followup_is_once_only_even_concurrently_and_after_restart(self):
        await self.observe("部署失败", verdict(topic_state="open"))
        topic = (await self.store.rows("topics"))[0]
        with self.store.connection() as db:
            db.execute("UPDATE topics SET created_at=?", (utc_stamp(NOW - timedelta(days=1)),))
        self.jev.classify.return_value = {"followup": str(topic["id"])}
        async def choose():
            return await self.service.followup(scope=SCOPE, user_id=7, text="又聊到服务器了", recent=[], settings=SETTINGS, now=NOW)
        results = await asyncio.gather(choose(), choose())
        self.assertEqual(sum(result is not None for result in results), 1)
        self.service.store = IntelligenceStore(self.store.path)
        self.assertIsNone(await choose())

    async def test_unrelated_and_too_recent_topics_are_not_followed_up(self):
        await self.observe("部署失败", verdict(topic_state="open"))
        self.assertIsNone(await self.service.followup(scope=SCOPE, user_id=7, text="午饭吃什么", recent=[], settings=SETTINGS, now=NOW))
        self.jev.classify.assert_not_awaited()

    async def test_reminder_create_persists_cancel_is_owner_scoped(self):
        result = await self.tool("reminder_create", "明早九点提醒我更新证书")
        self.assertIn("2026-09-20 09:00", result)
        reloaded = IntelligenceStore(self.store.path)
        row = (await reloaded.rows("reminders"))[0]
        self.assertFalse(await reloaded.close_record("reminders", row["id"], scope=SCOPE, user_id=8))
        self.assertFalse(await reloaded.close_record("reminders", row["id"], scope="other", user_id=7))
        self.assertTrue(await reloaded.close_record("reminders", row["id"], scope=SCOPE, user_id=7))

    async def test_invalid_reminder_does_not_claim_success(self):
        result = await self.tool("reminder_create", "九点提醒我更新证书")
        self.assertNotIn("已设置", result)
        self.assertEqual(await self.store.rows("reminders"), [])

    async def test_duplicate_event_does_not_create_second_reminder(self):
        await self.tool("reminder_create", "明早九点提醒我更新证书")
        await self.tool("reminder_create", "明早九点提醒我更新证书")
        self.assertEqual(len(await self.store.rows("reminders")), 1)

    async def test_repeated_relative_request_confirms_original_stored_time(self):
        original = await self.tool("reminder_create", "一小时后提醒我更新证书")
        repeated = await self.service.handle_tool(intent="reminder_create", query="一小时后提醒我更新证书",
            source_event="r1", scope=SCOPE, user_id=7, group_id=100, display_name="测试用户",
            settings=replace(SETTINGS, context_timezone="UTC"), now=NOW + timedelta(minutes=5))
        self.assertEqual(repeated, original)
        self.assertEqual(len(await self.store.rows("reminders")), 1)

    async def test_due_dispatch_is_atomic_and_survives_restart(self):
        await self.tool("reminder_create", "一分钟后提醒我更新证书")
        self.service.store = IntelligenceStore(self.store.path)
        await self.service.store.recover()
        send = AsyncMock()
        calls = await asyncio.gather(*[self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(minutes=2)) for _ in range(2)])
        self.assertEqual(sum(calls), 1)
        send.assert_awaited_once()
        self.assertEqual((await self.store.rows("reminders"))[0]["status"], "sent")

    async def test_not_due_cancelled_disabled_or_expired_never_send(self):
        await self.tool("reminder_create", "明早九点提醒我更新证书")
        send = AsyncMock()
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW)
        self.config.update({"jev_tools_enabled":False})
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(days=2))
        self.config.update({"jev_tools_enabled":True})
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(days=3))
        send.assert_not_awaited()
        self.assertEqual((await self.store.rows("reminders"))[0]["status"], "expired")

    async def test_group_permission_is_checked_again_at_delivery(self):
        await self.tool("reminder_create", "一分钟后提醒我更新证书")
        send = AsyncMock()
        await self.service.deliver_due(send=send, group_enabled=lambda _:False, now=NOW+timedelta(minutes=2))
        send.assert_not_awaited()
        self.assertEqual((await self.store.rows("reminders"))[0]["status"], "blocked")

    async def test_failed_or_uncertain_sends_are_not_repeated(self):
        await self.tool("reminder_create", "一分钟后提醒我更新证书")
        row = (await self.store.rows("reminders"))[0]
        await self.store.claim_reminder(row["id"], NOW+timedelta(minutes=2))
        await self.store.recover()
        send = AsyncMock()
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(minutes=3))
        send.assert_not_awaited()
        self.assertEqual((await self.store.rows("reminders"))[0]["status"], "uncertain")

    async def test_summary_and_memory_queries_are_scoped(self):
        await self.observe("我喜欢 Linux", verdict(memory_kind="durable"))
        self.assertIn("Linux", await self.tool("memory_query", "你记得我什么"))
        self.assertNotIn("Linux", await self.tool("memory_query", "你记得我什么", user_id=8))
        self.summary.messages_between.return_value = [{"display_name":"群友", "content":"今天部署好了"}]
        self.assertIn("部署", await self.tool("summary", "总结今天的聊天"))
        self.assertEqual(self.summary.messages_between.call_args.args[0], 100)
        self.assertEqual(self.chat.complete.await_count, 1)

    async def test_pending_limit_is_enforced_without_duplicates(self):
        for index in range(20):
            await self.tool("reminder_create", "明早九点提醒我更新证书", event=f"r{index}")
        result = await self.tool("reminder_create", "明早九点提醒我更新证书", event="r-new")
        self.assertIn("20 条", result)
        self.assertEqual(len(await self.store.rows("reminders")), 20)

    async def test_cancelled_and_failed_reminders_never_dispatch_again(self):
        await self.tool("reminder_create", "一分钟后提醒我更新证书")
        row = (await self.store.rows("reminders"))[0]
        await self.tool("reminder_cancel", f"取消提醒 {row['id']}")
        send = AsyncMock(side_effect=RuntimeError("offline"))
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(minutes=2))
        send.assert_not_awaited()
        await self.tool("reminder_create", "一分钟后提醒我更新证书", event="r2")
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(minutes=2))
        await self.service.deliver_due(send=send, group_enabled=lambda _:True, now=NOW+timedelta(minutes=3))
        send.assert_awaited_once()
        self.assertEqual((await self.store.rows("reminders", status="failed"))[0]["last_error"], "RuntimeError")

    async def test_unresolved_query_excludes_other_groups_closed_and_yesterday(self):
        await self.observe("当前服务器断连", verdict(topic_state="open"))
        await self.observe("另一群的事项", verdict(topic_state="open"), scope="other", event="2")
        with self.store.connection() as db:
            db.execute("UPDATE topics SET created_at=?", (utc_stamp(NOW),))
        result = await self.tool("unresolved", "整理今天未解决的问题")
        self.assertIn("当前服务器断连", result)
        self.assertNotIn("另一群", result)
        with self.store.connection() as db:
            db.execute("UPDATE topics SET created_at=?", (utc_stamp(NOW - timedelta(days=1)),))
        self.assertNotIn("当前服务器断连", await self.tool("unresolved", "整理今天未解决的问题"))


if __name__ == "__main__":
    unittest.main()
