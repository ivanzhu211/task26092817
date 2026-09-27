"""Compliance rule evaluation over a candidate assignment state.

All durations use real elapsed time (UTC). Violations are only reported
when they involve at least one of the newly added assignments; a 24h
window or local week counts as "involved" if it intersects a new shift.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .intervals import overlap_seconds, shift_interval_utc, week_bounds_utc
from .models import Assignment, Violation, Warning_
from .store import Store

REST_MIN = dt.timedelta(hours=10)
MAX_24H = dt.timedelta(hours=16)
MAX_WEEK = dt.timedelta(hours=60)
CERT_WARN_LEAD = dt.timedelta(days=30)
_WINDOW = dt.timedelta(hours=24)


@dataclass(frozen=True)
class ShiftView:
    shift_id: int
    start_utc: dt.datetime
    end_utc: dt.datetime
    template_id: int
    department_id: int


def _build_views(store: Store, shift_ids: set[int]) -> dict[int, ShiftView]:
    views: dict[int, ShiftView] = {}
    tz_cache: dict[int, ZoneInfo] = {}
    for shift_id in shift_ids:
        shift = store.get_shift(shift_id)
        template = store.get_template(shift.template_id)
        tz = tz_cache.setdefault(
            template.department_id,
            ZoneInfo(store.get_department(template.department_id).iana_tz),
        )
        start_utc, end_utc = shift_interval_utc(template, shift, tz)
        views[shift_id] = ShiftView(
            shift_id, start_utc, end_utc, template.id, template.department_id
        )
    return views


def _employee_tz(store: Store, employee_id: int, cache: dict) -> ZoneInfo:
    if employee_id not in cache:
        emp = store.get_employee(employee_id)
        cache[employee_id] = ZoneInfo(store.get_department(emp.department_id).iana_tz)
    return cache[employee_id]


def _check_overlap_rest(
    employee_id: int,
    intervals: list[ShiftView],
    added_ids: set[int],
    violations: list[Violation],
) -> None:
    ordered = sorted(intervals, key=lambda v: (v.start_utc, v.end_utc))
    overlap_shifts: set[int] = set()
    rest_shifts: set[int] = set()
    for prev, nxt in zip(ordered, ordered[1:]):
        pair_involved = prev.shift_id in added_ids or nxt.shift_id in added_ids
        if not pair_involved:
            continue
        gap = nxt.start_utc - prev.end_utc
        if gap < dt.timedelta(0):
            overlap_shifts.update((prev.shift_id, nxt.shift_id))
        elif dt.timedelta(0) < gap < REST_MIN:
            rest_shifts.update((prev.shift_id, nxt.shift_id))
    if overlap_shifts:
        violations.append(Violation(
            "OVERLAP", employee_id, tuple(sorted(overlap_shifts)),
            "shifts overlap in real time",
        ))
    if rest_shifts:
        violations.append(Violation(
            "REST", employee_id, tuple(sorted(rest_shifts)),
            "less than 10h rest between adjacent work blocks",
        ))


def _check_max_24h(
    employee_id: int,
    intervals: list[ShiftView],
    added: list[ShiftView],
    violations: list[Violation],
) -> None:
    anchors: list[dt.datetime] = []
    for v in intervals:
        anchors.append(v.start_utc)
        anchors.append(v.end_utc - _WINDOW)
    for anchor in anchors:
        w_start, w_end = anchor, anchor + _WINDOW
        total = dt.timedelta(0)
        for v in intervals:
            total += dt.timedelta(seconds=overlap_seconds(
                v.start_utc, v.end_utc, w_start, w_end))
        if total <= MAX_24H:
            continue
        involved = any(
            overlap_seconds(a.start_utc, a.end_utc, w_start, w_end) > 0
            for a in added
        )
        if involved:
            violations.append(Violation(
                "MAX_24H", employee_id,
                tuple(sorted(a.shift_id for a in added)),
                f"more than 16h work in a rolling 24h window"
                f" ({total} in window starting {w_start.isoformat()})",
            ))
            return


def _check_max_week(
    employee_id: int,
    intervals: list[ShiftView],
    added: list[ShiftView],
    tz: ZoneInfo,
    violations: list[Violation],
) -> None:
    totals: dict[dt.datetime, dt.timedelta] = {}
    bounds: dict[dt.datetime, dt.datetime] = {}
    for v in intervals:
        cursor = v.start_utc
        while cursor < v.end_utc:
            w_start, w_end = week_bounds_utc(cursor, tz)
            seg_end = min(v.end_utc, w_end)
            totals[w_start] = totals.get(w_start, dt.timedelta(0)) + (seg_end - cursor)
            bounds[w_start] = w_end
            cursor = seg_end
    for w_start, total in totals.items():
        if total <= MAX_WEEK:
            continue
        w_end = bounds[w_start]
        involved = any(
            overlap_seconds(a.start_utc, a.end_utc, w_start, w_end) > 0
            for a in added
        )
        if involved:
            violations.append(Violation(
                "MAX_WEEK", employee_id,
                tuple(sorted(a.shift_id for a in added)),
                f"more than 60h work in local week starting {w_start.isoformat()}"
                f" ({total})",
            ))
            return


def _check_certs(
    store: Store,
    employee_id: int,
    added: list[ShiftView],
    violations: list[Violation],
    warnings: list[Warning_],
) -> None:
    emp = store.get_employee(employee_id)
    certs = {c.tag: c.expiry_date for c in emp.certificates}
    bad_shifts: list[int] = []
    for view in added:
        template = store.get_template(view.template_id)
        for tag in template.required_certs:
            expiry = certs.get(tag)
            # Valid through end of expiry date UTC -> expires at next 00:00 UTC.
            expiry_moment = (
                dt.datetime.combine(expiry, dt.time.min, tzinfo=dt.timezone.utc)
                + dt.timedelta(days=1)
            ) if expiry is not None else None
            if expiry_moment is None or expiry_moment < view.end_utc:
                bad_shifts.append(view.shift_id)
                break
            if expiry_moment - view.start_utc < CERT_WARN_LEAD:
                warnings.append(Warning_(
                    "CERT_EXPIRING", employee_id, view.shift_id,
                    f"certificate {tag!r} expires {expiry.isoformat()} UTC,"
                    f" less than 30 days after shift start",
                ))
    if bad_shifts:
        violations.append(Violation(
            "CERT", employee_id, tuple(sorted(set(bad_shifts))),
            "required certificate missing or does not cover the whole shift",
        ))


def _check_overstaff(
    store: Store,
    after: list[Assignment],
    added: list[Assignment],
    violations: list[Violation],
) -> None:
    count_by_shift: dict[int, int] = {}
    for a in after:
        count_by_shift[a.shift_id] = count_by_shift.get(a.shift_id, 0) + 1
    for a in added:
        view_shift = store.get_shift(a.shift_id)
        template = store.get_template(view_shift.template_id)
        if count_by_shift[a.shift_id] > template.staffing:
            violations.append(Violation(
                "OVERSTAFF", a.employee_id, (a.shift_id,),
                f"shift staffed {count_by_shift[a.shift_id]}"
                f" over limit {template.staffing}",
            ))


def evaluate(
    store: Store,
    after: list[Assignment],
    added: list[Assignment],
) -> tuple[list[Violation], list[Warning_]]:
    """Validate the after-state; report only violations involving `added`."""
    violations: list[Violation] = []
    warnings: list[Warning_] = []
    if not added:
        return violations, warnings

    views = _build_views(store, {a.shift_id for a in after})
    added_by_emp: dict[int, list[ShiftView]] = {}
    all_by_emp: dict[int, list[ShiftView]] = {}
    for a in after:
        all_by_emp.setdefault(a.employee_id, []).append(views[a.shift_id])
    for a in added:
        added_by_emp.setdefault(a.employee_id, []).append(views[a.shift_id])

    tz_cache: dict[int, ZoneInfo] = {}
    for employee_id, added_views in added_by_emp.items():
        intervals = all_by_emp[employee_id]
        added_ids = {v.shift_id for v in added_views}
        _check_overlap_rest(employee_id, intervals, added_ids, violations)
        _check_max_24h(employee_id, intervals, added_views, violations)
        _check_max_week(
            employee_id, intervals, added_views,
            _employee_tz(store, employee_id, tz_cache), violations,
        )
        _check_certs(store, employee_id, added_views, violations, warnings)

    _check_overstaff(store, after, added, violations)
    return violations, warnings
