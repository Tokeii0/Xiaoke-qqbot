from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .config import RuntimeConfigStore
    from .state import GroupStateStore


# ---------------------------------------------------------------------------
# Pure helpers (no DB) — unit-tested directly.
# ---------------------------------------------------------------------------

_THANKS = (
    "谢谢", "感谢", "多谢", "谢啦", "谢了", "thx", "thanks", "辛苦", "太棒",
    "厉害", "喜欢你", "可爱", "点赞", "赞美",
)
_HOSTILE = (
    "闭嘴", "滚", "垃圾", "废物", "讨厌", "白痴", "智障", "脑残", "蠢货",
    "傻逼", "煞笔", "sb", "去死", "烦死",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decay_factor(seconds_elapsed: float, half_life_seconds: float) -> float:
    if half_life_seconds <= 0 or seconds_elapsed <= 0:
        return 1.0
    return 0.5 ** (seconds_elapsed / half_life_seconds)


def decay_favorability(
    value: float,
    seconds_elapsed: float,
    half_life_days: float,
    floor: float = 50.0,
) -> float:
    """Relationships fade toward neutral (50) without contact."""
    factor = _decay_factor(seconds_elapsed, half_life_days * 86400.0)
    return max(0.0, min(100.0, floor + (value - floor) * factor))


def decay_mood(
    valence: float,
    arousal: float,
    seconds_elapsed: float,
    half_life_hours: float,
) -> tuple[float, float]:
    """Mood cools toward neutral (0 valence, 0 arousal) over time."""
    factor = _decay_factor(seconds_elapsed, half_life_hours * 3600.0)
    return valence * factor, arousal * factor


def mood_label(valence: float, arousal: float) -> str:
    """Coarse Chinese label for a valence/arousal pair (valence -1..1, arousal 0..1)."""
    if valence >= 0.6 and arousal >= 0.5:
        return "兴奋"
    if valence >= 0.25:
        return "愉快"
    if valence <= -0.5 and arousal >= 0.5:
        return "烦躁"
    if valence <= -0.25:
        return "低落"
    return "平静"


def classify_interaction_event(text: str) -> tuple[float, float] | None:
    """Immediate mood nudge (valence_delta, arousal_delta) from one message, or None."""
    lowered = text.casefold()
    if any(word in lowered for word in _THANKS):
        return (0.25, 0.12)
    if any(word in lowered for word in _HOSTILE):
        return (-0.30, 0.22)
    return None


# ---------------------------------------------------------------------------
# Persistent state: mood + proactive/follow-up ledgers (separate DB).
# ---------------------------------------------------------------------------


class BotStateStore:
    """Bot mood + proactive/follow-up bookkeeping. Mirrors MemberProfileStore's shape."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
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
                CREATE TABLE IF NOT EXISTS bot_mood (
                    group_id INTEGER PRIMARY KEY,
                    valence REAL NOT NULL DEFAULT 0,
                    arousal REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proactive_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proactive_ledger_group
                    ON proactive_ledger(group_id, created_at);
                CREATE TABLE IF NOT EXISTS followup_ledger (
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    topic_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, user_id, topic_key)
                );
                """
            )

    # --- mood -------------------------------------------------------------

    async def read_mood(self, group_id: int, half_life_hours: float) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_mood, group_id, half_life_hours)

    def _read_mood(self, group_id: int, half_life_hours: float) -> dict[str, Any]:
        now = _now()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT valence, arousal, updated_at FROM bot_mood WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if row is None:
                return {"valence": 0.0, "arousal": 0.0, "label": mood_label(0.0, 0.0)}
            updated = _parse_iso(row["updated_at"]) or now
            elapsed = max(0.0, (now - updated).total_seconds())
            valence, arousal = decay_mood(
                float(row["valence"]), float(row["arousal"]), elapsed, half_life_hours
            )
            if abs(valence - float(row["valence"])) > 0.02 or abs(arousal - float(row["arousal"])) > 0.02:
                connection.execute(
                    "UPDATE bot_mood SET valence = ?, arousal = ?, updated_at = ? WHERE group_id = ?",
                    (valence, arousal, _iso(now), group_id),
                )
        return {
            "valence": round(valence, 3),
            "arousal": round(arousal, 3),
            "label": mood_label(valence, arousal),
        }

    async def nudge_mood(
        self,
        group_id: int,
        valence_delta: float,
        arousal_delta: float,
        half_life_hours: float,
    ) -> None:
        await asyncio.to_thread(
            self._nudge_mood, group_id, valence_delta, arousal_delta, half_life_hours
        )

    def _nudge_mood(
        self,
        group_id: int,
        valence_delta: float,
        arousal_delta: float,
        half_life_hours: float,
    ) -> None:
        now = _now()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT valence, arousal, updated_at FROM bot_mood WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            if row is None:
                valence, arousal = 0.0, 0.0
            else:
                updated = _parse_iso(row["updated_at"]) or now
                elapsed = max(0.0, (now - updated).total_seconds())
                valence, arousal = decay_mood(
                    float(row["valence"]), float(row["arousal"]), elapsed, half_life_hours
                )
            valence = max(-1.0, min(1.0, valence + valence_delta))
            arousal = max(0.0, min(1.0, arousal + arousal_delta))
            connection.execute(
                """
                INSERT INTO bot_mood (group_id, valence, arousal, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    valence = excluded.valence,
                    arousal = excluded.arousal,
                    updated_at = excluded.updated_at
                """,
                (group_id, valence, arousal, _iso(now)),
            )

    # --- proactive ledger -------------------------------------------------

    async def record_proactive(self, group_id: int, kind: str) -> None:
        await asyncio.to_thread(self._record_proactive, group_id, kind)

    def _record_proactive(self, group_id: int, kind: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO proactive_ledger (group_id, kind, created_at) VALUES (?, ?, ?)",
                (group_id, kind, _iso(_now())),
            )

    async def count_proactive(
        self, group_id: int, since: datetime, kind: str | None = None
    ) -> int:
        return await asyncio.to_thread(self._count_proactive, group_id, since, kind)

    def _count_proactive(self, group_id: int, since: datetime, kind: str | None) -> int:
        since_iso = _iso(since)
        with self._lock, self._connection() as connection:
            if kind is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM proactive_ledger WHERE group_id = ? AND created_at >= ?",
                    (group_id, since_iso),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM proactive_ledger WHERE group_id = ? AND kind = ? AND created_at >= ?",
                    (group_id, kind, since_iso),
                ).fetchone()
        return int(row["n"] or 0)

    async def last_proactive_at(self, group_id: int, kind: str) -> datetime | None:
        return await asyncio.to_thread(self._last_proactive_at, group_id, kind)

    def _last_proactive_at(self, group_id: int, kind: str) -> datetime | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT MAX(created_at) AS last FROM proactive_ledger WHERE group_id = ? AND kind = ?",
                (group_id, kind),
            ).fetchone()
        return _parse_iso(row["last"]) if row else None

    # --- follow-up ledger -------------------------------------------------

    async def recent_followup_exists(
        self, group_id: int, user_id: int, topic_key: str, within_days: float
    ) -> bool:
        return await asyncio.to_thread(
            self._recent_followup_exists, group_id, user_id, topic_key, within_days
        )

    def _recent_followup_exists(
        self, group_id: int, user_id: int, topic_key: str, within_days: float
    ) -> bool:
        threshold = _iso(_now() - timedelta(days=within_days))
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM followup_ledger
                WHERE group_id = ? AND user_id = ? AND topic_key = ? AND created_at >= ?
                LIMIT 1
                """,
                (group_id, user_id, topic_key, threshold),
            ).fetchone()
        return row is not None

    async def record_followup(self, group_id: int, user_id: int, topic_key: str) -> None:
        await asyncio.to_thread(self._record_followup, group_id, user_id, topic_key)

    def _record_followup(self, group_id: int, user_id: int, topic_key: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO followup_ledger (group_id, user_id, topic_key, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, user_id, topic_key) DO UPDATE SET
                    created_at = excluded.created_at
                """,
                (group_id, user_id, topic_key, _iso(_now())),
            )


