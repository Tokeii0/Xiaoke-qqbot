"""One authenticated request journal for JEV and every model-backed feature."""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from nonebot import logger


_context = ContextVar("model_request_context", default={})
KINDS = {"jev": "JEV 判定", "chat": "对话模型", "vision": "图片理解", "voice": "语音合成", "image": "图片生成", "search": "联网搜索"}
FEATURES = {"reply": "聊天回复", "gate": "回复与场景判定", "voice_judge": "语音适合度与风格",
    "routine_diary": "校园日记", "campus_photo": "校园配图", "voice": "语音合成", "vision": "图片理解",
    "memory": "记忆筛选", "memory_recall": "记忆召回", "topic": "事项与追问", "knowledge": "群知识库",
    "feedback": "自然语言纠错", "preference": "回复偏好", "summary": "群聊总结", "member_profile": "群员画像",
    "admin_preview": "后台试用", "semantic": "语义判定", "tool": "自然语言功能",
    "web_search": "联网搜索", "search_query": "搜索词整理", "typing_judge": "闲聊手误判断", "reply_trim": "短回复收口"}


@contextmanager
def call_scope(feature=None, **values):
    context = {**_context.get(), **values}
    if feature:
        context["feature"] = feature
    context.setdefault("trace_id", uuid.uuid4().hex[:16])
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)


