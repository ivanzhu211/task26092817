"""Local-wall-clock to UTC interval conversion.

Rules (per spec):
- fold=0 always: nonexistent local times use the pre-transition offset,
  repeated local times resolve to the earlier occurrence.
- Durations are real elapsed time (UTC difference), never wall-clock diff.
- If end wall time <= start wall time, the shift ends the next local day.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .clocktime import parse_12h
from .models import ShiftInstance, ShiftTemplate

_ONE_DAY = dt.timedelta(days=1)


def localize(naive: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    """Attach tz with fold=0 (gap -> pre-transition offset, overlap -> earlier)."""
    return naive.replace(tzinfo=tz, fold=0)


def shift_interval_utc(
    template: ShiftTemplate, shift: ShiftInstance, tz: ZoneInfo
) -> tuple[dt.datetime, dt.datetime]:
    """Return (start_utc, end_utc) as aware UTC datetimes."""
    start_wall = parse_12h(template.start_wall)
    end_wall = parse_12h(template.end_wall)
    start_local = dt.datetime.combine(
        shift.local_date, dt.time(start_wall.hour, start_wall.minute)
    )
    end_date = shift.local_date
    if (end_wall.hour, end_wall.minute) <= (start_wall.hour, start_wall.minute):
        end_date = shift.local_date + _ONE_DAY
    end_local = dt.datetime.combine(end_date, dt.time(end_wall.hour, end_wall.minute))
    start_utc = localize(start_local, tz).astimezone(dt.timezone.utc)
    end_utc = localize(end_local, tz).astimezone(dt.timezone.utc)
    return start_utc, end_utc


def week_bounds_utc(
    moment_utc: dt.datetime, tz: ZoneInfo
) -> tuple[dt.datetime, dt.datetime]:
    """UTC bounds of the local week (Mon 00:00 .. next Mon 00:00) containing moment."""
    local = moment_utc.astimezone(tz)
    monday = local.date() - dt.timedelta(days=local.weekday())
    start_local = dt.datetime.combine(monday, dt.time.min)
    start_utc = localize(start_local, tz).astimezone(dt.timezone.utc)
    end_local = start_local + dt.timedelta(days=7)
    end_utc = localize(end_local, tz).astimezone(dt.timezone.utc)
    return start_utc, end_utc


def overlap_seconds(
    a_start: dt.datetime, a_end: dt.datetime,
    b_start: dt.datetime, b_end: dt.datetime,
) -> float:
    """Length of intersection of two UTC intervals, in seconds (>= 0)."""
    latest = max(a_start, b_start)
    earliest = min(a_end, b_end)
    return max(0.0, (earliest - latest).total_seconds())