# ---------------------------------------------------------------------------
# The single gate every proactive send must pass through.
# ---------------------------------------------------------------------------


class ProactivityBudget:
    """Master switch -> group enabled -> quiet hours -> cooldown -> hourly/daily caps."""

    def __init__(
        self,
        config_store: "RuntimeConfigStore",
        group_state: "GroupStateStore",
        state_store: BotStateStore,
    ) -> None:
        self.config_store = config_store
        self.group_state = group_state
        self.state_store = state_store

    @staticmethod
    def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    async def peek(
        self,
        group_id: int,
        kind: str,
        now: datetime | None = None,
        ignore_quiet: bool = False,
    ) -> bool:
        settings = self.config_store.snapshot()
        if not settings.proactive_enabled:
            return False
        if group_id not in settings.allowed_groups:
            return False
        if not self.group_state.is_enabled(group_id):
            return False
        moment = now or _now()
        local = moment.astimezone(ZoneInfo(settings.context_timezone))
        if not ignore_quiet and self._in_quiet_hours(
            local.hour, settings.proactive_quiet_start, settings.proactive_quiet_end
        ):
            return False
        if settings.proactive_cooldown_seconds > 0:
            last = await self.state_store.last_proactive_at(group_id, kind)
            if last is not None and (moment - last).total_seconds() < settings.proactive_cooldown_seconds:
                return False
        hourly = await self.state_store.count_proactive(group_id, moment - timedelta(hours=1))
        if hourly >= settings.proactive_hourly_cap:
            return False
        daily = await self.state_store.count_proactive(group_id, moment - timedelta(days=1))
        if daily >= settings.proactive_daily_cap:
            return False
        return True

    async def commit(self, group_id: int, kind: str) -> None:
        await self.state_store.record_proactive(group_id, kind)
