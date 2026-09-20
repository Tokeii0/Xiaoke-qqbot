from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent

from .clients import ChatClient
from .request_log import call_scope
from .humanize_state import BotStateStore, decay_favorability

if TYPE_CHECKING:
    from .config import RuntimeConfigStore, Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, limit: int = 8) -> list[str]:
    raw = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    for item in raw:
        text = _bounded_text(item, 50)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def parse_member_analysis(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("画像模型没有返回 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("画像模型返回的 JSON 无法解析") from exc
    if not isinstance(data, dict):
        raise ValueError("画像模型返回格式不正确")
    try:
        delta = float(data.get("favorability_delta", 0))
    except (TypeError, ValueError):
        delta = 0.0
    return {
        "profile_summary": _bounded_text(data.get("profile_summary"), 1000),
        "personality_traits": _string_list(data.get("personality_traits")),
        "interests": _string_list(data.get("interests")),
        "communication_style": _bounded_text(data.get("communication_style"), 500),
        "interaction_advice": _bounded_text(data.get("interaction_advice"), 500),
        "favorability_delta": max(-5.0, min(5.0, delta)),
        "favorability_reason": _bounded_text(data.get("favorability_reason"), 300),
    }


class MemberProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS member_profiles (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    favorability REAL NOT NULL DEFAULT 50,
                    profile_summary TEXT NOT NULL DEFAULT '',
                    personality_traits TEXT NOT NULL DEFAULT '[]',
                    interests TEXT NOT NULL DEFAULT '[]',
                    communication_style TEXT NOT NULL DEFAULT '',
                    interaction_advice TEXT NOT NULL DEFAULT '',
                    favorability_reason TEXT NOT NULL DEFAULT '',
                    admin_note TEXT NOT NULL DEFAULT '',
                    bot_nickname TEXT NOT NULL DEFAULT '',
                    offense_count INTEGER NOT NULL DEFAULT 0,
                    last_offense_at TEXT,
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    analysis_error TEXT NOT NULL DEFAULT '',
                    analyzed_message_count INTEGER NOT NULL DEFAULT 0,
                    last_analyzed_message_id INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_analyzed_at TEXT,
                    PRIMARY KEY (group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS member_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_member_messages_owner
                    ON member_messages(group_id, user_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_member_profiles_seen
                    ON member_profiles(group_id, last_seen_at DESC);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(member_profiles)")}
            if "bot_nickname" not in columns:
                connection.execute(
                    "ALTER TABLE member_profiles ADD COLUMN bot_nickname TEXT NOT NULL DEFAULT ''"
                )
            if "offense_count" not in columns:
                connection.execute(
                    "ALTER TABLE member_profiles ADD COLUMN offense_count INTEGER NOT NULL DEFAULT 0"
                )
            if "last_offense_at" not in columns:
                connection.execute("ALTER TABLE member_profiles ADD COLUMN last_offense_at TEXT")

    @staticmethod
    def _profile(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("personality_traits", "interests"):
            try:
                parsed = json.loads(result.get(key) or "[]")
            except json.JSONDecodeError:
                parsed = []
            result[key] = parsed if isinstance(parsed, list) else []
        result["favorability"] = round(float(result["favorability"]), 1)
        return result

    async def record_message(
        self,
        *,
        group_id: int,
        user_id: int,
        display_name: str,
        content: str,
        retention: int,
        min_messages: int,
        interval_messages: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._record_message,
            group_id,
            user_id,
            display_name,
            content,
            retention,
            min_messages,
            interval_messages,
        )

    def _record_message(
        self,
        group_id: int,
        user_id: int,
        display_name: str,
        content: str,
        retention: int,
        min_messages: int,
        interval_messages: int,
    ) -> bool:
        timestamp = _now()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO member_profiles (
                    group_id, user_id, display_name, message_count, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    message_count = member_profiles.message_count + 1,
                    last_seen_at = excluded.last_seen_at
                """,
                (group_id, user_id, display_name, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO member_messages(group_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
                (group_id, user_id, content[:1000], timestamp),
            )
            connection.execute(
                """
                DELETE FROM member_messages
                WHERE group_id = ? AND user_id = ? AND id NOT IN (
                    SELECT id FROM member_messages
                    WHERE group_id = ? AND user_id = ?
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (group_id, user_id, group_id, user_id, retention),
            )
            row = connection.execute(
                """
                SELECT message_count, analyzed_message_count, analysis_status
                FROM member_profiles WHERE group_id = ? AND user_id = ?
                """,
                (group_id, user_id),
            ).fetchone()
        if row is None or row["analysis_status"] == "processing":
            return False
        count = int(row["message_count"])
        analyzed = int(row["analyzed_message_count"])
        return count >= min_messages and count - analyzed >= interval_messages

    async def list_members(
        self,
        *,
        group_id: int | None = None,
        query: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_members, group_id, query, limit)

    def _list_members(
        self,
        group_id: int | None,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if group_id is not None:
            clauses.append("group_id = ?")
            parameters.append(group_id)
        if query.strip():
            clauses.append("(display_name LIKE ? OR CAST(user_id AS TEXT) LIKE ?)")
            pattern = f"%{query.strip()}%"
            parameters.extend((pattern, pattern))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(500, limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM member_profiles {where} ORDER BY last_seen_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._profile(row) for row in rows]

    async def get_member(
        self,
        group_id: int,
        user_id: int,
        message_limit: int = 20,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_member, group_id, user_id, message_limit)

    def _get_member(
        self,
        group_id: int,
        user_id: int,
        message_limit: int,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM member_profiles WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ).fetchone()
            if row is None:
                return None
            messages = connection.execute(
                """
                SELECT id, content, created_at FROM member_messages
                WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?
                """,
                (group_id, user_id, max(1, min(100, message_limit))),
            ).fetchall()
        result = self._profile(row)
        result["recent_messages"] = [dict(message) for message in messages]
        return result

    async def analysis_input(
        self,
        group_id: int,
        user_id: int,
        sample_limit: int,
        force: bool,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._analysis_input,
            group_id,
            user_id,
            sample_limit,
            force,
        )

    def _analysis_input(
        self,
        group_id: int,
        user_id: int,
        sample_limit: int,
        force: bool,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            profile = connection.execute(
                "SELECT * FROM member_profiles WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ).fetchone()
            if profile is None:
                return None
            last_id = int(profile["last_analyzed_message_id"])
            messages = connection.execute(
                """
                SELECT id, content, created_at FROM member_messages
                WHERE group_id = ? AND user_id = ? AND id > ?
                ORDER BY id DESC LIMIT ?
                """,
                (group_id, user_id, last_id, sample_limit),
            ).fetchall()
            has_new_messages = bool(messages)
            if force and not messages:
                messages = connection.execute(
                    """
                    SELECT id, content, created_at FROM member_messages
                    WHERE group_id = ? AND user_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (group_id, user_id, sample_limit),
                ).fetchall()
            if not messages:
                return None
            connection.execute(
                """
                UPDATE member_profiles SET analysis_status = 'processing', analysis_error = ''
                WHERE group_id = ? AND user_id = ?
                """,
                (group_id, user_id),
            )
        result = self._profile(profile)
        result["messages"] = [dict(message) for message in reversed(messages)]
        result["max_message_id"] = max(int(message["id"]) for message in messages)
        result["has_new_messages"] = has_new_messages
        return result

    async def save_analysis(
        self,
        group_id: int,
        user_id: int,
        analysis: dict[str, Any],
        max_message_id: int,
        apply_favorability_delta: bool,
    ) -> None:
        await asyncio.to_thread(
            self._save_analysis,
            group_id,
            user_id,
            analysis,
            max_message_id,
            apply_favorability_delta,
        )

    def _save_analysis(
        self,
        group_id: int,
        user_id: int,
        analysis: dict[str, Any],
        max_message_id: int,
        apply_favorability_delta: bool,
    ) -> None:
        delta = float(analysis["favorability_delta"]) if apply_favorability_delta else 0.0
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE member_profiles SET
                    favorability = MIN(100, MAX(0, favorability + ?)),
                    profile_summary = ?, personality_traits = ?, interests = ?,
                    communication_style = ?, interaction_advice = ?,
                    favorability_reason = ?, analysis_status = 'ready', analysis_error = '',
                    analyzed_message_count = message_count,
                    last_analyzed_message_id = MAX(last_analyzed_message_id, ?),
                    last_analyzed_at = ?
                WHERE group_id = ? AND user_id = ?
                """,
                (
                    delta,
                    analysis["profile_summary"],
                    json.dumps(analysis["personality_traits"], ensure_ascii=False),
                    json.dumps(analysis["interests"], ensure_ascii=False),
                    analysis["communication_style"],
                    analysis["interaction_advice"],
                    analysis["favorability_reason"],
                    max_message_id,
                    _now(),
                    group_id,
                    user_id,
                ),
            )

    async def mark_analysis_failed(self, group_id: int, user_id: int, error: str) -> None:
        await asyncio.to_thread(self._mark_analysis_failed, group_id, user_id, error)

    def _mark_analysis_failed(self, group_id: int, user_id: int, error: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE member_profiles SET analysis_status = 'failed', analysis_error = ?
                WHERE group_id = ? AND user_id = ?
                """,
                (error[:500], group_id, user_id),
            )

    async def update_member(
        self,
        group_id: int,
        user_id: int,
        favorability: float,
        admin_note: str,
        bot_nickname: str = "",
        offense_count: int | None = None,
    ) -> dict[str, Any] | None:
        await asyncio.to_thread(
            self._update_member,
            group_id,
            user_id,
            favorability,
            admin_note,
            bot_nickname,
            offense_count,
        )
        return await self.get_member(group_id, user_id)

    def _update_member(
        self,
        group_id: int,
        user_id: int,
        favorability: float,
        admin_note: str,
        bot_nickname: str,
        offense_count: int | None,
    ) -> None:
        columns = ["favorability = ?", "admin_note = ?", "bot_nickname = ?"]
        params: list[Any] = [
            max(0.0, min(100.0, favorability)),
            admin_note[:2000],
            bot_nickname.strip()[:50],
        ]
        if offense_count is not None:
            columns.append("offense_count = ?")
            params.append(max(0, int(offense_count)))
        params.extend((group_id, user_id))
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE member_profiles SET {', '.join(columns)} WHERE group_id = ? AND user_id = ?",
                params,
            )

    async def delete_member(self, group_id: int, user_id: int) -> bool:
        return await asyncio.to_thread(self._delete_member, group_id, user_id)

    def _delete_member(self, group_id: int, user_id: int) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM member_messages WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
            cursor = connection.execute(
                "DELETE FROM member_profiles WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
        return cursor.rowcount > 0

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats)

    def _stats(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS members, COALESCE(SUM(message_count), 0) AS messages,
                       SUM(CASE WHEN analysis_status = 'ready' THEN 1 ELSE 0 END) AS analyzed
                FROM member_profiles
                """
            ).fetchone()
        return {
            "members": int(row["members"] or 0),
            "messages": int(row["messages"] or 0),
            "analyzed": int(row["analyzed"] or 0),
        }

    async def get_profile(self, group_id: int, user_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_profile, group_id, user_id)

    def _get_profile(self, group_id: int, user_id: int) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM member_profiles WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ).fetchone()
        return self._profile(row) if row is not None else None

    async def get_profile_decayed(
        self, group_id: int, user_id: int, half_life_days: float
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._get_profile_decayed, group_id, user_id, half_life_days
        )

    def _get_profile_decayed(
        self, group_id: int, user_id: int, half_life_days: float
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM member_profiles WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ).fetchone()
            if row is None:
                return None
            profile = self._profile(row)
            seen = _parse_timestamp(profile.get("last_seen_at"))
            if seen is not None:
                elapsed = max(0.0, (datetime.now(timezone.utc) - seen).total_seconds())
                decayed = round(
                    decay_favorability(profile["favorability"], elapsed, half_life_days), 1
                )
                if abs(decayed - profile["favorability"]) > 0.5:
                    connection.execute(
                        "UPDATE member_profiles SET favorability = ? WHERE group_id = ? AND user_id = ?",
                        (decayed, group_id, user_id),
                    )
                profile["favorability"] = decayed
            return profile

    async def flag_offense(self, group_id: int, user_id: int) -> int:
        return await asyncio.to_thread(self._flag_offense, group_id, user_id)

    def _flag_offense(self, group_id: int, user_id: int) -> int:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE member_profiles
                SET offense_count = offense_count + 1, last_offense_at = ?
                WHERE group_id = ? AND user_id = ?
                """,
                (_now(), group_id, user_id),
            )
            if cursor.rowcount == 0:
                return 0
            row = connection.execute(
                "SELECT offense_count FROM member_profiles WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            ).fetchone()
        return int(row["offense_count"]) if row else 0


class MemberAnalysisService:
    def __init__(
        self,
        store: MemberProfileStore,
        config_store: "RuntimeConfigStore",
        chat_client: ChatClient,
        state_store: BotStateStore | None = None,
    ) -> None:
        self.store = store
        self.config_store = config_store
        self.chat_client = chat_client
        self.state_store = state_store
        self._tasks: set[asyncio.Task[None]] = set()
        self._inflight: set[tuple[int, int]] = set()

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)

        def done(completed: asyncio.Task[None]) -> None:
            self._tasks.discard(completed)
            try:
                completed.result()
            except Exception:
                logger.exception("群员画像后台任务失败")

        task.add_done_callback(done)

    async def record(
        self,
        *,
        group_id: int,
        user_id: int,
        display_name: str,
        content: str,
    ) -> None:
        settings = self.config_store.snapshot()
        if not settings.member_analysis_enabled:
            return
        should_analyze = await self.store.record_message(
            group_id=group_id,
            user_id=user_id,
            display_name=display_name,
            content=content,
            retention=settings.member_message_retention,
            min_messages=settings.member_analysis_min_messages,
            interval_messages=settings.member_analysis_interval_messages,
        )
        key = (group_id, user_id)
        if settings.member_analysis_auto and should_analyze and key not in self._inflight:
            self._track(asyncio.create_task(self._auto_analyze(group_id, user_id)))

    async def _auto_analyze(self, group_id: int, user_id: int) -> None:
        try:
            await self.analyze(group_id, user_id)
        except ValueError:
            # 后台自动分析的可预期情况（并发已在分析、暂无新样本、模型未配置、解析失败已入库）
            # 忽略即可，避免噪音错误日志；真正的失败仍会写入 analysis_status='failed'。
            logger.debug(f"跳过自动群员画像：group={group_id}, user={user_id}")

    async def analyze(self, group_id: int, user_id: int, force: bool = False) -> dict[str, Any]:
        key = (group_id, user_id)
        if key in self._inflight:
            raise ValueError("该群员的画像正在分析中")
        settings = self.config_store.snapshot()
        if not settings.chat_configured:
            raise ValueError("尚未配置可用的对话模型")
        self._inflight.add(key)
        try:
            source = await self.store.analysis_input(
                group_id,
                user_id,
                settings.member_analysis_sample_limit,
                force,
            )
            if source is None:
                raise ValueError("没有可用于分析的新消息")
            message_lines = "\n".join(
                f"- {item['content']}" for item in source["messages"]
            )
            system = (
                "你是QQ群聊成员画像分析器。只根据公开群聊文字做保守、可修正的互动画像，"
                "不得推断疾病、政治立场、宗教、性取向、精确年龄或其他敏感属性。"
                "输出必须是单个 JSON 对象，字段为：profile_summary（简短概括）、"
                "personality_traits（字符串数组）、interests（字符串数组）、"
                "communication_style、interaction_advice、favorability_delta（-5到5）、"
                "favorability_reason。好感度表示该成员与机器人互动的友好程度；证据不足时增量为0。"
            )
            user = (
                f"群号：{group_id}\nQQ：{user_id}\n昵称：{source['display_name']}\n"
                f"当前画像：{source['profile_summary'] or '尚无'}\n"
                f"当前好感度：{source['favorability']}\n"
                f"本次聊天样本：\n{message_lines}"
            )
            analysis_settings = replace(
                settings,
                temperature=min(settings.temperature, 0.3),
                max_tokens=min(settings.max_tokens, 1200),
                response_format="text",
            )
            with call_scope("member_profile", group_id=group_id, user_id=user_id):
                content = await self.chat_client.complete(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    analysis_settings, feature="member_profile",
                )
            analysis = parse_member_analysis(content)
            applied = bool(source["has_new_messages"])
            await self.store.save_analysis(
                group_id,
                user_id,
                analysis,
                int(source["max_message_id"]),
                applied,
            )
            if applied and self.state_store is not None and settings.mood_event_nudges_enabled:
                delta = float(analysis["favorability_delta"])
                if delta:
                    await self.state_store.nudge_mood(
                        group_id,
                        max(-0.4, min(0.4, delta / 12.0)),
                        min(0.3, abs(delta) / 30.0),
                        settings.mood_half_life_hours,
                    )
            member = await self.store.get_member(group_id, user_id)
            if member is None:
                raise ValueError("群员画像已不存在")
            return member
        except Exception as exc:
            await self.store.mark_analysis_failed(group_id, user_id, str(exc))
            raise
        finally:
            self._inflight.discard(key)

    async def shutdown(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def register_member_analysis(
    config_store: "RuntimeConfigStore",
    service: MemberAnalysisService,
) -> None:
    matcher = on_message(priority=10, block=False)

    @matcher.handle()
    async def collect_group_message(bot: Bot, event: MessageEvent) -> None:
        if not isinstance(event, GroupMessageEvent) or str(event.user_id) == str(bot.self_id):
            return
        settings = config_store.snapshot()
        if int(event.group_id) not in settings.allowed_groups:
            return
        content = event.get_plaintext().strip()
        if not content:
            return
        sender = getattr(event, "sender", None)
        card = str(getattr(sender, "card", "") or "").strip()
        nickname = str(getattr(sender, "nickname", "") or "").strip()
        await service.record(
            group_id=int(event.group_id),
            user_id=int(event.user_id),
            display_name=card or nickname or str(event.user_id),
            content=content,
        )
