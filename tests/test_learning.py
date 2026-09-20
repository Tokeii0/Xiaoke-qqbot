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
from xiaoke_bot.intelligence_store import IntelligenceStore, utc_stamp
from xiaoke_bot.judge import JevVerdict
from xiaoke_bot.learning import LearningService, knowledge_sources

SCOPE = "agent:qq-group-100:group:100"
SETTINGS = replace(BASE,jev_enabled=True,jev_knowledge_enabled=True,jev_feedback_enabled=True)
NOW = datetime.now(timezone.utc)


def turn(message_id, text, user_id=7, bot=False):
    return {"message_id":str(message_id),"text":text,"speaker":"小可" if bot else f"用户{user_id}",
            "user_id":None if bot else user_id,"is_bot":bot,"created_at":utc_stamp(NOW)}


class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = IntelligenceStore(Path(self.folder.name)/"intelligence.db")
        self.jev = SimpleNamespace(classify=AsyncMock())
        self.service = LearningService(self.store,self.jev)
        self.recent = [turn(101,"Nginx 代理接口一直返回 504，怎么解决？"),
                       turn(102,"把 proxy_read_timeout 设置为 60s 后重载 Nginx。",bot=True)]
        self.current = turn(103,"按你这个超时设置改了，现在接口正常，问题解决了。")
        async def choose(*,questions,**kwargs):
            if "problem" in questions:
                return {"problem":"m0",**{key:"keep" if key == "solution_m1" else "drop" for key in questions if key.startswith("solution_")}}
            return {key:"different" for key in questions}
        self.jev.classify.side_effect = choose

    async def observe(self, result=None, **kwargs):
        params = dict(scope=SCOPE,user_id=7,display_name="用户7",text=self.current["text"],source_event="103",
            recent=self.recent,current=self.current,verdict=result or JevVerdict(0,0,0,0,1,knowledge_action="confirm"),settings=SETTINGS)
        params.update(kwargs)
        await self.service.observe_knowledge(**params)

    async def feedback(self, kind="voice", **kwargs):
        params=dict(kind=kind,scope=SCOPE,user_id=7,group_id=100,display_name="用户7",query="以后代码问题用文字回答",
                    source_event="f1",recent=self.recent,settings=SETTINGS)
        params.update(kwargs)
        return await self.service.feedback(**params)

    def answers(self, value):
        self.jev.classify.side_effect = None
        self.jev.classify.return_value = value

    async def test_confirmed_solution_keeps_exact_sources_and_survives_restart(self):
        await self.observe()
        rows = await IntelligenceStore(self.store.path).rows("knowledge",scope=SCOPE)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["text"],self.recent[1]["text"])
        self.assertEqual([source["message_id"] for source in rows[0]["sources"]],["101","102","103"])
        self.assertIn("消息 102",knowledge_sources(rows,"Asia/Taipei"))

    async def test_unconfirmed_or_feedback_messages_never_become_knowledge(self):
        for result in (JevVerdict(0,0,0,0,1),JevVerdict(0,0,0,0,1,knowledge_action="confirm",feedback_kind="forget")):
            await self.observe(result)
        self.assertEqual(await self.store.rows("knowledge"),[])
        self.jev.classify.assert_not_awaited()

    async def test_uncertain_selection_and_unknown_source_do_not_create_knowledge(self):
        for response in (None,{}, {"problem":"m0","solution_fake":"keep"}):
            self.answers(response)
            await self.observe()
        self.assertEqual(await self.store.rows("knowledge"),[])

    async def test_confirmation_cannot_be_attached_to_someone_elses_problem(self):
        await self.observe(user_id=8,current=turn(103,"我看起来觉得应该好了",user_id=8))
        self.assertEqual(await self.store.rows("knowledge"),[])

    async def test_duplicate_source_event_is_idempotent(self):
        await self.observe()
        await self.observe()
        self.assertEqual(len(await self.store.rows("knowledge")),1)

    async def test_new_confirmation_refreshes_expired_knowledge_with_new_sources(self):
        await self.observe()
        with self.store.connection() as db:
            db.execute("UPDATE knowledge SET created_at=?",(utc_stamp(NOW-timedelta(days=91)),))
        original=self.jev.classify.side_effect
        async def choose(*,questions,**kwargs):
            return await original(questions=questions,**kwargs) if "problem" in questions else {"1":"duplicate"}
        self.jev.classify.side_effect=choose
        fresh=turn(105,"今天再次验证这个具体超时设置仍然能解决 504。")
        await self.observe(current=fresh,source_event="105")
        rows=await self.store.rows("knowledge")
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["sources"][-1]["message_id"],"105")
        self.assertEqual(rows[1]["status"],"superseded")

    async def test_expired_foreign_and_outdated_solutions_are_not_recalled(self):
        await self.observe()
        self.answers({"1":"use"})
        self.assertEqual(len(await self.service.recall_knowledge(scope=SCOPE,query="504",settings=SETTINGS,now=NOW)),1)
        self.assertEqual(await self.service.recall_knowledge(scope="other",query="504",settings=SETTINGS,now=NOW),[])
        self.assertEqual(await self.service.recall_knowledge(scope=SCOPE,query="504",settings=SETTINGS,now=NOW+timedelta(days=91)),[])
        await self.store.close_record("knowledge",1)
        self.assertEqual(await self.service.recall_knowledge(scope=SCOPE,query="504",settings=SETTINGS,now=NOW),[])

    async def test_recall_uncertainty_and_version_mismatch_skip_old_advice(self):
        await self.observe()
        for response in (None,{}, {"1":"skip"}):
            self.answers(response)
            self.assertEqual(await self.service.recall_knowledge(scope=SCOPE,query="已经更换版本了",settings=SETTINGS),[])

    async def test_only_source_owner_or_manager_can_retire_knowledge(self):
        await self.observe()
        self.answers({"outdated":"1"})
        result=JevVerdict(0,0,0,0,1,knowledge_action="outdated")
        await self.observe(result,user_id=8,manager=False)
        self.assertEqual((await self.store.rows("knowledge"))[0]["status"],"active")
        await self.observe(result,user_id=8,manager=True)
        self.assertEqual((await self.store.rows("knowledge"))[0]["status"],"outdated")

    async def test_forget_only_targets_own_fact_and_invalidates_dependent_knowledge(self):
        await self.observe()
        mine=await self.store.save_fact(scope=SCOPE,user_id=7,display_name="用户7",text="我的接口有 504",source_event="101")
        other=await self.store.save_fact(scope=SCOPE,user_id=8,display_name="用户8",text="别人自己的事实",source_event="104")
        self.answers({"fact":str(other)})
        self.assertIn("不能确定",await self.feedback("forget"))
        self.answers({"fact":str(mine)})
        self.assertIn("已撤回",await self.feedback("forget"))
        self.assertEqual((await self.store.rows("facts",user_id=8))[0]["status"],"active")
        self.assertEqual((await self.store.rows("knowledge"))[0]["status"],"outdated")

    async def test_member_cannot_change_group_rule_even_when_classifier_selects_group(self):
        self.answers({"scope":"group","topic":"code","mode":"text"})
        self.assertIn("需要群主",await self.feedback(manager=False))
        self.assertEqual(await self.store.rows("preferences"),[])

    async def test_manager_group_rule_applies_to_members_but_not_other_groups(self):
        self.answers({"scope":"group","topic":"all","mode":"text"})
        self.assertIn("已设置",await self.feedback(manager=True))
        self.assertTrue(await self.service.text_only(scope=SCOPE,user_id=8,query="你好",reply="你好",settings=SETTINGS))
        self.assertFalse(await self.service.text_only(scope="other",user_id=8,query="你好",reply="你好",settings=SETTINGS))

    async def test_private_chat_cannot_create_group_rule(self):
        self.answers({"scope":"group","topic":"all","mode":"text"})
        await self.feedback(manager=True,group_id=None)
        self.assertEqual(await self.store.rows("preferences"),[])

    async def test_personal_preference_is_owner_scoped_and_topic_judged(self):
        self.answers({"scope":"personal","topic":"code","mode":"text"})
        await self.feedback()
        self.assertFalse(await self.service.text_only(scope=SCOPE,user_id=8,query="写代码",reply="代码",settings=SETTINGS))
        for response, expected in (({"channel":"text"},True),({"channel":"default"},False),(None,True),({},True)):
            self.answers(response)
            self.assertEqual(await self.service.text_only(scope=SCOPE,user_id=7,query="写代码",reply="代码",settings=SETTINGS),expected)

    async def test_reset_personal_preferences_does_not_override_group_rule(self):
        self.answers({"scope":"personal","topic":"code","mode":"text"})
        await self.feedback()
        self.answers({"scope":"group","topic":"all","mode":"text"})
        await self.feedback(manager=True,user_id=99,source_event="group")
        self.answers({"scope":"personal","topic":"all","mode":"auto"})
        await self.feedback(source_event="reset")
        rules=await self.store.preference_rules(SCOPE,7)
        self.assertEqual([row["target_user_id"] for row in rules],[0])

    async def test_preference_replay_cannot_duplicate_or_reactivate_cancelled_rule(self):
        self.answers({"scope":"personal","topic":"all","mode":"text"})
        await asyncio.gather(self.feedback(),self.feedback())
        row=(await self.store.rows("preferences"))[0]
        self.assertEqual(len(await self.store.rows("preferences")),1)
        await self.store.close_record("preferences",row["id"])
        self.assertIn("已处理",await self.feedback())
        self.assertEqual(await self.store.preference_rules(SCOPE,7),[])

    async def test_ambiguous_or_disabled_feedback_does_not_modify_preferences(self):
        for answer in ({},{"scope":"unclear","topic":"code","mode":"text"},None):
            self.answers(answer)
            await self.feedback()
        self.assertEqual(await self.store.rows("preferences"),[])
        self.assertIsNone(await self.feedback(settings=replace(SETTINGS,jev_feedback_enabled=False)))


if __name__ == "__main__":
    unittest.main()
