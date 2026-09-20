"""Persistent, conversation-scoped facts, open topics and one-off reminders."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


class IntelligenceStore:
    TABLES = {"facts", "topics", "reminders", "knowledge", "preferences"}

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', superseded_by INTEGER,
                    source_event TEXT NOT NULL, UNIQUE(scope, source_event)
                );
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', asked_at TEXT,
                    source_event TEXT NOT NULL, UNIQUE(scope, source_event)
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, user_id INTEGER NOT NULL,
                    group_id INTEGER, display_name TEXT NOT NULL, text TEXT NOT NULL,
                    created_at TEXT NOT NULL, due_at TEXT NOT NULL, timezone TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', last_error TEXT NOT NULL DEFAULT '',
                    source_event TEXT NOT NULL, UNIQUE(scope, source_event)
                );
                CREATE INDEX IF NOT EXISTS facts_owner ON facts(scope,user_id,status);
                CREATE INDEX IF NOT EXISTS topics_owner ON topics(scope,user_id,status);
                CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status,due_at);
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL, question TEXT NOT NULL, text TEXT NOT NULL,
                    sources TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    superseded_by INTEGER, source_event TEXT NOT NULL, UNIQUE(scope, source_event)
                );
                CREATE INDEX IF NOT EXISTS knowledge_scope ON knowledge(scope,status);
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, user_id INTEGER NOT NULL,
                    target_user_id INTEGER NOT NULL, display_name TEXT NOT NULL, text TEXT NOT NULL,
                    topic TEXT NOT NULL, mode TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', source_event TEXT NOT NULL, UNIQUE(scope,source_event)
                );
                CREATE INDEX IF NOT EXISTS preferences_scope ON preferences(scope,target_user_id,status);
            """)

    @contextmanager
    def connection(self):
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    async def rows(self, kind, *, scope=None, user_id=None, status=None, limit=100):
        return await asyncio.to_thread(self._rows, kind, scope, user_id, status, limit)

    def _rows(self, kind, scope, user_id, status, limit):
        if kind not in self.TABLES:
            raise ValueError("未知记录类型")
        clauses, values = [], []
        for key, value in (("scope", scope), ("user_id", user_id), ("status", status)):
            if value is not None:
                clauses.append(f"{key} = ?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM {kind}{where} ORDER BY id DESC LIMIT ?", (*values, max(1, min(limit, 500)))
            )]
            if kind == "knowledge":
                for row in rows:
                    row["sources"] = json.loads(row["sources"])
            return rows

    async def save_knowledge(self, *, scope, user_id, display_name, question, text, sources, source_event, supersedes=()):
        return await asyncio.to_thread(self._save_knowledge, scope, user_id, display_name, question, text, sources, source_event, supersedes)

    def _save_knowledge(self, scope, user_id, display_name, question, text, sources, source_event, supersedes):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM knowledge WHERE scope=? AND source_event=?", (scope, source_event)).fetchone()
            if existing:
                return existing["id"]
            cursor = db.execute("INSERT INTO knowledge(scope,user_id,display_name,question,text,sources,created_at,source_event) VALUES(?,?,?,?,?,?,?,?)",
                (scope, user_id, display_name[:50], question, text, json.dumps(sources, ensure_ascii=False), utc_stamp(), source_event))
            for old_id in supersedes:
                db.execute("UPDATE knowledge SET status='superseded',superseded_by=? WHERE id=? AND scope=? AND status='active'",
                           (cursor.lastrowid, old_id, scope))
            return cursor.lastrowid

    async def save_preference(self, *, scope, user_id, target_user_id, display_name, text, topic, mode, source_event):
        return await asyncio.to_thread(self._save_preference, scope, user_id, target_user_id, display_name, text, topic, mode, source_event)

    def _save_preference(self, scope, user_id, target_user_id, display_name, text, topic, mode, source_event):
        if topic not in {"all", "code", "technical"} or mode not in {"text", "auto"}:
            raise ValueError("不支持的回复偏好")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM preferences WHERE scope=? AND source_event=?", (scope,source_event)).fetchone()
            if existing:
                return dict(existing)
            clause = "" if topic == "all" and mode == "auto" else " AND topic=?"
            values = (scope, target_user_id) if not clause else (scope, target_user_id, topic)
            db.execute("UPDATE preferences SET status='superseded' WHERE scope=? AND target_user_id=? AND status='active'" + clause, values)
            cursor = db.execute("INSERT INTO preferences(scope,user_id,target_user_id,display_name,text,topic,mode,created_at,source_event) VALUES(?,?,?,?,?,?,?,?,?)",
                (scope,user_id,target_user_id,display_name[:50],text[:1000],topic,mode,utc_stamp(),source_event))
            return dict(db.execute("SELECT * FROM preferences WHERE id=?", (cursor.lastrowid,)).fetchone())

    async def preference_rules(self, scope, user_id):
        return await asyncio.to_thread(self._preference_rules, scope, user_id)

    def _preference_rules(self, scope, user_id):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM preferences WHERE scope=? AND target_user_id IN (0,?) AND status='active' AND mode='text' ORDER BY id DESC", (scope,user_id))]

    async def save_fact(self, *, scope, user_id, display_name, text, source_event, supersedes=()):
        return await asyncio.to_thread(self._save_fact, scope, user_id, display_name, text, source_event, supersedes)

    def _save_fact(self, scope, user_id, display_name, text, source_event, supersedes):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO facts(scope,user_id,display_name,text,created_at,source_event) VALUES(?,?,?,?,?,?)",
                       (scope, user_id, display_name[:50], text[:1000], utc_stamp(), source_event))
            row = db.execute("SELECT id FROM facts WHERE scope=? AND source_event=?", (scope, source_event)).fetchone()
            for old_id in supersedes:
                db.execute("UPDATE facts SET status='superseded',superseded_by=? WHERE id=? AND scope=? AND user_id=? AND status='active' AND id!=?",
                           (row["id"], old_id, scope, user_id, row["id"]))
            return row["id"]

    async def update_topic(self, *, scope, user_id, display_name, text, source_event, topic_id=None, resolved=False):
        return await asyncio.to_thread(self._update_topic, scope, user_id, display_name, text, source_event, topic_id, resolved)

    def _update_topic(self, scope, user_id, display_name, text, source_event, topic_id, resolved):
        stamp = utc_stamp()
        with self.connection() as db:
            if topic_id is not None:
                db.execute("UPDATE topics SET status=?,updated_at=? WHERE id=? AND scope=? AND user_id=? AND status='open'",
                           ("resolved" if resolved else "open", stamp, topic_id, scope, user_id))
                return topic_id
            if resolved:
                return None
            db.execute("INSERT OR IGNORE INTO topics(scope,user_id,display_name,text,created_at,updated_at,source_event) VALUES(?,?,?,?,?,?,?)",
                       (scope, user_id, display_name[:50], text[:1000], stamp, stamp, source_event))
            return db.execute("SELECT id FROM topics WHERE scope=? AND source_event=?", (scope, source_event)).fetchone()["id"]

    async def reserve_followup(self, topic_id, scope, user_id):
        return await asyncio.to_thread(self._reserve_followup, topic_id, scope, user_id)

    def _reserve_followup(self, topic_id, scope, user_id):
        with self.connection() as db:
            return bool(db.execute("UPDATE topics SET asked_at=? WHERE id=? AND scope=? AND user_id=? AND status='open' AND asked_at IS NULL",
                                   (utc_stamp(), topic_id, scope, user_id)).rowcount)

    async def release_followup(self, topic_id):
        await asyncio.to_thread(self._release_followup, topic_id)

    def _release_followup(self, topic_id):
        with self.connection() as db:
            db.execute("UPDATE topics SET asked_at=NULL WHERE id=? AND status='open'", (topic_id,))

    async def create_reminder(self, *, scope, user_id, group_id, display_name, text, due_at, timezone_name, source_event):
        return await asyncio.to_thread(self._create_reminder, scope, user_id, group_id, display_name, text, due_at, timezone_name, source_event)

    def _create_reminder(self, scope, user_id, group_id, display_name, text, due_at, timezone_name, source_event):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM reminders WHERE scope=? AND source_event=?", (scope, source_event)).fetchone()
            if existing:
                return dict(existing)
            count = db.execute("SELECT COUNT(*) FROM reminders WHERE scope=? AND user_id=? AND status IN ('pending','sending')", (scope, user_id)).fetchone()[0]
            if count >= 20:
                raise ValueError("你在此会话已有 20 条待处理提醒，请先取消不需要的提醒。")
            cursor = db.execute("INSERT INTO reminders(scope,user_id,group_id,display_name,text,created_at,due_at,timezone,source_event) VALUES(?,?,?,?,?,?,?,?,?)",
                                (scope, user_id, group_id, display_name[:50], text, utc_stamp(), utc_stamp(due_at), timezone_name, source_event))
            return dict(db.execute("SELECT * FROM reminders WHERE id=?", (cursor.lastrowid,)).fetchone())

    async def close_record(self, kind, record_id, *, scope=None, user_id=None):
        return await asyncio.to_thread(self._close_record, kind, record_id, scope, user_id)

    def _close_record(self, kind, record_id, scope, user_id):
        transitions = {"facts": ("active", "forgotten"), "topics": ("open", "resolved"), "reminders": ("pending", "cancelled"),
                       "knowledge": ("active", "outdated"), "preferences": ("active", "disabled")}
        if kind not in transitions:
            raise ValueError("未知记录类型")
        old, new = transitions[kind]
        where, args = "id=? AND status=?", [new, record_id, old]
        if scope is not None:
            where += " AND scope=?"
            args.append(scope)
        if user_id is not None:
            where += " AND user_id=?"
            args.append(user_id)
        with self.connection() as db:
            changed = bool(db.execute(f"UPDATE {kind} SET status=? WHERE {where}", args).rowcount)
            if changed and kind == "facts":
                fact = db.execute("SELECT * FROM facts WHERE id=?", (record_id,)).fetchone()
                for row in db.execute("SELECT id,sources FROM knowledge WHERE scope=? AND status='active'", (fact["scope"],)).fetchall():
                    if any(str(source.get("message_id")) == fact["source_event"] and source.get("user_id") == fact["user_id"] for source in json.loads(row["sources"])):
                        db.execute("UPDATE knowledge SET status='outdated' WHERE id=?", (row["id"],))
            return changed

    async def due_reminders(self, now):
        return await asyncio.to_thread(self._due_reminders, now)

    def _due_reminders(self, now):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT 30", (utc_stamp(now),))]

    async def claim_reminder(self, reminder_id, now):
        return await asyncio.to_thread(self._claim_reminder, reminder_id, now)

    def _claim_reminder(self, reminder_id, now):
        with self.connection() as db:
            return bool(db.execute("UPDATE reminders SET status='sending' WHERE id=? AND status='pending' AND due_at<=?", (reminder_id, utc_stamp(now))).rowcount)

    async def finish_reminder(self, reminder_id, status, error=""):
        await asyncio.to_thread(self._finish_reminder, reminder_id, status, error)

    def _finish_reminder(self, reminder_id, status, error):
        if status not in {"sent", "failed", "expired", "blocked"}:
            raise ValueError("未知提醒结果")
        with self.connection() as db:
            db.execute("UPDATE reminders SET status=?,last_error=? WHERE id=? AND status='sending'", (status, error[:200], reminder_id))

    async def recover(self):
        await asyncio.to_thread(self._recover)

    def _recover(self):
        with self.connection() as db:
            # The external send and SQLite commit cannot be atomic. Never repeat an uncertain send.
            db.execute("UPDATE reminders SET status='uncertain',last_error='发送途中重启，结果未知，请核对后重新设置' WHERE status='sending'")
