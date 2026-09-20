"""Deterministic parsing of one-off reminder times; Jev only selects the action."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReminderRequest:
    due_at: datetime
    text: str


def number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total, digit = 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        elif char in "十百":
            total += (digit or 1) * (10 if char == "十" else 100)
            digit = 0
        else:
            raise ValueError("无法识别时间中的数字")
    return total + digit


NUM = r"[0-9零〇一二两三四五六七八九十百]+"
RELATIVE = rf"(?P<count>{NUM}|半)\s*(?P<unit>分钟|小时|天)后"
ABSOLUTE = (
    r"(?P<day>今天|明天|后天|(?:下周|周|星期)[一二三四五六日天]|\d{4}[-/]\d{1,2}[-/]\d{1,2})"
    rf"\s*(?P<period>凌晨|早上|上午|中午|下午|晚上)?\s*(?P<hour>{NUM})"
    rf"(?:点|时|[:：])(?:(?P<minute>{NUM})分?|(?P<half>半))?"
)
TIME = re.compile(rf"(?:{RELATIVE}|{ABSOLUTE})")


def parse_reminder(text: str, tz_name: str, now: datetime | None = None) -> ReminderRequest:
    """No guessed dates: ambiguous, past, recurring, and out-of-range times are rejected."""
    tz = ZoneInfo(tz_name)
    moment = (now or datetime.now(timezone.utc)).astimezone(tz)
    query = re.sub(r"^(?:请帮我|请|麻烦|帮我)\s*", "", text.strip()).strip()
    for old, new in (("明早", "明天上午"), ("明晚", "明天晚上"), ("今早", "今天上午"), ("今晚", "今天晚上")):
        query = query.replace(old, new, 1)
    parts = re.fullmatch(r"(.*?)提醒我(?:一下)?[，,：:\s]*(.+)", query)
    if not parts:
        raise ValueError("请说明提醒时间和内容，例如：明早九点提醒我更新证书。")
    before, after = (part.strip() for part in parts.groups())
    if before:
        match = TIME.fullmatch(before)
        content = after
    else:
        match = TIME.match(after)
        content = after[match.end():].lstrip(" ，,：:") if match else ""
    if not match or not content.strip("。.!！ "):
        raise ValueError("请给出明确的日期、时间和内容，例如：明天上午九点提醒我更新证书。目前支持一次性提醒。")
    if len(content) > 300:
        raise ValueError("提醒内容请控制在 300 字以内。")
    groups = match.groupdict()
    if groups["unit"]:
        amount = 0.5 if groups["count"] == "半" else number(groups["count"])
        seconds = amount * {"分钟": 60, "小时": 3600, "天": 86400}[groups["unit"]]
        if not 0 < seconds <= 365 * 86400:
            raise ValueError("提醒时间请设在未来一年内。")
        due = (moment.astimezone(timezone.utc) + timedelta(seconds=seconds)).astimezone(tz)
    else:
        day = groups["day"]
        if day in {"今天", "明天", "后天"}:
            date = moment.date() + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[day])
        elif day.startswith(("周", "下周", "星期")):
            weekday = "一二三四五六日".index(day[-1].replace("天", "日"))
            delta = weekday - moment.weekday()
            if day.startswith("下周"):
                delta += 7
            date = moment.date() + timedelta(days=delta)
        else:
            try:
                date = datetime.strptime(day.replace("/", "-"), "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError("这个日期不存在，请重新指定。") from exc
        hour = number(groups["hour"])
        minute = 30 if groups["half"] else number(groups["minute"] or "0")
        period = groups["period"]
        if period and not 1 <= hour <= 12:
            raise ValueError("带上午、下午等时段时，请使用 1–12 点。")
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        elif period in {"凌晨", "早上", "上午"} and hour == 12:
            hour = 0
        elif period == "中午" and hour < 11:
            raise ValueError("中午几点不够明确，请使用 24 小时时间。")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("时间需要在 00:00–23:59 之间。")
        due = datetime(date.year, date.month, date.day, hour, minute, tzinfo=tz)
        if due.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != due.replace(tzinfo=None):
            raise ValueError("这个本地时间因夏令时不存在，请换一个时间。")
        if due.replace(fold=0).utcoffset() != due.replace(fold=1).utcoffset():
            raise ValueError("这个时间处于夏令时切换的重复时段，请换一个明确时间。")
    elapsed = due.astimezone(timezone.utc) - moment.astimezone(timezone.utc)
    if elapsed <= timedelta(0):
        raise ValueError("这个提醒时间已经过去，请指定未来时间。")
    if elapsed > timedelta(days=365):
        raise ValueError("提醒时间请设在未来一年内。")
    return ReminderRequest(due.astimezone(timezone.utc), content.strip())


def reminder_cancel_id(text: str) -> int:
    match = re.fullmatch(r"(?:请)?(?:取消|删除)(?:我的)?提醒\s*[#＃]?\s*(\d+)[。！!]?", text.strip())
    if not match:
        raise ValueError("请说明提醒编号，例如：取消提醒 12。可以先说“查看我的提醒”。")
    return int(match.group(1))
