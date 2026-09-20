from __future__ import annotations

import re

# Hidden marker the model appends when the incoming message genuinely offends the bot.
# It is stripped from the reply before sending, like a think-block.
_OFFENSE_MARKER = re.compile(r"\[\[\s*OFFENSE\s*\]\]", re.IGNORECASE)


def build_offense_prompt(criteria: str) -> str:
    """System instruction telling the model to flag an offending message with a hidden marker."""
    rule = str(criteria or "").strip() or "对方对你进行人身攻击、辱骂、恶意骚扰或严重冒犯"
    return (
        "## 冒犯判定\n"
        f"判定标准：{rule}。\n"
        "如果本轮最后一条用户消息符合上述标准，请在你回复的最末尾另起一行、单独输出标记 "
        "[[OFFENSE]]（该标记会被系统移除，用户看不到）；不符合就绝对不要输出它。"
        "只针对明确的恶意冒犯——对方只是反对、玩笑、正常争论或情绪化用词时，不要判定为冒犯。"
    )


def detect_offense(reply: str) -> tuple[bool, str]:
    """Return (was_offended, reply_without_marker). Marker match is case-insensitive."""
    text = reply or ""
    if not _OFFENSE_MARKER.search(text):
        return False, reply
    cleaned = _OFFENSE_MARKER.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return True, cleaned
