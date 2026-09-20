from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_runtime_config import BASE
from xiaoke_bot.judge import JevClient
from xiaoke_bot.request_log import RequestLogStore
from xiaoke_bot.typing_style import TypingPlan, TypingStyle

SETTINGS = replace(BASE, typo_enabled=True, typo_probability=0.04, typo_cooldown_minutes=30, jev_enabled=True, jev_log_enabled=True)
TEXT = "确实挺有意思的哈哈哈"


class TypingStyleTests(unittest.IsolatedAsyncioTestCase):
    def warm(self, style, scope="g:1"):
        for _ in range(5):
            style.sent(scope, TypingPlan(TEXT), now=10000)

    async def test_requires_clean_replies_probability_and_jev_approval(self):
        style, jev = TypingStyle(), SimpleNamespace(judge_typing=AsyncMock(return_value=True))
        async def plan():
            return await style.plan(TEXT, scope="g:1", query="哈哈是吧", recent=[], settings=SETTINGS, jev=jev, now=10000)
        with patch("xiaoke_bot.typing_style.random.random", return_value=0):
            self.assertFalse((await plan()).changed)
        self.warm(style)
        with patch("xiaoke_bot.typing_style.random.random", return_value=0.5):
            self.assertFalse((await plan()).changed)
        jev.judge_typing.assert_not_awaited()
        jev.judge_typing.return_value = False
        with patch("xiaoke_bot.typing_style.random.random", return_value=0):
            self.assertFalse((await plan()).changed)
        jev.judge_typing.return_value = True
        with patch("xiaoke_bot.typing_style.random.random", return_value=0):
            result = await plan()
        self.assertEqual(result.text, "确是挺有意思的哈哈哈")
        self.assertEqual(result.correction, "确实，打快了")
        self.assertEqual(sum(a != b for a, b in zip(TEXT, result.text)), 1)

    async def test_protected_content_never_reaches_typo_judge(self):
        style, jev = TypingStyle(), SimpleNamespace(judge_typing=AsyncMock(return_value=True))
        self.warm(style)
        for text, query in ((TEXT + " https://example.test", ""), (TEXT + "，明天9点", ""), (TEXT, "帮我排查 Nginx"),
            (TEXT + '，他说“确实”', ""), (TEXT + " `print()`", ""), (TEXT, "认真回答，别打错"), (TEXT + "，两百元", "")):
            with patch("xiaoke_bot.typing_style.random.random", return_value=0):
                result = await style.plan(text, scope="g:1", query=query, recent=[], settings=SETTINGS, jev=jev, now=10000)
            self.assertEqual(result.text, text)
            self.assertFalse(result.changed)
        jev.judge_typing.assert_not_awaited()

    async def test_cooldown_five_clean_replies_and_same_word_suppression(self):
        style, jev = TypingStyle(), SimpleNamespace(judge_typing=AsyncMock(return_value=True))
        self.warm(style)
        with patch("xiaoke_bot.typing_style.random.random", return_value=0):
            result = await style.plan(TEXT, scope="g:1", query="聊天", recent=[], settings=SETTINGS, jev=jev, now=10000)
            style.sent("g:1", result, now=10000)
            self.warm(style)
            for text, now in (("好像真的挺好玩的哈哈", 10001), (TEXT, 12000)):
                self.assertFalse((await style.plan(text, scope="g:1", query="聊天", recent=[], settings=SETTINGS, jev=jev, now=now)).changed)
            self.assertTrue((await style.plan("好像真的挺好玩的哈哈", scope="g:1", query="聊天", recent=[], settings=SETTINGS, jev=jev, now=12000)).changed)
        self.assertEqual(jev.judge_typing.await_count, 2)

    async def test_switches_and_absent_candidates_do_not_invent_errors(self):
        style, jev = TypingStyle(), SimpleNamespace(judge_typing=AsyncMock(return_value=True))
        self.warm(style)
        for settings, text in ((replace(SETTINGS, typo_enabled=False), TEXT), (replace(SETTINGS, jev_enabled=False), TEXT),
            (replace(SETTINGS, typo_probability=0), TEXT), (SETTINGS, "咱们就坐在这聊会儿吧")):
            with patch("xiaoke_bot.typing_style.random.random", return_value=0):
                self.assertFalse((await style.plan(text, scope="g:1", query="聊天", recent=[], settings=settings, jev=jev, now=10000)).changed)
        jev.judge_typing.assert_not_awaited()

    async def test_jev_final_reply_gate_is_conservative_and_logged(self):
        store = RequestLogStore()
        client, sdk = JevClient(store), AsyncMock()
        with patch.object(client, "_ensure", AsyncMock(return_value=sdk)):
            for score, expected in ((0.89, False), (0.9, True), (1.0, True), (float("nan"), False), (float("inf"), False)):
                sdk.system_one.return_value = SimpleNamespace(answers={"casual_typing": SimpleNamespace(noul=score)})
                result = await client.judge_typing(text=TEXT, query="聊天", recent=[], settings=SETTINGS)
                self.assertEqual(result, expected)
            sdk.system_one.side_effect = RuntimeError("unavailable")
            self.assertFalse(await client.judge_typing(text=TEXT, query="聊天", recent=[], settings=SETTINGS))
        self.assertTrue(all(row["feature"] == "typing_judge" for row in store.recent()["items"]))
        self.assertEqual(store.recent()["items"][0]["status"], "error")
