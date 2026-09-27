"""时区感知时间换算（仅 zoneinfo + datetime）。"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_CLOCK_12H_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*$",
    re.IGNORECASE,
)


def parse_clock_12h(text: str) -> time:
    """解析 12 小时制时刻，如 '7:00 PM'；非法输入抛 ValueError。"""
    if not isinstance(text, str):
        raise ValueError(f"非法的12小时制时刻: {text!r}")
    match = _CLOCK_12H_RE.match(text)
    if match is None:
        raise ValueError(f"非法的12小时制时刻: {text!r}")
    hour12 = int(match.group(1))
    minute = int(match.group(2))
    meridiem = match.group(3).upper()
    if not 1 <= hour12 <= 12:
        raise ValueError(f"非法的小时（必须在1-12之间）: {text!r}")
    if not 0 <= minute <= 59:
        raise ValueError(f"非法的分钟: {text!r}")
    if meridiem == "AM":
        hour24 = 0 if hour12 == 12 else hour12
    else:
        hour24 = 12 if hour12 == 12 else hour12 + 12
    return time(hour=hour24, minute=minute)


def local_to_utc(day: date, clock: time, tz: ZoneInfo) -> datetime:
    """把部门本地（日期, 时刻）换算成 UTC datetime。

    不存在的本地时刻按跳变前偏移（PEP 495, fold=0）；
    重复的本地时刻取较早那次（fold=0）。
    """
    naive = datetime.combine(day, clock)
    aware = naive.replace(tzinfo=tz)  # fold 默认 0
    return aware.astimezone(timezone.utc)


def shift_window_utc(
    day: date, start: time, end: time, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """返回班次实例的 (UTC开始, UTC结束)；结束不晚于开始即跨到次日。"""
    start_utc = local_to_utc(day, start, tz)
    end_day = day
    if end <= start:
        end_day = day + timedelta(days=1)
    end_utc = local_to_utc(end_day, end, tz)
    return start_utc, end_utc


def week_bounds_utc(moment: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """返回 moment 所属部门本地自然周（周一00:00 起）的 UTC 边界。"""
    local = moment.astimezone(tz)
    monday = local.date() - timedelta(days=local.weekday())
    start_utc = local_to_utc(monday, time(0, 0), tz)
    return start_utc, start_utc + timedelta(weeks=1)
