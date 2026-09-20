from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata

from .config import Settings


class Command(str, Enum):
    HELP = "help"
    STATUS = "status"
    ENABLE = "enable"
    DISABLE = "disable"
    MEMORY = "memory"


@dataclass(frozen=True)
class ParsedCommand:
    command: Command
    argument: str = ""


COMMAND_ALIASES = {
    "帮助": Command.HELP,
    "help": Command.HELP,
    "状态": Command.STATUS,
    "status": Command.STATUS,
    "开启": Command.ENABLE,
    "启用": Command.ENABLE,
    "enable": Command.ENABLE,
    "关闭": Command.DISABLE,
    "停用": Command.DISABLE,
    "disable": Command.DISABLE,
    "记忆": Command.MEMORY,
    "memory": Command.MEMORY,
}


def parse_command(text: str, bot_name: str = "小可") -> ParsedCommand | None:
    stripped = text.strip()
    prefixes = (f"/{bot_name}", f"/{bot_name.lower()}", "/bot")
    matched = next((prefix for prefix in prefixes if stripped.lower().startswith(prefix.lower())), None)
    if matched is None:
        return None
    remainder = stripped[len(matched) :].strip()
    if not remainder:
        return ParsedCommand(Command.HELP)
    name, _, argument = remainder.partition(" ")
    command = COMMAND_ALIASES.get(name.lower())
    return ParsedCommand(command, argument.strip()) if command else ParsedCommand(Command.HELP)


def strip_bot_name(text: str, bot_name: str) -> str:
    """移除消息开头的机器人名称，名称后没有正文时返回空字符串。"""
    value = text.strip()
    if value.startswith(bot_name):
        value = value[len(bot_name) :].lstrip(" ，,:：")
    return value


def contains_trigger_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    normalized_text = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", text).casefold(),
    )
    return any(
        re.sub(r"\s+", "", unicodedata.normalize("NFKC", keyword).casefold())
        in normalized_text
        for keyword in keywords
        if keyword.strip()
    )


def is_superuser(user_id: int, settings: Settings) -> bool:
    return user_id in settings.superusers


def is_allowed_context(*, user_id: int, group_id: int | None, settings: Settings) -> bool:
    if group_id is None:
        return is_superuser(user_id, settings)
    return group_id in settings.allowed_groups


def can_manage(*, user_id: int, sender_role: str, settings: Settings) -> bool:
    return is_superuser(user_id, settings) or sender_role in {"admin", "owner"}
