from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import Rule

if TYPE_CHECKING:
    from .config import RuntimeConfigStore


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", normalized)


def find_blocked_keyword(content: str, keywords: tuple[str, ...]) -> str | None:
    haystack = _compact(content)
    for keyword in keywords:
        needle = _compact(keyword)
        if needle and needle in haystack:
            return keyword
    return None


def register_moderation(config_store: "RuntimeConfigStore") -> None:
    async def should_moderate(event: MessageEvent) -> bool:
        if not isinstance(event, GroupMessageEvent):
            return False
        settings = config_store.snapshot()
        if not settings.moderation_enabled or event.group_id not in settings.allowed_groups:
            return False
        if settings.moderation_exempt_admins:
            role = str(getattr(getattr(event, "sender", None), "role", "member") or "member")
            if int(event.user_id) in settings.superusers or role in {"admin", "owner"}:
                return False
        return find_blocked_keyword(event.get_plaintext(), settings.moderation_keywords) is not None

    matcher = on_message(rule=Rule(should_moderate), priority=2, block=True)

    @matcher.handle()
    async def withdraw_blocked_message(bot: Bot, event: GroupMessageEvent) -> None:
        try:
            await bot.delete_msg(message_id=event.message_id)
            logger.info(
                f"监管插件已撤回消息：group={event.group_id}, user={event.user_id}, message={event.message_id}"
            )
        except Exception:
            logger.exception(
                f"监管插件撤回失败：group={event.group_id}, message={event.message_id}"
            )
