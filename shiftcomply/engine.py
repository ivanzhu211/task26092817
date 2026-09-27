"""Public operations: assign / unassign / swap.

Evaluation runs in memory against the candidate after-state; the database
is only mutated once the state is known to be compliant, and swap applies
its two moves in a single transaction. Rejected operations append every
violation to the append-only compliance log (with UTC timestamp + actor).
"""
from __future__ import annotations

import datetime as dt

from .models import Assignment, Result, Violation
from .rules import evaluate
from .store import Store


class Engine:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ----- helpers -----

    def _reject(self, actor: str, violations: list[Violation]) -> Result:
        now = dt.datetime.now(dt.timezone.utc)
        try:
            self._store.begin()
            for v in violations:
                self._store.append_log(now, actor, v)
            self._store.commit()
        except BaseException:
            self._store.rollback()
            raise
        return Result(ok=False, violations=violations)

    # ----- operations -----

    def assign(self, employee_id: int, shift_id: int, actor: str) -> Result:
        current = self._store.all_assignments()
        new = Assignment(employee_id, shift_id)
        if new in current:
            raise ValueError(f"employee {employee_id} already on shift {shift_id}")
        violations, warnings = evaluate(self._store, current + [new], [new])
        if violations:
            return self._reject(actor, violations)
        try:
            self._store.begin()
            self._store.insert_assignment(employee_id, shift_id)
            self._store.commit()
        except BaseException:
            self._store.rollback()
            raise
        return Result(ok=True, warnings=warnings)

    def unassign(self, employee_id: int, shift_id: int, actor: str) -> Result:
        try:
            self._store.begin()
            self._store.delete_assignment(employee_id, shift_id)
            self._store.commit()
        except BaseException:
            self._store.rollback()
            raise
        return Result(ok=True)

    def swap(
        self, a_id: int, shift_a_id: int, b_id: int, shift_b_id: int, actor: str
    ) -> Result:
        current = self._store.all_assignments()
        if Assignment(a_id, shift_a_id) not in current:
            raise ValueError(f"employee {a_id} is not on shift {shift_a_id}")
        if Assignment(b_id, shift_b_id) not in current:
            raise ValueError(f"employee {b_id} is not on shift {shift_b_id}")
        removed = [Assignment(a_id, shift_a_id), Assignment(b_id, shift_b_id)]
        added = [Assignment(a_id, shift_b_id), Assignment(b_id, shift_a_id)]
        after = [a for a in current if a not in removed] + added
        violations, warnings = evaluate(self._store, after, added)
        if violations:
            return self._reject(actor, violations)
        try:
            self._store.begin()
            self._store.delete_assignment(a_id, shift_a_id)
            self._store.delete_assignment(b_id, shift_b_id)
            self._store.insert_assignment(a_id, shift_b_id)
            self._store.insert_assignment(b_id, shift_a_id)
            self._store.commit()
        except BaseException:
            self._store.rollback()
            raise
        return Result(ok=True, warnings=warnings)
