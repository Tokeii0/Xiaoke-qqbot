"""Rare, bounded text-only slips, never random corruption of generated facts."""
from __future__ import annotations

import random
import re
import time
from collections import OrderedDict
from dataclasses import dataclass


# One common homophone/shape mistake in a conversational word; no free-form character mutation.
_PAIRS = (("好像", "好象"), ("确实", "确是"), ("已经", "己经"), ("稍微", "稍为"),
          ("毕竟", "毕竞"), ("反正", "反证"), ("刚才", "刚材"))
_PROTECTED = re.compile(r"[A-Za-z0-9`@#\n\r\[\]{}<>《》「」『』“”\"]|https?://|[零〇一二三四五六七八九十百千万]+(?:元|点|分|号|日|月|年|次|个)|"
                        r"代码|命令|报错|版本|参数|密码|地址|提醒|取消|来源|诊断|用药|合同|转账|身份证|别打错|不要打错|准确|严肃|认真")


@dataclass(frozen=True)
class TypingPlan:
    text: str
    correction: str = ""
    changed: bool = False


class TypingStyle:
    def __init__(self):
        self._states = OrderedDict()

    def _state(self, scope):
        if scope not in self._states:
            self._states[scope] = {"last_at": 0.0, "clean": 0, "last_word": ""}
            if len(self._states) > 1024:
                self._states.popitem(last=False)
        self._states.move_to_end(scope)
        return self._states[scope]

    async def plan(self, text, *, scope, query, recent, settings, jev, now=None):
        unchanged = TypingPlan(text)
        if not settings.typo_enabled or not settings.jev_enabled or not 8 <= len(text) <= 120 or _PROTECTED.search(text + query):
            return unchanged
        state = self._state(scope)
        moment = time.time() if now is None else now
        if state["clean"] < 5 or moment - state["last_at"] < settings.typo_cooldown_minutes * 60:
            return unchanged
        candidates = [(a, b) for a, b in _PAIRS if a in text and a != state["last_word"]]
        if not candidates or random.random() >= settings.typo_probability:
            return unchanged
        # Ask about the final answer only after probability, content and cooldown filters pass.
        if not await jev.judge_typing(text=text, query=query, recent=recent, settings=settings):
            return unchanged
        correct, typo = random.choice(candidates)
        correction = f"{correct}，打快了" if random.random() < 0.25 else ""
        return TypingPlan(text.replace(correct, typo, 1), correction, True)

    def sent(self, scope, plan, *, now=None):
        state = self._state(scope)
        if plan.changed:
            state.update(last_at=time.time() if now is None else now, clean=0,
                         last_word=next((a for a, b in _PAIRS if b in plan.text), ""))
        else:
            state["clean"] += 1