def sanitized(value, secrets=(), depth=0):
    if depth > 12:
        return "[结构过深]"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"binary_bytes": len(value)}
    if isinstance(value, str):
        if value.startswith(("data:image/", "data:audio/", "base64://")):
            return "[媒体数据已省略]"
        for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
            value = value.replace(secret, "[密钥已隐藏]")
        value = re.sub(r"\bBearer\s+\S+|\bsk-[\w-]{12,}", "[密钥已隐藏]", value, flags=re.I)
        return value[:24000] + ("…[已截断]" if len(value) > 24000 else "")
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "model_dump"):
        value = value.model_dump()
    elif not isinstance(value, (dict, list, tuple)):
        value = {key: item for key, item in vars(value).items() if not key.startswith("_")} if hasattr(value, "__dict__") else str(value)
    if isinstance(value, dict):
        omitted = {"authorization", "api_key", "apikey", "password", "access_token", "secret", "cookie", "set-cookie",
                   "b64_json", "audio", "wav", "pcm", "reasoning_content", "reasoning", "thinking"}
        return {str(key): "[已省略]" if str(key).lower() in omitted else sanitized(item, secrets, depth + 1)
                for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [sanitized(item, secrets, depth + 1) for item in value[:60]]
    return sanitized(value, secrets, depth + 1)


def packed(value, secrets=()):
    text = json.dumps(sanitized(value, secrets), ensure_ascii=False, default=str)
    return text if len(text) <= 100000 else json.dumps({"truncated": True, "preview": text[:95000]}, ensure_ascii=False)


class RequestLogStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.RLock()
        self._memory = None if path else sqlite3.connect(":memory:", check_same_thread=False)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS model_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT NOT NULL,
                feature TEXT NOT NULL, model TEXT NOT NULL, endpoint TEXT NOT NULL, trace_id TEXT,
                group_id INTEGER, user_id INTEGER, status TEXT NOT NULL, duration_ms REAL DEFAULT 0,
                request_json TEXT NOT NULL, response_json TEXT, usage_json TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS request_time ON model_requests(id DESC);
                CREATE INDEX IF NOT EXISTS request_kind ON model_requests(kind, id DESC);
            """)
            db.execute("UPDATE model_requests SET status='interrupted', error='服务重启，请求未完成' WHERE status='running'")

    @contextmanager
    def connection(self):
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10) if self.path else self._memory
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                if self.path:
                    db.close()

    def begin(self, *, kind, feature, model, endpoint, context, request, retention):
        with self.connection() as db:
            row = db.execute("""INSERT INTO model_requests
                (created_at,kind,feature,model,endpoint,trace_id,group_id,user_id,status,request_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (datetime.now(timezone.utc).isoformat(timespec="seconds"), kind, feature,
                model, endpoint, context.get("trace_id"), context.get("group_id"), context.get("user_id"), "running", request))
            if retention > 0:
                db.execute("DELETE FROM model_requests WHERE status!='running' AND id NOT IN (SELECT id FROM model_requests ORDER BY id DESC LIMIT ?)", (retention,))
            return row.lastrowid

    def finish(self, identity, status, duration, response, usage, error, request=None, retention=0):
        with self.connection() as db:
            db.execute("UPDATE model_requests SET status=?,duration_ms=?,response_json=?,usage_json=?,error=?,request_json=COALESCE(?,request_json) WHERE id=?",
                       (status, duration, response, usage, error, request, identity))
            if retention > 0:
                db.execute("DELETE FROM model_requests WHERE status!='running' AND id NOT IN (SELECT id FROM model_requests ORDER BY id DESC LIMIT ?)", (retention,))

    def recent(self, *, kind="", feature="", status="", group_id=None, query="", offset=0, limit=50):
        clauses, params = [], []
        for column, value in (("kind", kind), ("feature", feature), ("status", status), ("group_id", group_id)):
            if value is not None and value != "":
                clauses.append(f"{column}=?")
                params.append(value)
        if query:
            clauses.append("(model LIKE ? OR request_json LIKE ? OR error LIKE ?)")
            params.extend(["%" + query[:100] + "%"] * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as db:
            count = db.execute("SELECT COUNT(*) FROM model_requests" + where, params).fetchone()[0]
            rows = db.execute("SELECT id,created_at,kind,feature,model,trace_id,group_id,user_id,status,duration_ms,error,usage_json FROM model_requests" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", [*params, min(100, max(1, limit)), max(0, offset)]).fetchall()
            stats = dict(db.execute("""SELECT COUNT(*) AS total, SUM(status='success') AS success,
                SUM(status IN ('error','interrupted','cancelled')) AS failed, SUM(status='running') AS running,
                AVG(CASE WHEN status!='running' THEN duration_ms END) AS average_ms FROM model_requests""" + where, params).fetchone())
        items = [{**dict(row), "usage": json.loads(row["usage_json"] or "null")} for row in rows]
        for item in items:
            item.pop("usage_json", None)
        return {"items": items, "total": count, "stats": stats, "kinds": KINDS, "features": FEATURES}

    def detail(self, identity):
        with self.connection() as db:
            row = db.execute("SELECT * FROM model_requests WHERE id=?", (identity,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("request", "response", "usage"):
            result[field] = json.loads(result.pop(field + "_json") or "null")
        return result


class Trace:
    request = None
    response = None
    usage = None


@asynccontextmanager
async def trace_request(store, *, kind, settings, request, model, endpoint="", feature=None):
    trace = Trace()
    identity = None
    context = dict(_context.get())
    context.setdefault("trace_id", uuid.uuid4().hex[:16])
    feature = "admin_preview" if context.get("feature") == "admin_preview" else feature or context.get("feature", "reply")
    secrets = [getattr(settings, key, "") for key in ("api_key", "fallback_api_key", "vision_api_key", "voice_api_key", "routine_photo_api_key", "memory_api_key", "search_api_key")]
    secrets.extend(context.pop("redact_secrets", ()))
    secrets.extend(os.getenv(key, "") for key in ("TYPESAFE_API_KEY", "WEB_ADMIN_TOKEN", "ONEBOT_ACCESS_TOKEN"))
    started = time.monotonic()
    status, error = "success", ""
    try:
        if store and (kind != "jev" or settings.jev_log_enabled):
            try:
                address = urlsplit(endpoint)
                host = address.hostname or ""
                if ":" in host:
                    host = f"[{host}]"
                if address.port is not None:
                    host += f":{address.port}"
                endpoint = urlunsplit((address.scheme, host, address.path, "", ""))
                identity = await asyncio.to_thread(store.begin, kind=kind, feature=feature, model=sanitized(model, secrets), endpoint=sanitized(endpoint, secrets),
                    context=context, request=packed(request, secrets), retention=settings.jev_log_retention)
            except Exception as exc:
                logger.warning(f"请求日志写入失败：{type(exc).__name__}")
        yield trace
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        error = sanitized(f"{type(exc).__name__}: {exc}", secrets)[:1000]
        raise
    finally:
        if identity is not None:
            try:
                await asyncio.shield(asyncio.to_thread(store.finish, identity, status, round((time.monotonic() - started) * 1000),
                    packed(trace.response, secrets), packed(trace.usage, secrets), error,
                    packed(trace.request, secrets) if trace.request is not None else None, settings.jev_log_retention))
            except Exception as exc:
                logger.warning(f"请求日志收尾失败：{type(exc).__name__}")
