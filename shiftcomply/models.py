"""Core domain types."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Department:
    id: int
    name: str
    iana_tz: str


@dataclass(frozen=True)
class Certificate:
    tag: str
    expiry_date: dt.date  # UTC date; valid through end of this day UTC


@dataclass(frozen=True)
class Employee:
    id: int
    name: str
    department_id: int
    certificates: tuple[Certificate, ...] = ()


@dataclass(frozen=True)
class ShiftTemplate:
    id: int
    department_id: int
    start_wall: str  # raw 12h string, e.g. '7:00 PM'
    end_wall: str
    staffing: int
    required_certs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShiftInstance:
    id: int
    template_id: int
    local_date: dt.date  # department-local date of the shift start


@dataclass(frozen=True)
class Assignment:
    employee_id: int
    shift_id: int


@dataclass(frozen=True)
class Violation:
    kind: str  # OVERLAP / REST / MAX_24H / MAX_WEEK / CERT / OVERSTAFF
    employee_id: int
    shift_ids: tuple[int, ...]
    message: str


@dataclass(frozen=True)
class Warning_:
    kind: str  # CERT_EXPIRING
    employee_id: int
    shift_id: int
    message: str


@dataclass
class Result:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Warning_] = field(default_factory=list)
