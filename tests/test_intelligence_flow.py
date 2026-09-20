"""Exercise the actual NoneBot handler without a QQ connection or external calls."""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment

from test_runtime_config import BASE
from xiaoke_bot.clients import ServiceError
from xiaoke_bot.judge import JevVerdict
from xiaoke_bot.voice import VoiceClip


class IntelligenceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.previous_cwd = os.getcwd()
        os.chdir(self.folder.name)
        self.settings = replace(BASE, jev_enabled=True, jev_scene_enabled=True, jev_continuity_enabled=True,
            jev_memory_enabled=True, jev_tools_enabled=True, allowed_groups=frozenset({100}),
            voice_enabled=False, member_profile_in_reply=False, mood_in_reply=False,
            mood_event_nudges_enabled=False, vision_enabled=False, history_messages=10,
            humanize_delay_enabled=False, segment_send_enabled=False, quote_reply_enabled=False)
        try:
            nonebot.get_driver()
        except ValueError:
            nonebot.init()
        with patch("xiaoke_bot.config.get_settings", return_value=self.settings), \
             patch.dict(os.environ, {"WEB_ADMIN_ENABLED":"false", "BOT_CONFIG_FILE":"data/config.json", "BOT_SECRET_FILE":"data/secrets.json",
                                      "BOT_HISTORY_DB":"data/history.db", "BOT_JEV_LOG_DB":"data/judge.db", "BOT_INTELLIGENCE_DB":"data/intelligence.db",
                                      "BOT_MEMBER_DB":"data/members.db", "BOT_STATE_DB":"data/state.db", "BOT_SUMMARY_DB":"data/summaries.db"}):
            self.plugin = importlib.import_module("xiaoke_bot.plugin")
        self.original_clients = [self.plugin.chat_client, self.plugin.vision_client, self.plugin.memory_client, self.plugin.jev_client]
        self.sent = AsyncMock()
        self.diary_chat = SimpleNamespace(complete=AsyncMock(return_value='{"details":["手边放着一杯水"]}'))
        prepare_diary = self.plugin.campus_photo_client.diary.prepare
        async def prepare(settings, chat_client):
            return await prepare_diary(settings, self.diary_chat)
        self.patches = [
            patch.object(self.plugin.campus_photo_client.diary, "prepare", side_effect=prepare),
            patch.object(self.plugin.message_handler, "send", self.sent),
            patch.object(self.plugin.chat_client, "complete", AsyncMock(return_value="一起检查连接日志。")),
            patch.object(self.plugin.memory_client, "recall", AsyncMock(return_value="")),
            patch.object(self.plugin.memory_client, "capture", AsyncMock()),
            patch.object(self.plugin.member_profile_store, "get_profile", AsyncMock(return_value=None)),
            patch.object(self.plugin.intelligence, "observe", AsyncMock(return_value=False)),
            patch.object(self.plugin.intelligence, "memory_context", AsyncMock(return_value="")),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        if self.plugin._background_tasks:
            await asyncio.gather(*list(self.plugin._background_tasks), return_exceptions=True)
        for item in reversed(self.patches):
            item.stop()
        for client in self.original_clients:
            await client.close()
        sys.modules.pop("xiaoke_bot.plugin", None)
        os.chdir(self.previous_cwd)
        self.folder.cleanup()

    def event(self, text):
        return GroupMessageEvent(time=0, self_id=999, post_type="message", sub_type="normal",
            user_id=7, group_id=100, message_type="group", message_id=123,
            message=Message(text), original_message=Message(text), raw_message=text,
            font=0, sender={"user_id":7,"nickname":"测试","role":"member"}, to_me=True)

    async def test_global_memory_search_is_only_available_in_superuser_private_chat(self):
        text = "/小可 记忆 测试事实"
        operator = next(iter(self.settings.superusers))
        search = AsyncMock(return_value="网关测试结果")
        with patch.object(self.plugin.memory_client, "search", search):
            for user_id in (7, operator):
                event = self.event(text)
                event.user_id = user_id
                event.sender.role = "admin"
                await self.plugin.handle_message(SimpleNamespace(self_id=999), event)
                search.assert_not_awaited()
                self.assertIn("仅限超管在私聊", str(self.sent.await_args))

            def private_event(user_id):
                return PrivateMessageEvent(time=0, self_id=999, post_type="message", sub_type="friend",
                    user_id=user_id, message_type="private", message_id=124,
                    message=Message(text), original_message=Message(text), raw_message=text,
                    font=0, sender={"user_id":user_id,"nickname":"测试用户"}, to_me=True)

            self.sent.reset_mock()
            await self.plugin.handle_message(SimpleNamespace(self_id=999), private_event(7))
            search.assert_not_awaited()
            self.sent.assert_not_awaited()
            await self.plugin.handle_message(SimpleNamespace(self_id=999), private_event(operator))
            search.assert_awaited_once_with("测试事实", limit=5)
            self.assertIn("网关测试结果", str(self.sent.await_args))

    async def test_every_sent_chunk_after_first_is_paced_with_its_own_length(self):
        order = []
        async def send(message):
            order.append(("send", str(message)))
            return {"message_id": len(order)}
        async def delay(settings, text):
            order.append(("wait", text))
        settings = replace(self.settings, segment_send_enabled=True, segment_probability=1, max_reply_chars=50)
        text = "早饭很好吃 " + "中" * 120 + " 下次再聊"
        with patch.object(self.plugin.message_handler, "send", side_effect=send), \
             patch.object(self.plugin, "apply_segment_delay", side_effect=delay):
            identity = await self.plugin._send_text_answer(text, self.event("早饭"), settings)
        chunks = ["早饭很好吃", "中" * 50, "中" * 50, "中" * 20, "下次再聊"]
        expected = [("send", chunks[0])]
        for chunk in chunks[1:]:
            expected.extend([("wait", chunk), ("send", chunk)])
        self.assertEqual(order, expected)
        self.assertEqual(identity, "1")

    async def test_short_reaction_stays_one_bubble_without_adding_an_old_topic_question(self):
        self.plugin.runtime_config.update({"segment_send_enabled": True, "segment_probability": 1,
            "jev_followup_enabled": True})
        self.plugin.chat_client.complete.return_value = "被发现了 摸一下怎么了"
        verdict = JevVerdict(1, 1, 0, 0, 1, scene="joke", reply_depth="minimal")
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch.object(self.plugin.jev_client, "trim_short_reply", AsyncMock(return_value="被发现了")) as trim, \
             patch.object(self.plugin.intelligence, "followup", AsyncMock()) as followup:
            await self.plugin.handle_message(AsyncMock(), self.event("又在摸鱼"))
        self.sent.assert_awaited_once()
        self.assertIn("被发现了", str(self.sent.call_args.args[0]))
        self.assertNotIn("摸一下怎么了", str(self.sent.call_args.args[0]))
        trim.assert_awaited_once()
        self.assertEqual(self.plugin._histories["agent:qq-group-100:group:100"][-1]["content"], "被发现了")
        followup.assert_not_awaited()
        self.assertIn("本轮只需一个简短反应", str(self.plugin.chat_client.complete.call_args.args[0]))

    async def test_serious_request_keeps_complete_answer_even_if_shortness_vote_is_wrong(self):
        verdict = JevVerdict(1, 1, 0, 0, 1, scene="explain", reply_depth="minimal")
        answer = "先确认服务是否启动。\n再检查上游端口和错误日志，不能只凭 502 就判断是网络问题。"
        self.plugin.chat_client.complete.return_value = answer
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch.object(self.plugin, "_send_answer", AsyncMock(return_value=("991", "text"))) as send:
            await self.plugin.handle_message(AsyncMock(), self.event("Nginx 502 怎么排查"))
        self.assertEqual(send.call_args.kwargs["raw_text"], answer)
        prompt = str(self.plugin.chat_client.complete.call_args.args[0])
        self.assertNotIn("本轮只需一个简短反应", prompt)
        self.assertIn("按问题需要直接回答", prompt)

    async def test_sleep_transition_during_segment_wait_stops_remaining_chunks(self):
        with patch.object(self.plugin, "apply_segment_delay", AsyncMock()) as delay, \
             patch.object(self.plugin, "routine_is_sleeping", side_effect=[False, True]):
            with self.assertRaises(self.plugin.RoutineSleeping):
                await self.plugin._send_text_answer("字" * 110, self.event("hi"), replace(self.settings, max_reply_chars=50))
        self.assertEqual(self.sent.await_count, 1)
        delay.assert_awaited_once()

    async def test_photo_threshold_opens_reply_gate_for_contextual_life_question(self):
        settings = replace(self.settings, routine_enabled=True, routine_photo_enabled=True,
            jev_gate_enabled=True, jev_log_enabled=False, probability_reply_enabled=False)
        event = self.event("那早上吃什么呀")
        event.to_me = False
        for score, expected in ((0.74, False), (0.75, True), (0.95, True)):
            verdict = JevVerdict(0.3, 0.1, 0, 0, 1, routine_photo="dining", routine_photo_score=score)
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}), \
                 patch.object(self.plugin.JevClient, "configured", return_value=True), \
                 patch.object(self.plugin.jev_client, "judge", AsyncMock(return_value=verdict)):
                decision = await self.plugin._decide_response(event, "那早上吃什么呀", settings, 100, "g:100")
            self.assertEqual(decision.should_respond, expected)

    async def test_natural_reminder_routes_to_store_and_never_chat_generation(self):
        verdict = JevVerdict(1,1,0,0,1,tool_intent="reminder_create")
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True,verdict,False))):
            await self.plugin.handle_message(AsyncMock(), self.event("小可，明早九点提醒我更新证书"))
        self.plugin.chat_client.complete.assert_not_awaited()
        self.plugin.memory_client.recall.assert_not_awaited()
        rows = await self.plugin.intelligence.store.rows("reminders")
        self.assertEqual(rows[0]["text"], "更新证书")
        self.assertIn("已设置提醒", str(self.sent.call_args.args[0]))

    async def test_scene_prompt_reaches_chat_and_temporary_messages_skip_memory_capture(self):
        verdict = JevVerdict(1,1,0,0,1,scene="explain",emotion_target="thing",memory_kind="temporary")
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True,verdict,False))):
            await self.plugin.handle_message(AsyncMock(), self.event("小可，这服务器真垃圾"))
        messages = self.plugin.chat_client.complete.call_args.args[0]
        self.assertTrue(any("不是针对你" in item["content"] for item in messages))
        self.plugin.memory_client.capture.assert_not_awaited()

    async def test_routine_schedule_and_jev_reaction_reach_the_chat_model(self):
        self.plugin.runtime_config.update({"routine_enabled": True})
        verdict = JevVerdict(1, 1, 0, 0, 1, routine_reaction="focus")
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 9)):
            await self.plugin.handle_message(AsyncMock(), self.event("小可，帮我排查 Nginx 报错"))
        messages = self.plugin.chat_client.complete.call_args.args[0]
        self.assertTrue(any("你的校园日常" in item["content"] for item in messages))
        self.assertTrue(any("专心回应当前问题" in item["content"] for item in messages))
        self.assertTrue(any("你今天已经记录的角色生活" in item["content"] for item in messages))

    async def test_force_wake_opens_actual_reply_guard_and_supplies_reason_to_chat_and_judge(self):
        from xiaoke_bot.routine import local_now
        from test_routine_wake import NOW, REASON, SCHEDULE
        settings = replace(self.plugin.runtime_config.snapshot(), routine_enabled=True, routine_sleep_silent=True,
            routine_variation="fixed", routine_weekday_schedule=SCHEDULE,
            routine_diary_path=str(self.plugin.campus_photo_client.diary.path.resolve()))
        clock = lambda current_settings, now=None: local_now(current_settings, now or NOW)
        verdict = JevVerdict(1, 1, 0, 0, 1)
        with patch.object(self.plugin.runtime_config, "snapshot", return_value=settings), \
             patch("xiaoke_bot.routine.local_now", side_effect=clock), \
             patch.object(self.plugin.JevClient, "configured", return_value=True), \
             patch.object(self.plugin.jev_client, "judge", AsyncMock(return_value=verdict)) as judge:
            await self.plugin.handle_message(AsyncMock(), self.event("小可，起来聊天"))
            judge.assert_not_awaited()
            self.sent.assert_not_awaited()
            self.plugin.campus_photo_client.diary.force_wake(settings, REASON, NOW)
            await self.plugin.handle_message(AsyncMock(), self.event("小可，为什么起床了"))
        self.plugin.chat_client.complete.assert_awaited_once()
        self.sent.assert_awaited_once()
        self.diary_chat.complete.assert_not_awaited()
        self.assertIn(REASON, str(judge.call_args.kwargs["routine_events"]))
        self.assertIn(REASON, str(self.plugin.chat_client.complete.call_args.args[0]))

    async def test_sleep_mutes_addressed_chat_before_jev_then_wakes_automatically(self):
        self.plugin.runtime_config.update({"routine_enabled": True, "routine_sleep_silent": True})
        verdict = JevVerdict(1, 1, 0, 0, 1)
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))) as decide:
            with patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 2)):
                await self.plugin.handle_message(AsyncMock(), self.event("小可，快回复我"))
            decide.assert_not_awaited()
            self.plugin.chat_client.complete.assert_not_awaited()
            self.sent.assert_not_awaited()
            with patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 10)):
                await self.plugin.handle_message(AsyncMock(), self.event("小可，你在干嘛"))
            self.plugin.chat_client.complete.assert_awaited_once()
            self.sent.assert_awaited()

    async def test_jev_photo_scene_sends_image_and_records_actual_delivery(self):
        from xiaoke_bot.campus_photo import CampusPhoto
        self.plugin.runtime_config.update({"routine_enabled": True, "routine_photo_enabled": True})
        verdict = JevVerdict(1, 1, 0, 0, 1, routine_reaction="share", routine_photo="sport")
        photo = CampusPhoto(b"photo", "1024x768", "在操场慢跑", "sport", "2026-09-21 17:00", "recorded-sport")
        async def send_photo(**kwargs):
            await kwargs["send"](photo.data)
            return photo
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch.object(self.plugin.campus_photo_client, "maybe_send", AsyncMock(side_effect=send_photo)) as send, \
             patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 17)):
            await self.plugin.handle_message(AsyncMock(), self.event("小可，看看操场"))
        self.assertEqual(send.call_args.kwargs["verdict"].routine_photo, "sport")
        self.assertEqual(self.sent.await_count, 2)
        self.assertEqual(self.sent.call_args.args[0].type, "image")
        last = self.plugin._histories[self.plugin._session_key(self.event(""))][-1]
        self.assertEqual(last["photo"]["activity"], "在操场慢跑")
        self.assertEqual(last["photo"]["event_id"], "recorded-sport")
        self.assertEqual(send.call_args.kwargs["request_id"], "123")

    async def test_dinner_diary_reaches_jev_and_later_chat_with_same_facts(self):
        from test_routine_diary import SCHEDULE
        self.plugin.runtime_config.update({"routine_enabled": True, "routine_photo_enabled": True,
            "routine_variation": "fixed", "routine_weekday_schedule": SCHEDULE})
        async def judge(**kwargs):
            dinner = next(row for row in kwargs["routine_events"] if row["label"] == "晚饭")
            return JevVerdict(1, 1, 0, 0, 1, routine_reaction="share", routine_photo="dining", routine_photo_event=dinner["event_id"])
        with patch.object(self.plugin.JevClient, "configured", return_value=True), \
             patch.object(self.plugin.jev_client, "judge", AsyncMock(side_effect=judge)) as jev, \
             patch.object(self.plugin.campus_photo_client, "maybe_send", AsyncMock(return_value=None)), \
             patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 21)):
            await self.plugin.handle_message(AsyncMock(), self.event("小可，晚上吃的什么"))
            first = self.plugin.chat_client.complete.call_args.args[0]
            question = self.event("小可，你今晚吃了啥")
            question.user_id, question.message_id = 8, 124
            await self.plugin.handle_message(AsyncMock(), question)
            second = self.plugin.chat_client.complete.call_args.args[0]
        facts = lambda messages: next(item["content"] for item in messages if item["content"].startswith("## 你今天已经记录"))
        self.assertEqual(facts(first), facts(second))
        self.assertIn("本轮 JEV 判断对方在问这件事", facts(first))
        self.assertIn("已结束", facts(first))
        self.assertEqual(jev.await_count, 2)

    async def test_pending_reply_does_not_send_if_sleep_begins_during_generation(self):
        self.plugin.runtime_config.update({"routine_enabled": True, "routine_sleep_silent": True})
        verdict = JevVerdict(1, 1, 0, 0, 1)
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch("xiaoke_bot.routine.local_now", return_value=datetime(2026, 9, 21, 10)) as clock:
            async def cross_bedtime(*args, **kwargs):
                clock.return_value = datetime(2026, 9, 21, 23, 59)
                return "这条回复不该发送"
            self.plugin.chat_client.complete.side_effect = cross_bedtime
            await self.plugin.handle_message(AsyncMock(), self.event("小可，聊聊天"))
        self.plugin.chat_client.complete.assert_awaited_once()
        self.sent.assert_not_awaited()

    async def test_silent_group_messages_still_reach_memory_observation(self):
        verdict = JevVerdict(0,0,0,0,1,memory_kind="correction")
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(False,verdict,False))):
            await self.plugin.handle_message(AsyncMock(), self.event("我现在改用 Linux 了"))
        self.plugin.intelligence.observe.assert_awaited_once()
        self.plugin.chat_client.complete.assert_not_awaited()
        self.sent.assert_not_awaited()

    async def test_hard_addressed_messages_receive_semantic_judgment(self):
        verdict = JevVerdict(1,1,0,0,1,scene="comfort")
        with patch.object(self.plugin.JevClient,"configured",return_value=True), \
             patch.object(self.plugin.jev_client,"judge",AsyncMock(return_value=verdict)) as judge:
            decision = await self.plugin._decide_response(self.event("小可，今天好累"), "小可，今天好累", self.settings, 100, "test-session")
        self.assertTrue(decision.should_respond)
        self.assertEqual(decision.verdict.scene, "comfort")
        judge.assert_awaited_once()

    async def test_silent_image_is_retained_and_used_in_later_text_followup(self):
        self.plugin.runtime_config.update({"vision_enabled":True,"vision_mode":"direct"})
        picture=self.event("")
        picture.message=Message([MessageSegment.image("https://example.test/qq-image.png")])
        verdict=JevVerdict(0,0,0,0,1)
        image_part={"type":"image_url","image_url":{"url":"data:image/png;base64,test-fixture"}}
        with patch.object(self.plugin,"_decide_response",AsyncMock(return_value=self.plugin.Decision(False,verdict,False))), \
             patch.object(self.plugin.vision_client,"describe",AsyncMock()) as describe:
            await self.plugin.handle_message(AsyncMock(),picture)
            self.plugin.chat_client.complete.assert_not_awaited()
            followup=self.event("小可，刚才那张图有哪里不对？")
            followup.message_id=124
            with patch.object(self.plugin,"_decide_response",AsyncMock(return_value=self.plugin.Decision(True,verdict,False))), \
                 patch.object(self.plugin.vision_client,"_image_part",AsyncMock(return_value=image_part)):
                await self.plugin.handle_message(AsyncMock(),followup)
            describe.assert_not_awaited()
        messages=self.plugin.chat_client.complete.call_args.args[0]
        attached=[item for item in messages if isinstance(item["content"],list)]
        self.assertEqual(len(attached),1)
        self.assertIn("[图片]",attached[0]["content"][0]["text"])
        self.assertEqual(attached[0]["content"][1],image_part)
        self.assertTrue(all(set(item)=={"role","content"} for item in messages))

    async def test_feedback_opens_reply_gate_even_when_continuity_would_close(self):
        settings=replace(self.settings,jev_feedback_enabled=True)
        event=self.event("以后代码问题用文字回答")
        event.to_me=False
        result=JevVerdict(0,0,0,0,1,continuity="closing",feedback_kind="voice")
        with patch.object(self.plugin.JevClient,"configured",return_value=True), \
             patch.object(self.plugin.jev_client,"judge",AsyncMock(return_value=result)):
            decision=await self.plugin._decide_response(event,event.get_plaintext(),settings,100,"test-session")
        self.assertTrue(decision.should_respond)
        self.assertFalse(decision.charge_budget)

    async def test_feedback_persists_and_acknowledges_without_chat_or_voice(self):
        self.plugin.runtime_config.update({"jev_feedback_enabled":True})
        result=JevVerdict(1,1,0,0,1,feedback_kind="voice")
        with patch.object(self.plugin,"_decide_response",AsyncMock(return_value=self.plugin.Decision(True,result,False))), \
             patch.object(self.plugin.jev_client,"classify",AsyncMock(return_value={"scope":"personal","topic":"code","mode":"text"})):
            await self.plugin.handle_message(AsyncMock(),self.event("小可，以后代码问题用文字回答"))
        self.assertEqual((await self.plugin.intelligence.store.rows("preferences"))[0]["topic"],"code")
        self.plugin.chat_client.complete.assert_not_awaited()
        self.plugin.memory_client.capture.assert_not_awaited()
        self.assertIn("已设置",str(self.sent.call_args.args[0]))

    async def test_saved_text_preference_is_applied_before_voice_generation(self):
        self.plugin.runtime_config.update({"jev_feedback_enabled":True,"voice_enabled":True})
        await self.plugin.intelligence.store.save_preference(scope="agent:qq-group-100:group:100",user_id=7,target_user_id=7,
            display_name="测试",text="所有回复用文字",topic="all",mode="text",source_event="rule")
        result=JevVerdict(1,1,0,0,1)
        with patch.object(self.plugin,"_decide_response",AsyncMock(return_value=self.plugin.Decision(True,result,False))), \
             patch.object(self.plugin,"_send_answer",AsyncMock(return_value=("9999", "text"))) as send:
            await self.plugin.handle_message(AsyncMock(),self.event("小可，写段 Python"))
        self.assertFalse(send.call_args.args[2].voice_enabled)
        self.assertEqual(self.plugin._histories["agent:qq-group-100:group:100"][-1]["message_id"],"9999")
        messages = self.plugin.chat_client.complete.call_args.args[0]
        self.assertTrue(any("当前回复对象的所有回复使用文字" in item["content"] for item in messages))

    async def test_voice_awareness_follows_actual_delivery_restart_and_runtime_toggle(self):
        self.plugin.runtime_config.set_voice_api_key("test-voice-key")
        self.plugin.runtime_config.update({"voice_enabled": True,
            "voice_reply_probability": 1, "voice_send_text": False})
        self.plugin.chat_client.complete.return_value = "可以呀，想听我说什么？"
        self.sent.return_value = {"message_id": 9001}
        verdict = JevVerdict(1, 1, 0, 0, 1)
        clip = VoiceClip(b"RIFF-test", "可以呀，想听我说什么？", 1, 1)
        session = "agent:qq-group-100:group:100"
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch.object(self.plugin.voice_client, "generate", AsyncMock(return_value=clip)) as generate:
            await self.plugin.handle_message(AsyncMock(), self.event("小可，你会发语音吗？"))
            messages = self.plugin.chat_client.complete.call_args.args[0]
            self.assertTrue(any("你可以发送 QQ 语音消息" in item["content"] for item in messages))
            self.assertEqual(self.sent.call_args.args[0].type, "record")
            self.assertEqual(self.plugin._histories[session][-1]["delivery"], "voice")
            self.assertEqual(self.plugin._histories[session][-1]["message_id"], "9001")

            await asyncio.gather(*list(self.plugin._background_tasks))
            self.plugin._histories.clear()
            self.plugin._restore_histories()
            generate.side_effect = ServiceError("synthetic voice outage")
            await self.plugin.handle_message(AsyncMock(), self.event("你刚才不是发了语音吗？"))
            messages = self.plugin.chat_client.complete.call_args.args[0]
            self.assertTrue(any("实际发送形式：QQ 语音" in item["content"] for item in messages))
            self.assertEqual(self.plugin._histories[session][-1]["delivery"], "text")
            self.assertEqual(self.sent.call_args.args[0].type, "text")

            self.plugin.runtime_config.update({"voice_enabled": False})
            generate.reset_mock()
            await self.plugin.handle_message(AsyncMock(), self.event("现在能发语音吗？"))
            messages = self.plugin.chat_client.complete.call_args.args[0]
            self.assertTrue(any("QQ 语音回复当前已关闭" in item["content"] for item in messages))
            self.assertTrue(any("实际发送形式：文字" in item["content"] for item in messages))
            generate.assert_not_awaited()

    async def test_voice_delivery_records_text_fallback_when_qq_audio_send_fails(self):
        settings = replace(self.settings, voice_enabled=True, voice_api_key="test-voice-key", voice_send_text=True)
        clip = VoiceClip(b"RIFF-test", "你好", 1, 1)
        with patch.object(self.plugin.voice_client, "generate", AsyncMock(return_value=clip)):
            for fails in (False, True):
                with self.subTest(audio_failure=fails):
                    self.sent.reset_mock()
                    self.sent.side_effect = [{"message_id": 42}, RuntimeError("synthetic QQ failure") if fails else {"message_id": 43}]
                    result = await self.plugin._send_answer("你好", self.event("你好"), settings)
                    self.assertEqual(result, ("42", "text" if fails else "voice_text"))
                    self.assertEqual(self.sent.await_count, 2)

    async def test_knowledge_source_footer_is_sent_even_for_voice_without_text(self):
        async def audio_only(**kwargs):
            await kwargs["send_audio"](b"audio")
            return True
        with patch.object(self.plugin,"maybe_send_voice_reply",audio_only):
            _, delivery = await self.plugin._send_answer("办法",self.event("问题"),self.settings,footer="群内来源 #3：用户7 · 消息 102")
        self.assertEqual(self.sent.await_count,2)
        self.assertIn("消息 102",str(self.sent.call_args.args[0]))
        self.assertEqual(delivery, "voice")

    async def test_search_results_ground_reply_and_keep_sources_in_history_without_typos(self):
        from xiaoke_bot.web_search import SearchContext
        source = {"title": "官方发布说明", "url": "https://example.test/release", "content": "已经发布新版", "published_date": "2026-09-20"}
        self.plugin.runtime_config.update({"search_enabled": True, "typo_enabled": True})
        verdict = JevVerdict(1, 1, 0, 0, 1, search_score=0.95)
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch.object(self.plugin.web_search_client, "context", AsyncMock(return_value=SearchContext("最新版本", [source]))) as search, \
             patch.object(self.plugin.typing_style, "plan", AsyncMock()) as typing:
            await self.plugin.handle_message(AsyncMock(), self.event("小可，查一下最新版本"))
        search.assert_awaited_once()
        self.assertEqual(search.call_args.kwargs["verdict"], verdict)
        self.assertTrue(any(source["url"] in str(row["content"]) and "不可信" in str(row["content"])
                            for row in self.plugin.chat_client.complete.call_args.args[0]))
        self.assertIn(source["url"], "".join(str(call.args[0]) for call in self.sent.await_args_list))
        last = self.plugin._histories[self.plugin._session_key(self.event(""))][-1]
        self.assertIn(source["url"], last["content"])
        typing.assert_not_awaited()

    async def test_typo_and_correction_reach_history_but_not_long_term_memory(self):
        from xiaoke_bot.typing_style import TypingPlan
        answer, displayed = "确实挺有意思的哈哈哈", "确是挺有意思的哈哈哈"
        self.plugin.runtime_config.update({"typo_enabled": True, "jev_memory_enabled": False})
        self.plugin.chat_client.complete.return_value = answer
        verdict = JevVerdict(1, 1, 0, 0, 1)
        with patch.object(self.plugin, "_decide_response", AsyncMock(return_value=self.plugin.Decision(True, verdict, False))), \
             patch.object(self.plugin.typing_style, "plan", AsyncMock(return_value=TypingPlan(displayed, "确实，打快了", True))), \
             patch.object(self.plugin, "apply_segment_delay", AsyncMock()) as delay:
            await self.plugin.handle_message(AsyncMock(), self.event("小可，哈哈是吧"))
        self.assertEqual([str(call.args[0]) for call in self.sent.await_args_list], [displayed, "确实，打快了"])
        delay.assert_awaited_once()
        last = self.plugin._histories[self.plugin._session_key(self.event(""))][-1]
        self.assertEqual(last["content"], displayed + "\n确实，打快了")
        await asyncio.gather(*list(self.plugin._background_tasks))
        self.assertEqual(self.plugin.memory_client.capture.call_args.kwargs["assistant_content"], answer)

    async def test_voice_receives_correct_text_and_only_text_delivery_can_use_typo(self):
        from xiaoke_bot.typing_style import TypingPlan
        answer, displayed = "确实挺有意思的哈哈哈", "确是挺有意思的哈哈哈"
        settings = replace(self.settings, typo_enabled=True, voice_enabled=True, voice_api_key="test-voice-key", voice_reply_probability=1, voice_send_text=False)
        with patch.object(self.plugin.voice_client, "generate", AsyncMock(return_value=VoiceClip(b"RIFF-test", answer, 1, 1))) as generate, \
             patch.object(self.plugin.typing_style, "plan", AsyncMock(return_value=TypingPlan(displayed, changed=True))) as typing:
            context = {"scope": "g:100", "query": "闲聊", "recent": []}
            _, delivery = await self.plugin._send_answer(answer, self.event("闲聊"), settings, raw_text=answer, typing_context=context)
            self.assertEqual(delivery, "voice")
            self.assertEqual(generate.call_args.args[0], answer)
            typing.assert_not_awaited()
            generate.side_effect = ServiceError("voice unavailable")
            _, delivery = await self.plugin._send_answer(answer, self.event("闲聊"), settings, raw_text=answer, typing_context=context)
            self.assertEqual(delivery, "text")
            typing.assert_awaited_once()
            self.assertEqual(str(self.sent.call_args.args[0]), displayed)

    async def test_source_url_remains_clickable_across_message_size_boundary(self):
        url = "https://example.test/" + "release" * 20
        with patch.object(self.plugin, "apply_segment_delay", AsyncMock()):
            await self.plugin._send_text_answer("联网检索来源：\n" + url + "\n其他内容", self.event("查一下"), replace(self.settings, max_reply_chars=50))
        self.assertIn(url, [str(call.args[0]) for call in self.sent.await_args_list])


if __name__ == "__main__":
    unittest.main()
