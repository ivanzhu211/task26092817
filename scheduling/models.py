"""核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Department:
    id: str
    timezone: str  # IANA 名称，如 America/New_York


@dataclass(frozen=True)
class Certificate:
    tag: str
    expires_on: date  # UTC 日期；当天 UTC 结束（次日 00:00 UTC）前有效


@dataclass(frozen=True)
class Employee:
    id: str
    department_id: str
    certificates: tuple[Certificate, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ShiftTemplate:
    id: str
    department_id: str
    start_text: str  # 12 小时制，如 '7:00 PM'
    end_text: str  # 12 小时制，如 '7:00 AM'
    headcount: int
    required_certificates: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ShiftInstance:
    template_id: str
    local_date: date


@dataclass(frozen=True)
class Violation:
    code: str
    employee_id: str
    shift_id: str
    related_shift_ids: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""


@dataclass(frozen=True)
class CertificateWarning:
    employee_id: str
    shift_id: str
    certificate_tag: str
    expires_utc: datetime


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    warnings: tuple[CertificateWarning, ...] = field(default_factory=tuple)
