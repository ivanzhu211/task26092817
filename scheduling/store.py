"""SQLite 持久化：数据装载与分配状态读写（合规日志只追加）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .models import (
    Certificate,
    Department,
    Employee,
    ShiftInstance,
    ShiftTemplate,
    Violation,
)
from .timeutil import parse_clock_12h, shift_window_utc


def shift_instance_id(template_id: str, local_date: date) -> str:
    return f"{template_id}@{local_date.isoformat()}"


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self._conn.close()

    def _create_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id TEXT PRIMARY KEY,
                timezone TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employees (
                id TEXT PRIMARY KEY,
                department_id TEXT NOT NULL REFERENCES departments(id)
            );

            CREATE TABLE IF NOT EXISTS certificates (
                employee_id TEXT NOT NULL REFERENCES employees(id),
                tag TEXT NOT NULL,
                expires_on TEXT NOT NULL,
                PRIMARY KEY (employee_id, tag)
            );

            CREATE TABLE IF NOT EXISTS shift_templates (
                id TEXT PRIMARY KEY,
                department_id TEXT NOT NULL REFERENCES departments(id),
                start_text TEXT NOT NULL,
                end_text TEXT NOT NULL,
                headcount INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS template_required_certs (
                template_id TEXT NOT NULL REFERENCES shift_templates(id),
                tag TEXT NOT NULL,
                PRIMARY KEY (template_id, tag)
            );

            CREATE TABLE IF NOT EXISTS shift_instances (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL REFERENCES shift_templates(id),
                local_date TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assignments (
                employee_id TEXT NOT NULL REFERENCES employees(id),
                shift_id TEXT NOT NULL REFERENCES shift_instances(id),
                PRIMARY KEY (employee_id, shift_id)
            );

            CREATE TABLE IF NOT EXISTS compliance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                violation_code TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                shift_id TEXT NOT NULL,
                related_shift_ids TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );

            CREATE TRIGGER IF NOT EXISTS compliance_log_no_update
            BEFORE UPDATE ON compliance_log
            BEGIN
                SELECT RAISE(ABORT, 'compliance_log is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS compliance_log_no_delete
            BEFORE DELETE ON compliance_log
            BEGIN
                SELECT RAISE(ABORT, 'compliance_log is append-only');
            END;
            """
        )
        self._conn.commit()

    # --- 装载 ---
    def add_department(self, dept: Department) -> None:
        ZoneInfo(dept.timezone)  # 装载时拒绝非法时区
        self._conn.execute(
            "INSERT INTO departments(id, timezone) VALUES (?, ?)",
            (dept.id, dept.timezone),
        )
        self._conn.commit()

    def add_employee(self, emp: Employee) -> None:
        if self.get_department(emp.department_id) is None:
            raise ValueError(f"部门不存在: {emp.department_id}")
        with self._conn:
            self._conn.execute(
                "INSERT INTO employees(id, department_id) VALUES (?, ?)",
                (emp.id, emp.department_id),
            )
            self._conn.executemany(
                "INSERT INTO certificates(employee_id, tag, expires_on) "
                "VALUES (?, ?, ?)",
                [
                    (emp.id, cert.tag, cert.expires_on.isoformat())
                    for cert in emp.certificates
                ],
            )

    def add_shift_template(self, tpl: ShiftTemplate) -> None:
        dept = self.get_department(tpl.department_id)
        if dept is None:
            raise ValueError(f"部门不存在: {tpl.department_id}")
        # 装载时拒绝非法的 12 小时制输入
        parse_clock_12h(tpl.start_text)
        parse_clock_12h(tpl.end_text)
        if tpl.headcount < 1:
            raise ValueError("编制人数必须 >= 1")
        with self._conn:
            self._conn.execute(
                "INSERT INTO shift_templates"
                "(id, department_id, start_text, end_text, headcount) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    tpl.id,
                    tpl.department_id,
                    tpl.start_text,
                    tpl.end_text,
                    tpl.headcount,
                ),
            )
            self._conn.executemany(
                "INSERT INTO template_required_certs(template_id, tag) "
                "VALUES (?, ?)",
                [(tpl.id, tag) for tag in tpl.required_certificates],
            )

    def add_shift_instance(self, inst: ShiftInstance) -> None:
        tpl = self.get_shift_template(inst.template_id)
        if tpl is None:
            raise ValueError(f"班次模板不存在: {inst.template_id}")
        dept = self.get_department(tpl.department_id)
        start_clock = parse_clock_12h(tpl.start_text)
        end_clock = parse_clock_12h(tpl.end_text)
        start_utc, end_utc = shift_window_utc(
            inst.local_date, start_clock, end_clock, ZoneInfo(dept.timezone)
        )
        sid = shift_instance_id(inst.template_id, inst.local_date)
        self._conn.execute(
            "INSERT INTO shift_instances"
            "(id, template_id, local_date, start_utc, end_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                sid,
                inst.template_id,
                inst.local_date.isoformat(),
                start_utc.isoformat(),
                end_utc.isoformat(),
            ),
        )
        self._conn.commit()

    # --- 查询 ---
    def get_department(self, dept_id: str) -> Department | None:
        row = self._conn.execute(
            "SELECT id, timezone FROM departments WHERE id = ?", (dept_id,)
        ).fetchone()
        if row is None:
            return None
        return Department(id=row["id"], timezone=row["timezone"])

    def get_employee(self, emp_id: str) -> Employee | None:
        row = self._conn.execute(
            "SELECT id, department_id FROM employees WHERE id = ?", (emp_id,)
        ).fetchone()
        if row is None:
            return None
        cert_rows = self._conn.execute(
            "SELECT tag, expires_on FROM certificates WHERE employee_id = ?",
            (emp_id,),
        ).fetchall()
        certs = tuple(
            Certificate(tag=r["tag"], expires_on=date.fromisoformat(r["expires_on"]))
            for r in cert_rows
        )
        return Employee(
            id=row["id"], department_id=row["department_id"], certificates=certs
        )

    def get_shift_template(self, template_id: str) -> ShiftTemplate | None:
        row = self._conn.execute(
            "SELECT id, department_id, start_text, end_text, headcount "
            "FROM shift_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if row is None:
            return None
        tag_rows = self._conn.execute(
            "SELECT tag FROM template_required_certs WHERE template_id = ? ORDER BY tag",
            (template_id,),
        ).fetchall()
        return ShiftTemplate(
            id=row["id"],
            department_id=row["department_id"],
            start_text=row["start_text"],
            end_text=row["end_text"],
            headcount=row["headcount"],
            required_certificates=tuple(r["tag"] for r in tag_rows),
        )

    def get_shift_instance_row(self, shift_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT id, template_id, local_date, start_utc, end_utc "
            "FROM shift_instances WHERE id = ?",
            (shift_id,),
        ).fetchone()

    def get_shift_window_utc(self, shift_id: str) -> tuple[datetime, datetime]:
        row = self.get_shift_instance_row(shift_id)
        if row is None:
            raise ValueError(f"班次不存在: {shift_id}")
        return (
            datetime.fromisoformat(row["start_utc"]),
            datetime.fromisoformat(row["end_utc"]),
        )

    def list_employee_shifts(self, emp_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT shift_id FROM assignments WHERE employee_id = ?",
            (emp_id,),
        ).fetchall()
        return [r["shift_id"] for r in rows]

    def list_shift_assignees(self, shift_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT employee_id FROM assignments WHERE shift_id = ?",
            (shift_id,),
        ).fetchall()
        return [r["employee_id"] for r in rows]

    # --- 分配变更 ---
    def assign(self, emp_id: str, shift_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO assignments(employee_id, shift_id) VALUES (?, ?)",
            (emp_id, shift_id),
        )
        self._conn.commit()

    def unassign(self, emp_id: str, shift_id: str) -> None:
        self._conn.execute(
            "DELETE FROM assignments WHERE employee_id = ? AND shift_id = ?",
            (emp_id, shift_id),
        )
        self._conn.commit()

    def apply_swap(
        self,
        emp_a: str,
        shift_a: str,
        emp_b: str,
        shift_b: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM assignments WHERE employee_id = ? AND shift_id = ?",
                (emp_a, shift_a),
            )
            self._conn.execute(
                "DELETE FROM assignments WHERE employee_id = ? AND shift_id = ?",
                (emp_b, shift_b),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO assignments(employee_id, shift_id) "
                "VALUES (?, ?)",
                (emp_a, shift_b),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO assignments(employee_id, shift_id) "
                "VALUES (?, ?)",
                (emp_b, shift_a),
            )

    # --- 合规日志（只追加） ---
    def append_log(
        self,
        timestamp_utc: datetime,
        actor: str,
        action: str,
        violations: list[Violation],
    ) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO compliance_log"
                "(ts_utc, actor, action, violation_code, employee_id, shift_id, "
                "related_shift_ids, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        timestamp_utc.isoformat(),
                        actor,
                        action,
                        v.code,
                        v.employee_id,
                        v.shift_id,
                        json.dumps(list(v.related_shift_ids), ensure_ascii=False),
                        v.detail,
                    )
                    for v in violations
                ],
            )

    def list_logs(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT id, ts_utc, actor, action, violation_code, employee_id, "
            "shift_id, related_shift_ids, detail FROM compliance_log ORDER BY id"
        ).fetchall()
