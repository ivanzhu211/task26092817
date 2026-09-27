"""12-hour wall-clock parsing (e.g. '7:00 PM', '12:30 AM')."""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERN = re.compile(r"^\s*(\d{1,2}):([0-5]\d)\s*([AaPp])[Mm]\s*$")


class ClockTimeError(ValueError):
    """Raised when a 12-hour clock string is invalid."""


@dataclass(frozen=True)
class WallTime:
    """A time-of-day as (hour, minute) in 24h form, parsed from 12h input."""

    hour: int
    minute: int

    def to_minutes(self) -> int:
        return self.hour * 60 + self.minute


def parse_12h(text: str) -> WallTime:
    """Parse 'h:MM AM/PM'. Rejects 13:00 PM, 0:15 AM, missing AM/PM, etc."""
    if not isinstance(text, str):
        raise ClockTimeError(f"not a string: {text!r}")
    match = _PATTERN.match(text)
    if match is None:
        raise ClockTimeError(f"invalid 12-hour clock time: {text!r}")
    hour12, minute, ap = int(match.group(1)), int(match.group(2)), match.group(3).upper()
    if not 1 <= hour12 <= 12:
        raise ClockTimeError(f"hour out of 1..12 range: {text!r}")
    if ap == "A":
        hour = 0 if hour12 == 12 else hour12
    else:
        hour = 12 if hour12 == 12 else hour12 + 12
    return WallTime(hour, minute)
