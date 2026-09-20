from __future__ import annotations

import asyncio
import html as html_lib
import json
import math
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING
from zoneinfo import ZoneInfo

from nonebot import get_bots, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment

from .clients import ChatClient, ServiceError
from .request_log import call_scope

if TYPE_CHECKING:
    from .config import RuntimeConfigStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def parse_summary_json(content: str) -> dict[str, Any]:
    """Parse the LLM's structured daily-summary JSON, bounding every field."""
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
        raise ValueError("总结模型没有返回 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("总结模型返回的 JSON 无法解析") from exc
    if not isinstance(data, dict):
        raise ValueError("总结模型返回格式不正确")

    topics: list[dict[str, str]] = []
    for item in data.get("topics") or []:
        if isinstance(item, dict):
            name = _bounded(item.get("name"), 30)
            if name:
                topics.append({"name": name, "detail": _bounded(item.get("detail"), 200)})
        if len(topics) >= 6:
            break
    highlights = [
        _bounded(item, 120) for item in (data.get("highlights") or []) if _bounded(item, 120)
    ][:6]
    quotes: list[dict[str, str]] = []
    for item in data.get("quotes") or []:
        if isinstance(item, dict):
            quote_text = _bounded(item.get("text"), 120)
            if quote_text:
                quotes.append({"speaker": _bounded(item.get("speaker"), 20) or "群友", "text": quote_text})
        if len(quotes) >= 5:
            break
    timeline: list[dict[str, Any]] = []
    for item in data.get("timeline") or []:
        if isinstance(item, dict):
            detail = _bounded(item.get("detail"), 240)
            if detail:
                people_raw = item.get("people") or item.get("participants") or []
                if isinstance(people_raw, str):
                    people_raw = [people_raw]
                people = [p for p in (_bounded(x, 20) for x in people_raw) if p][:6]
                timeline.append(
                    {"time": _bounded(item.get("time"), 30), "people": people, "detail": detail}
                )
        if len(timeline) >= 14:
            break
    mvp_raw = data.get("mvp") if isinstance(data.get("mvp"), dict) else {}
    return {
        "title": _bounded(data.get("title"), 40) or "今日群聊总结",
        "overview": _bounded(data.get("overview"), 400),
        "timeline": timeline,
        "topics": topics,
        "highlights": highlights,
        "quotes": quotes,
        "mvp": {"name": _bounded(mvp_raw.get("name"), 20), "reason": _bounded(mvp_raw.get("reason"), 120)},
        "mood": _bounded(data.get("mood"), 100),
    }


def estimate_height(data: dict[str, Any], stats: dict[str, Any]) -> int:
    """Rough pixel height for the rendered card; generous so nothing gets clipped."""

    def lines(text: str, per_line: int) -> int:
        return max(1, math.ceil(len(text or "") / per_line))

    height = 250  # header
    height += lines(data["overview"], 34) * 28 + 44
    for entry in data.get("timeline", []):
        height += 34 + lines(entry["detail"], 34) * 23 + 14
    if data.get("timeline"):
        height += 44
    height += 56
    for topic in data["topics"]:
        height += 38 + lines(topic["detail"], 38) * 24 + 16
    if not data["topics"]:
        height += 44
    height += 220 + 26 * len(stats.get("top_speakers") or [])
    if data["highlights"]:
        height += 56 + sum(32 + (lines(item, 38) - 1) * 22 for item in data["highlights"])
    if data["quotes"]:
        height += 56 + sum(72 + (lines(item["text"], 34) - 1) * 22 for item in data["quotes"])
    if data["mvp"]["name"]:
        height += 116
    height += 110  # mood + footer
    return max(720, min(4000, int(height * 1.22) + 60))


_IC = {
    "topic": '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z"/>',
    "time": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    "stats": '<line x1="6" y1="20" x2="6" y2="14"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="18" y1="20" x2="18" y2="9"/>',
    "mic": '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="19" x2="12" y2="22"/>',
    "star": '<polygon points="12 2 15 9 22 9.3 16.5 14 18.5 21 12 17 5.5 21 7.5 14 2 9.3 9 9"/>',
    "quote": '<path d="M6 17h3l2-4V6H4v7h2z"/><path d="M16 17h3l2-4V6h-9v7h4z"/>',
    "trophy": '<path d="M7 4h10v4a5 5 0 0 1-10 0z"/><path d="M7 4H4v2a3 3 0 0 0 3 3"/><path d="M17 4h3v2a3 3 0 0 1-3 3"/><line x1="12" y1="13" x2="12" y2="18"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="18" x2="12" y2="21"/>',
}


def _icon(name: str, color: str = "#8a7bd8") -> str:
    return (
        f'<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_IC[name]}</svg>'
    )


def _timeline_html(entries: list[dict[str, Any]], esc) -> str:
    rows = []
    for entry in entries:
        ats = "".join(f'<span class="tl-at">@{esc(p)}</span>' for p in entry["people"])
        rows.append(
            f'<div class="tl-item"><div class="tl-time">{esc(entry["time"]) or "—"}</div>'
            f'<div class="tl-body">{ats}<span class="tl-text">{esc(entry["detail"])}</span></div></div>'
        )
    return "".join(rows)


def render_summary_html(
    bot_name: str,
    group_id: int,
    date_str: str,
    data: dict[str, Any],
    stats: dict[str, Any],
) -> str:
    esc = html_lib.escape
    timeline_inner = _timeline_html(data.get("timeline") or [], esc)
    timeline_block = (
        f'<div class="section"><div class="section-title">{_icon("time")}对话脉络</div>'
        f'<div class="timeline">{timeline_inner}</div></div>'
        if timeline_inner
        else ""
    )
    topics = "".join(
        f'<div class="topic"><div class="topic-name">{esc(t["name"])}</div>'
        f'<div class="topic-detail">{esc(t["detail"])}</div></div>'
        for t in data["topics"]
    ) or '<div class="empty">今天大家聊得比较随意，没有形成集中话题～</div>'
    highlights = "".join(f"<li>{esc(item)}</li>" for item in data["highlights"])
    highlights_block = (
        f'<div class="section"><div class="section-title">{_icon("star", "#d8a24a")}高光时刻</div><ul class="highlights">{highlights}</ul></div>'
        if highlights
        else ""
    )
    quotes = "".join(
        f'<div class="quote"><div class="quote-text">“{esc(q["text"])}”</div>'
        f'<div class="quote-by">—— {esc(q["speaker"])}</div></div>'
        for q in data["quotes"]
    )
    quotes_block = (
        f'<div class="section"><div class="section-title">{_icon("quote", "#c9762c")}今日金句</div>{quotes}</div>' if quotes else ""
    )
    speakers = "".join(
        f'<div class="rank"><span class="rank-name">{esc(str(name))}</span>'
        f'<span class="rank-bar"><i style="width:{max(8, int(count / max(1, stats["top_speakers"][0][1]) * 100))}%"></i></span>'
        f'<span class="rank-count">{int(count)}条</span></div>'
        for name, count in stats.get("top_speakers") or []
    )
    mvp_block = (
        f'<div class="mvp"><span class="mvp-badge">{_icon("trophy", "#c9762c")}今日MVP</span>'
        f'<span class="mvp-name">{esc(data["mvp"]["name"])}</span>'
        f'<span class="mvp-reason">{esc(data["mvp"]["reason"])}</span></div>'
        if data["mvp"]["name"]
        else ""
    )
    mood_block = (
        f'<div class="mood">今日氛围：{esc(data["mood"])}</div>' if data["mood"] else ""
    )
    peak = esc(str(stats.get("peak_hour") or "—"))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
         background: linear-gradient(160deg, #232a53 0%, #4a3670 60%, #7a4d8f 100%);
         padding: 28px; color: #2a2a33; }}
  .card {{ background: #ffffff; border-radius: 24px; padding: 34px 36px; box-shadow: 0 18px 50px rgba(0,0,0,.35); }}
  .eyebrow {{ color: #8a7bd8; font-size: 13px; letter-spacing: 3px; font-weight: 700; }}
  h1 {{ font-size: 27px; margin: 8px 0 6px; color: #23223a; }}
  .meta {{ color: #9a97ad; font-size: 13px; margin-bottom: 20px; }}
  .overview {{ background: #f5f3ff; border-left: 4px solid #8a7bd8; border-radius: 0 12px 12px 0;
              padding: 14px 16px; font-size: 15px; line-height: 1.75; color: #3d3a55; }}
  .section {{ margin-top: 26px; }}
  .section-title {{ font-size: 16px; font-weight: 800; color: #37345a; margin-bottom: 12px; display: flex; align-items: center; }}
  .ico {{ width: 18px; height: 18px; margin-right: 8px; flex: none; }}
  .timeline {{ border-left: 2px solid #e9e6fb; margin: 4px 0 0 7px; padding-left: 18px; }}
  .tl-item {{ position: relative; margin-bottom: 14px; }}
  .tl-item::before {{ content: ''; position: absolute; left: -24px; top: 4px; width: 9px; height: 9px; border-radius: 50%; background: #8a7bd8; box-shadow: 0 0 0 3px #efecfd; }}
  .tl-time {{ font-weight: 700; color: #5b4bc4; font-size: 13px; }}
  .tl-body {{ margin-top: 4px; }}
  .tl-at {{ display: inline-block; background: #efe9ff; color: #6a4bc4; border-radius: 6px; padding: 1px 8px; font-size: 12px; margin: 0 5px 4px 0; font-weight: 600; }}
  .tl-text {{ color: #4d4a63; font-size: 13.5px; line-height: 1.65; }}
  .topic {{ background: #fafaff; border: 1px solid #ecebfa; border-radius: 14px; padding: 12px 14px; margin-bottom: 10px; }}
  .topic-name {{ font-weight: 700; color: #5b4bc4; font-size: 14.5px; }}
  .topic-detail {{ color: #5d5a75; font-size: 13.5px; line-height: 1.65; margin-top: 4px; }}
  .empty {{ color: #9a97ad; font-size: 13.5px; }}
  .stats {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-top: 4px; }}
  .stat {{ background: linear-gradient(150deg, #6f5bd5, #9367c9); color: #fff; border-radius: 14px;
          padding: 14px; text-align: center; }}
  .stat b {{ display: block; font-size: 23px; }}
  .stat span {{ font-size: 12px; opacity: .85; }}
  .rank {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13.5px; }}
  .rank-name {{ width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #44415f; }}
  .rank-bar {{ flex: 1; height: 8px; background: #efedfb; border-radius: 99px; overflow: hidden; }}
  .rank-bar i {{ display: block; height: 100%; background: linear-gradient(90deg, #8a7bd8, #c58ad8); border-radius: 99px; }}
  .rank-count {{ width: 56px; text-align: right; color: #8a87a3; }}
  .highlights li {{ margin: 0 0 8px 18px; font-size: 14px; line-height: 1.6; color: #46435f; }}
  .quote {{ background: #fff8f0; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; }}
  .quote-text {{ font-size: 14px; color: #6b4f2a; line-height: 1.6; }}
  .quote-by {{ text-align: right; color: #b39668; font-size: 12.5px; margin-top: 6px; }}
  .mvp {{ margin-top: 26px; background: linear-gradient(140deg, #ffedd8, #ffe1ee); border-radius: 14px;
         padding: 14px 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .mvp-badge {{ background: #fff; border-radius: 99px; padding: 4px 12px; font-size: 13px; font-weight: 800; color: #c9762c; }}
  .mvp-name {{ font-weight: 800; color: #7a4520; }}
  .mvp-reason {{ color: #96683d; font-size: 13px; }}
  .mood {{ margin-top: 20px; text-align: center; color: #6f5bd5; font-size: 14px; font-weight: 600; }}
  .footer {{ margin-top: 22px; text-align: center; color: #b9b6cc; font-size: 12px; }}
</style></head>
<body><div class="card">
  <div class="eyebrow">DAILY DIGEST</div>
  <h1>{esc(data["title"])}</h1>
  <div class="meta">{esc(date_str)} · 群 {group_id}</div>
  <div class="overview">{esc(data["overview"]) or "今天的群聊风平浪静。"}</div>
  {timeline_block}
  <div class="section"><div class="section-title">{_icon('topic')}今日话题</div>{topics}</div>
  <div class="section"><div class="section-title">{_icon('stats')}数据一览</div>
    <div class="stats">
      <div class="stat"><b>{int(stats.get("count") or 0)}</b><span>消息总数</span></div>
      <div class="stat"><b>{int(stats.get("members") or 0)}</b><span>活跃成员</span></div>
      <div class="stat"><b>{peak}</b><span>最活跃时段</span></div>
    </div>
    <div class="section"><div class="section-title">{_icon('mic')}话痨榜</div>{speakers or '<div class="empty">暂无数据</div>'}</div>
  </div>
  {highlights_block}
  {quotes_block}
  {mvp_block}
  {mood_block}
  <div class="footer">由 {esc(bot_name)} 自动生成 · {esc(date_str)}</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Headless-browser rendering (Edge/Chrome, zero extra Python deps).
# ---------------------------------------------------------------------------

_SUMMARY_WIDTH = 760


def find_browser() -> str | None:
    """Locate a Chromium-based browser; BOT_SUMMARY_BROWSER env overrides (env-only by design)."""
    override = os.getenv("BOT_SUMMARY_BROWSER", "").strip()
    if override:
        return override if Path(override).exists() else None
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


async def render_html_to_png(html_text: str, height: int, work_dir: Path) -> bytes | None:
    browser = find_browser()
    if browser is None:
        logger.warning("未找到 Edge/Chrome，无法把总结渲染成图片（可用 BOT_SUMMARY_BROWSER 指定路径）")
        return None
    work_dir = work_dir.resolve()  # as_uri() below requires an absolute path
    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}"
    html_path = work_dir / f"render-{stamp}.html"
    png_path = work_dir / f"render-{stamp}.png"
    profile_dir = work_dir / "profile"
    html_path.write_text(html_text, encoding="utf-8")
    base_args = [
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--force-device-scale-factor=1",
        f"--user-data-dir={profile_dir}",
        f"--window-size={_SUMMARY_WIDTH},{height}",
        f"--screenshot={png_path}",
        html_path.as_uri(),
    ]
    try:
        for headless_flag in ("--headless=new", "--headless"):
            process = await asyncio.create_subprocess_exec(
                browser,
                headless_flag,
                *base_args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=60)
            except asyncio.TimeoutError:
                process.kill()
                logger.warning("总结截图超时")
                return None
            if png_path.exists() and png_path.stat().st_size > 0:
                return png_path.read_bytes()
        logger.warning("浏览器没有产出截图文件")
        return None
    finally:
        for path in (html_path, png_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Storage: raw day messages + generated summaries.
# ---------------------------------------------------------------------------


class SummaryStore:
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
                CREATE TABLE IF NOT EXISTS summary_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_summary_messages
                    ON summary_messages(group_id, created_at);
                CREATE TABLE IF NOT EXISTS summaries (
                    group_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    html TEXT NOT NULL,
                    png_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, date)
                );
                """
            )

    async def record_message(
        self, group_id: int, user_id: int, display_name: str, content: str
    ) -> None:
        await asyncio.to_thread(self._record_message, group_id, user_id, display_name, content)

    def _record_message(self, group_id: int, user_id: int, display_name: str, content: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO summary_messages (group_id, user_id, display_name, content, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (group_id, user_id, display_name[:50], content[:500], _now_iso()),
            )

    async def messages_between(
        self, group_id: int, start_iso: str, end_iso: str, limit: int = 1500
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._messages_between, group_id, start_iso, end_iso, limit)

    def _messages_between(
        self, group_id: int, start_iso: str, end_iso: str, limit: int
    ) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT user_id, display_name, content, created_at FROM summary_messages
                WHERE group_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY id DESC LIMIT ?
                """,
                (group_id, start_iso, end_iso, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    async def stats_between(
        self, group_id: int, start_iso: str, end_iso: str, tz_name: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_between, group_id, start_iso, end_iso, tz_name)

    def _stats_between(
        self, group_id: int, start_iso: str, end_iso: str, tz_name: str
    ) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            totals = connection.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS members FROM summary_messages"
                " WHERE group_id = ? AND created_at >= ? AND created_at < ?",
                (group_id, start_iso, end_iso),
            ).fetchone()
            top = connection.execute(
                "SELECT MAX(display_name) AS name, COUNT(*) AS c FROM summary_messages"
                " WHERE group_id = ? AND created_at >= ? AND created_at < ?"
                " GROUP BY user_id ORDER BY c DESC LIMIT 5",
                (group_id, start_iso, end_iso),
            ).fetchall()
            stamps = connection.execute(
                "SELECT created_at FROM summary_messages"
                " WHERE group_id = ? AND created_at >= ? AND created_at < ?",
                (group_id, start_iso, end_iso),
            ).fetchall()
        histogram: dict[int, int] = {}
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
        for row in stamps:
            try:
                moment = datetime.fromisoformat(row["created_at"]).astimezone(tz)
            except ValueError:
                continue
            histogram[moment.hour] = histogram.get(moment.hour, 0) + 1
        peak_hour = None
        if histogram:
            hour = max(histogram, key=lambda key: histogram[key])
            peak_hour = f"{hour:02d}点"
        return {
            "count": int(totals["n"] or 0),
            "members": int(totals["members"] or 0),
            "top_speakers": [(row["name"], int(row["c"])) for row in top],
            "peak_hour": peak_hour,
        }

    async def save_summary(self, group_id: int, date: str, html_text: str, png_path: str) -> None:
        await asyncio.to_thread(self._save_summary, group_id, date, html_text, png_path)

    def _save_summary(self, group_id: int, date: str, html_text: str, png_path: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO summaries (group_id, date, html, png_path, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id, date) DO UPDATE SET
                    html = excluded.html, png_path = excluded.png_path,
                    created_at = excluded.created_at
                """,
                (group_id, date, html_text, png_path, _now_iso()),
            )

    async def get_latest_summary(self, group_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_latest_summary, group_id)

    def _get_latest_summary(self, group_id: int) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM summaries WHERE group_id = ? ORDER BY date DESC LIMIT 1",
                (group_id,),
            ).fetchone()
        return dict(row) if row else None

    async def has_summary(self, group_id: int, date: str) -> bool:
        return await asyncio.to_thread(self._has_summary, group_id, date)

    def _has_summary(self, group_id: int, date: str) -> bool:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM summaries WHERE group_id = ? AND date = ? LIMIT 1",
                (group_id, date),
            ).fetchone()
        return row is not None

    async def prune(self, message_days: int = 3, summary_days: int = 30) -> None:
        await asyncio.to_thread(self._prune, message_days, summary_days)

    def _prune(self, message_days: int, summary_days: int) -> None:
        message_cutoff = (datetime.now(timezone.utc) - timedelta(days=message_days)).isoformat(
            timespec="seconds"
        )
        summary_cutoff = (datetime.now(timezone.utc) - timedelta(days=summary_days)).strftime(
            "%Y-%m-%d"
        )
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM summary_messages WHERE created_at < ?", (message_cutoff,)
            )
            old = connection.execute(
                "SELECT png_path FROM summaries WHERE date < ? AND png_path != ''",
                (summary_cutoff,),
            ).fetchall()
            connection.execute("DELETE FROM summaries WHERE date < ?", (summary_cutoff,))
        for row in old:
            try:
                Path(row["png_path"]).unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Service: pull day messages -> LLM structured summary -> HTML -> PNG.
# ---------------------------------------------------------------------------


class DailySummaryService:
    def __init__(
        self,
        store: SummaryStore,
        config_store: "RuntimeConfigStore",
        chat_client: ChatClient,
    ) -> None:
        self.store = store
        self.config_store = config_store
        self.chat_client = chat_client
        self._inflight: set[int] = set()

    def _day_bounds(self, settings, date_str: str | None) -> tuple[str, str, str]:
        tz = ZoneInfo(settings.context_timezone)
        if date_str:
            day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
        else:
            now = datetime.now(tz)
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return (
            start.strftime("%Y-%m-%d"),
            start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end.astimezone(timezone.utc).isoformat(timespec="seconds"),
        )

    async def generate(
        self, group_id: int, *, force: bool = False, date_str: str | None = None
    ) -> dict[str, Any]:
        if group_id in self._inflight:
            raise ServiceError("该群的总结正在生成中")
        settings = self.config_store.snapshot()
        if not settings.chat_configured:
            raise ServiceError("尚未配置可用的对话模型")
        self._inflight.add(group_id)
        try:
            day, start_iso, end_iso = self._day_bounds(settings, date_str)
            messages = await self.store.messages_between(group_id, start_iso, end_iso, limit=5000)
            if len(messages) < settings.summary_min_messages and not force:
                raise ServiceError(f"今日消息只有 {len(messages)} 条，未达到生成门槛")
            if not messages:
                raise ServiceError("今天该群还没有可总结的消息")
            stats = await self.store.stats_between(
                group_id, start_iso, end_iso, settings.context_timezone
            )
            tz = ZoneInfo(settings.context_timezone)
            sample_lines = []
            for item in messages:  # 当天全部消息
                try:
                    stamp = datetime.fromisoformat(item["created_at"]).astimezone(tz)
                    prefix = stamp.strftime("%H:%M")
                except ValueError:
                    prefix = "--:--"
                sample_lines.append(f"{prefix} {item['display_name']}: {item['content'][:160]}")
            transcript = "\n".join(sample_lines)
            if len(transcript) > 24000:  # 保留最近的，控制在模型上下文预算内
                transcript = "（较早的消息已省略）\n" + transcript[-24000:]
            system = (
                "你是QQ群聊的每日总结助手。根据当天的完整群聊记录生成详细的结构化总结，只输出一个 JSON 对象，字段："
                "title（简短活泼的标题，不超过20字）、overview（3-5句今日概览）、"
                "timeline（数组，按时间顺序详细还原当天对话脉络，每项 "
                "{time:'时段，如 20:00-20:30', people:['参与的昵称',...], detail:'这段时间谁和谁聊了什么、具体聊到哪些内容'}，"
                "尽量覆盖当天各主要对话段落、写清参与者，最多12条）、"
                "topics（数组，每项 {name, detail}，当天主要话题，最多6个）、"
                "highlights（今日高光/趣事一句话数组，最多6条）、"
                "quotes（数组，每项 {speaker, text}，挑最有意思的原话，最多4条）、"
                "mvp（{name, reason}，今日最活跃或最有贡献的成员）、mood（一句话形容今日氛围）。"
                "要求：中文，尽量详细具体、写清是谁在聊和聊了什么，轻松有趣但不刻薄，不编造没发生的事，不输出 JSON 以外的内容。"
            )
            user = (
                f"群号：{group_id}\n日期：{day}\n"
                f"消息总数：{stats['count']}，活跃成员：{stats['members']}\n"
                f"当天完整聊天记录（时间 昵称: 内容）：\n" + transcript
            )
            analysis_settings = replace(
                settings,
                temperature=min(settings.temperature, 0.6),
                max_tokens=min(max(settings.max_tokens, 2500), 4000),
                response_format="text",
            )
            with call_scope("summary", group_id=group_id):
                content = await self.chat_client.complete(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    analysis_settings, feature="summary",
                )
            data = parse_summary_json(content)
            html_text = render_summary_html(settings.bot_name, group_id, day, data, stats)
            height = estimate_height(data, stats)
            work_dir = Path("data/summaries")
            png = await render_html_to_png(html_text, height, work_dir)
            png_file = ""
            if png:
                png_file = str(work_dir / f"summary-{group_id}-{day}.png")
                await asyncio.to_thread(Path(png_file).write_bytes, png)
            await self.store.save_summary(group_id, day, html_text, png_file)
            await self.store.prune()
            return {"date": day, "html": html_text, "png": png, "data": data, "stats": stats}
        finally:
            self._inflight.discard(group_id)


async def send_summary_to_group(group_id: int, result: dict[str, Any]) -> bool:
    bot = next(iter(get_bots().values()), None)
    if bot is None:
        logger.warning("没有已连接的机器人，总结无法发送")
        return False
    if result.get("png"):
        message = Message([MessageSegment.image(result["png"])])
    else:
        overview = result.get("data", {}).get("overview") or "今日群聊总结已生成"
        message = Message(
            [MessageSegment.text(f"📊 {result.get('date', '')} 群聊总结\n{overview}\n（图片渲染暂不可用）")]
        )
    try:
        await bot.send_group_msg(group_id=group_id, message=message)
        return True
    except Exception as exc:
        logger.warning(f"总结发送失败：group={group_id}, {exc}")
        return False


def register_daily_summary(config_store: "RuntimeConfigStore", store: SummaryStore) -> None:
    matcher = on_message(priority=10, block=False)

    @matcher.handle()
    async def collect_for_summary(bot: Bot, event: MessageEvent) -> None:
        if not isinstance(event, GroupMessageEvent) or str(event.user_id) == str(bot.self_id):
            return
        settings = config_store.snapshot()
        if not (settings.summary_enabled or settings.jev_tools_enabled):
            return
        if int(event.group_id) not in settings.allowed_groups:
            return
        content = event.get_plaintext().strip()
        if not content:
            return
        sender = getattr(event, "sender", None)
        card = str(getattr(sender, "card", "") or "").strip()
        nickname = str(getattr(sender, "nickname", "") or "").strip()
        await store.record_message(
            int(event.group_id),
            int(event.user_id),
            card or nickname or str(event.user_id),
            content,
        )
