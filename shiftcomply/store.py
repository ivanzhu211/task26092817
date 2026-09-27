"""SQLite persistence + data loading API."""
from __future__ import annotations

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clocktime import parse_12h
from .models import (
    Assignment,
    Certificate,
    Department,
    Employee,
    ShiftInstance,
    ShiftTemplate,
    Violation,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    iana_tz TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id)
);
CREATE TABLE IF NOT EXISTS certificates (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    tag TEXT NOT NULL,
    expiry_date TEXT NOT NULL,  -- ISO date, UTC
    PRIMARY KEY (employee_id, tag)
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    start_wall TEXT NOT NULL,
    end_wall TEXT NOT NULL,
    staffing INTEGER NOT NULL,
    UNIQUE (department_id, start_wall, end_wall)
);
CREATE TABLE IF NOT EXISTS template_certs (
    template_id INTEGER NOT NULL REFERENCES templates(id),
    tag TEXT NOT NULL,
    PRIMARY KEY (template_id, tag)
);
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES templates(id),
    local_date TEXT NOT NULL,  -- ISO date in department-local time
    UNIQUE (template_id, local_date)
);
CREATE TABLE IF NOT EXISTS assignments (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    PRIMARY KEY (employee_id, shift_id)
);
CREATE TABLE IF NOT EXISTS compliance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    employee_id INTEGER NOT NULL,
    shift_ids TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


class Store:
    """Owns the SQLite connection; loading API validates on write."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ----- loading API -----

    def add_department(self, name: str, iana_tz: str) -> int:
        try:
            ZoneInfo(iana_tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {iana_tz!r}") from exc
        cur = self._conn.execute(
            "INSERT INTO departments (name, iana_tz) VALUES (?, ?)", (name, iana_tz)
        )
        self._conn.commit()
        return cur.lastrowid

    def add_employee(self, department_id: int, name: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO employees (name, department_id) VALUES (?, ?)",
            (name, department_id),
        )
        self._conn.commit()
        return cur.lastrowid

    def add_certificate(self, employee_id: int, tag: str, expiry_date: dt.date) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO certificates (employee_id, tag, expiry_date)"
            " VALUES (?, ?, ?)",
            (employee_id, tag, expiry_date.isoformat()),
        )
        self._conn.commit()

    def add_template(
        self,
        department_id: int,
        start_wall: str,
        end_wall: str,
        staffing: int,
        required_certs: tuple[str, ...] = (),
    ) -> int:
        parse_12h(start_wall)  # raises ClockTimeError on invalid input
        parse_12h(end_wall)
        if staffing < 1:
            raise ValueError("staffing must be >= 1")
        cur = self._conn.execute(
            "INSERT INTO templates (department_id, start_wall, end_wall, staffing)"
            " VALUES (?, ?, ?, ?)",
            (department_id, start_wall, end_wall, staffing),
        )
        template_id = cur.lastrowid
        self._conn.executemany(
            "INSERT INTO template_certs (template_id, tag) VALUES (?, ?)",
            [(template_id, tag) for tag in required_certs],
        )
        self._conn.commit()
        return template_id

    def create_shift(self, template_id: int, local_date: dt.date) -> int:
        cur = self._conn.execute(
            "INSERT INTO shifts (template_id, local_date) VALUES (?, ?)",
            (template_id, local_date.isoformat()),
        )
        self._conn.commit()
        return cur.lastrowid

    # ----- reads -----

    def get_department(self, department_id: int) -> Department:
        row = self._conn.execute(
            "SELECT * FROM departments WHERE id = ?", (department_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no department {department_id}")
        return Department(row["id"], row["name"], row["iana_tz"])

    def get_employee(self, employee_id: int) -> Employee:
        row = self._conn.execute(
            "SELECT * FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no employee {employee_id}")
        certs = tuple(
            Certificate(r["tag"], dt.date.fromisoformat(r["expiry_date"]))
            for r in self._conn.execute(
                "SELECT tag, expiry_date FROM certificates WHERE employee_id = ?",
                (employee_id,),
            )
        )
        return Employee(row["id"], row["name"], row["department_id"], certs)

    def get_template(self, template_id: int) -> ShiftTemplate:
        row = self._conn.execute(
            "SELECT * FROM templates WHERE id = ?", (template_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no template {template_id}")
        certs = tuple(
            r["tag"]
            for r in self._conn.execute(
                "SELECT tag FROM template_certs WHERE template_id = ?", (template_id,)
            )
        )
        return ShiftTemplate(
            row["id"], row["department_id"], row["start_wall"], row["end_wall"],
            row["staffing"], certs,
        )

    def get_shift(self, shift_id: int) -> ShiftInstance:
        row = self._conn.execute(
            "SELECT * FROM shifts WHERE id = ?", (shift_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no shift {shift_id}")
        return ShiftInstance(
            row["id"], row["template_id"], dt.date.fromisoformat(row["local_date"])
        )

    def all_assignments(self) -> list[Assignment]:
        return [
            Assignment(r["employee_id"], r["shift_id"])
            for r in self._conn.execute("SELECT employee_id, shift_id FROM assignments")
        ]

    def shift_ids_for_employee(self, employee_id: int) -> list[int]:
        return [
            r["shift_id"]
            for r in self._conn.execute(
                "SELECT shift_id FROM assignments WHERE employee_id = ?",
                (employee_id,),
            )
        ]

    # ----- assignment mutations (called by Engine inside its transaction) -----

    def insert_assignment(self, employee_id: int, shift_id: int) -> None:
        self._conn.execute(
            "INSERT INTO assignments (employee_id, shift_id) VALUES (?, ?)",
            (employee_id, shift_id),
        )

    def delete_assignment(self, employee_id: int, shift_id: int) -> None:
        self._conn.execute(
            "DELETE FROM assignments WHERE employee_id = ? AND shift_id = ?",
            (employee_id, shift_id),
        )

    def append_log(self, ts_utc: dt.datetime, actor: str, violation: Violation) -> None:
        self._conn.execute(
            "INSERT INTO compliance_log (ts_utc, actor, kind, employee_id, shift_ids, message)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                ts_utc.isoformat(),
                actor,
                violation.kind,
                violation.employee_id,
                ",".join(str(s) for s in violation.shift_ids),
                violation.message,
            ),
        )

    def log_entries(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM compliance_log ORDER BY id"))

    # ----- transaction hooks used by Engine -----

    def begin(self) -> None:
        self._conn.execute("BEGIN")

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()
