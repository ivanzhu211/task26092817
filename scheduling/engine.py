"""排班合规引擎：assign / unassign / swap，带 actor，原子生效。"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import OperationResult
from .rules import validate
from .store import Store


class ComplianceEngine:
    def __init__(self, store: Store) -> None:
        self._store = store

    def _check_employee_shift(self, employee_id: str, shift_id: str) -> None:
        emp = self._store.get_employee(employee_id)
        if emp is None:
            raise ValueError(f"员工不存在: {employee_id}")
        inst_row = self._store.get_shift_instance_row(shift_id)
        if inst_row is None:
            raise ValueError(f"班次不存在: {shift_id}")
        tpl = self._store.get_shift_template(inst_row["template_id"])
        if tpl.department_id != emp.department_id:
            raise ValueError(
                f"员工 {employee_id} 与班次 {shift_id} 不属于同一部门"
            )

    def assign(self, actor: str, employee_id: str, shift_id: str) -> OperationResult:
        self._check_employee_shift(employee_id, shift_id)
        current = set(self._store.list_employee_shifts(employee_id))
        if shift_id in current:
            return OperationResult(ok=True)

        additions = {(employee_id, shift_id)}
        violations, warnings = validate(self._store, additions, set())
        if violations:
            self._store.append_log(
                datetime.now(timezone.utc), actor, "assign", violations
            )
            return OperationResult(ok=False, violations=tuple(violations))

        self._store.assign(employee_id, shift_id)
        return OperationResult(ok=True, warnings=tuple(warnings))

    def unassign(
        self, actor: str, employee_id: str, shift_id: str
    ) -> OperationResult:
        # 不做任何校验
        self._store.unassign(employee_id, shift_id)
        return OperationResult(ok=True)

    def swap(
        self,
        actor: str,
        employee_a: str,
        shift_a: str,
        employee_b: str,
        shift_b: str,
    ) -> OperationResult:
        self._check_employee_shift(employee_a, shift_a)
        self._check_employee_shift(employee_b, shift_b)
        if shift_a == shift_b or employee_a == employee_b:
            raise ValueError("swap 的员工与班次必须互不相同")

        a_current = set(self._store.list_employee_shifts(employee_a))
        b_current = set(self._store.list_employee_shifts(employee_b))
        if shift_a not in a_current or shift_b not in b_current:
            raise ValueError("swap 前提不成立：员工未被分配到对应班次")
        if shift_b in a_current or shift_a in b_current:
            raise ValueError("swap 前提不成立：目标班次已被该员工占用")

        # 关键：作为同一组增删一次性判定，不拆成两次 assign。
        additions = {
            (employee_a, shift_b),
            (employee_b, shift_a),
        }
        removals = {
            (employee_a, shift_a),
            (employee_b, shift_b),
        }
        violations, warnings = validate(self._store, additions, removals)
        if violations:
            self._store.append_log(
                datetime.now(timezone.utc), actor, "swap", violations
            )
            return OperationResult(ok=False, violations=tuple(violations))

        self._store.apply_swap(
            employee_a, shift_a, employee_b, shift_b
        )
        return OperationResult(ok=True, warnings=tuple(warnings))
