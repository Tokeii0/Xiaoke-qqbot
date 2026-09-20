"""Durable short-term chat history.

`plugin.py` keeps recent turns in an in-memory deque per session. That deque was
lost on every restart, which cost more than it looks:

* the chat model lost the ambient group context it uses to follow a conversation;
* the Jev reply gate lost `recent_messages` entirely, so right after a restart
  `bot_spoke_last` was always false and "someone is replying to the bot" could not
  be recognised at all.

This store mirrors the deque to SQLite so a restart resumes mid-conversation. Same
shape as the other stores here: sync sqlite3 under a lock, async wrappers that hand
the work to a thread.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class ChatHistoryStore:
    """Rolling per-session transcript, trimmed to the same depth as the deque."""

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
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_history_session
                    ON chat_history (session_key, id);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(chat_history)")}
            if "metadata" not in columns:
                connection.execute("ALTER TABLE chat_history ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    def load(self, keep: int) -> dict[str, list[dict[str, str]]]:
        """Read every session's newest `keep` turns, oldest first.

        Synchronous on purpose: this runs once during startup, before the first
        message is handled, so the deques are warm when traffic arrives.
        """
        if keep <= 0:
            return {}
        sessions: dict[str, list[dict[str, str]]] = {}
        with self._lock, self._connection() as connection:
            # `_append` already trims each session to `keep`, so the table holds at
            # most that many rows per session -- no per-session windowing needed here.
            rows = connection.execute(
                "SELECT session_key, role, content, metadata FROM chat_history ORDER BY id ASC"
            ).fetchall()
        for row in rows:
            try:
                metadata = json.loads(row["metadata"])
                metadata = {key: metadata[key] for key in ("images", "message_id", "user_id", "created_at", "delivery", "photo") if key in metadata}
            except (ValueError, TypeError):
                metadata = {}
            sessions.setdefault(row["session_key"], []).append({"role": row["role"], "content": row["content"], **metadata})
        # Defensive: `keep` may have been lowered since those rows were written.
        return {key: turns[-keep:] for key, turns in sessions.items()}

    async def append(self, session_key: str, role: str, content: str, keep: int, metadata: dict | None = None) -> None:
        await asyncio.to_thread(self._append, session_key, role, content, keep, metadata)

    def _append(self, session_key: str, role: str, content: str, keep: int, metadata: dict | None = None) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO chat_history (session_key, role, content, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_key,
                    role,
                    content,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            if keep > 0:
                # Keep this session's newest `keep` rows; other sessions untouched.
                connection.execute(
                    """
                    DELETE FROM chat_history
                    WHERE session_key = ? AND id NOT IN (
                        SELECT id FROM chat_history WHERE session_key = ?
                        ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (session_key, session_key, keep),
                )

    async def clear(self, session_key: str | None = None) -> int:
        return await asyncio.to_thread(self._clear, session_key)

    def _clear(self, session_key: str | None) -> int:
        with self._lock, self._connection() as connection:
            if session_key is None:
                removed = connection.execute(
                    "SELECT COUNT(*) AS n FROM chat_history"
                ).fetchone()["n"]
                connection.execute("DELETE FROM chat_history")
            else:
                removed = connection.execute(
                    "SELECT COUNT(*) AS n FROM chat_history WHERE session_key = ?",
                    (session_key,),
                ).fetchone()["n"]
                connection.execute(
                    "DELETE FROM chat_history WHERE session_key = ?", (session_key,)
                )
        return int(removed or 0)
