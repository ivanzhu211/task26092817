"""合规规则校验：对“变更后状态”判定，返回与本次新增分配相关的全部违规与证书预警。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import CertificateWarning, Violation
from .store import Store

REST_HOURS = 10.0
MAX_WORK_24H = 16.0
MAX_WORK_WEEK = 60.0
CERT_WARN_DAYS = 30

_OVERLAP = "shift_overlap"
_REST = "insufficient_rest"
_WORK_24H = "exceeds_24h_window"
_WORK_WEEK = "exceeds_weekly_hours"
_CERT_MISSING = "certificate_missing"
_CERT_EXPIRED = "certificate_expired"
_OVER_CAPACITY = "over_capacity"


def _overlap_hours(
    s1: datetime, e1: datetime, s2: datetime, e2: datetime
) -> float:
    lo = max(s1, s2)
    hi = min(e1, e2)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 3600.0


def validate(
    store: Store,
    additions: set[tuple[str, str]],
    removals: set[tuple[str, str]],
) -> tuple[list[Violation], list[CertificateWarning]]:
    """对变更后的状态做校验。

    additions / removals 为本次变更将新增/移除的 (员工, 班次) 集合。
    只返回涉及 additions 中新增分配的违规；证书预警也只针对新增分配。
    """
    violations: list[Violation] = []
    warnings: list[CertificateWarning] = []
    seen: set[tuple] = set()

    def add_violation(v: Violation) -> None:
        key = (v.code, v.employee_id, v.shift_id, v.related_shift_ids)
        if key not in seen:
            seen.add(key)
            violations.append(v)

    affected_employees = sorted({emp for emp, _ in additions})

    # 预先按员工组织新增/移除
    added_by_emp: dict[str, set[str]] = {}
    removed_by_emp: dict[str, set[str]] = {}
    for emp, sid in additions:
        added_by_emp.setdefault(emp, set()).add(sid)
    for emp, sid in removals:
        removed_by_emp.setdefault(emp, set()).add(sid)

    for emp_id in affected_employees:
        emp = store.get_employee(emp_id)
        if emp is None:
            raise ValueError(f"员工不存在: {emp_id}")
        tz = ZoneInfo(store.get_department(emp.department_id).timezone)

        # 变更后状态下该员工的全部班次（UTC 窗口）
        current = set(store.list_employee_shifts(emp_id))
        post_ids = (
            current - removed_by_emp.get(emp_id, set())
        ) | added_by_emp.get(emp_id, set())
        windows: dict[str, tuple[datetime, datetime]] = {
            sid: store.get_shift_window_utc(sid) for sid in post_ids
        }
        ordered = sorted(post_ids, key=lambda sid: windows[sid][0])
        new_for_emp = added_by_emp.get(emp_id, set())

        # --- 重叠 + 相邻休息 ---
        for i in range(len(ordered) - 1):
            a_id, b_id = ordered[i], ordered[i + 1]
            a_s, a_e = windows[a_id]
            b_s, b_e = windows[b_id]
            involves_new = a_id in new_for_emp or b_id in new_for_emp
            if not involves_new:
                continue
            if a_e > b_s:
                add_violation(
                    Violation(
                        _OVERLAP,
                        emp_id,
                        b_id if b_id in new_for_emp else a_id,
                        (a_id, b_id),
                        f"班次时间重叠: {a_id} 与 {b_id}",
                    )
                )
            else:
                gap_hours = (b_s - a_e).total_seconds() / 3600.0
                if gap_hours < REST_HOURS:
                    add_violation(
                        Violation(
                            _REST,
                            emp_id,
                            b_id if b_id in new_for_emp else a_id,
                            (a_id, b_id),
                            f"相邻班次仅间隔 {gap_hours:g} 小时，不足 {REST_HOURS:g} 小时",
                        )
                    )

        # --- 任意连续 24 小时不超过 16 小时 ---
        # 窗口内工作量是分段线性函数，极值出现在“班次结束”和
        # “班次开始 + 24h”两类断点，枚举这些窗口即可覆盖全部极值。
        day = timedelta(hours=24)
        breakpoints: set[datetime] = set()
        for sid in ordered:
            s, e = windows[sid]
            breakpoints.add(e)
            breakpoints.add(s + day)
        reported_windows: set[frozenset[str]] = set()
        for t in breakpoints:
            w_s, w_e = t - day, t
            contributors = tuple(
                sid
                for sid in ordered
                if _overlap_hours(*windows[sid], w_s, w_e) > 0
            )
            total = sum(
                _overlap_hours(*windows[sid], w_s, w_e) for sid in contributors
            )
            if total > MAX_WORK_24H:
                key = frozenset(contributors)
                if key in reported_windows:
                    continue
                reported_windows.add(key)
                for new_sid in new_for_emp:
                    if new_sid in key:
                        add_violation(
                            Violation(
                                _WORK_24H,
                                emp_id,
                                new_sid,
                                tuple(sorted(contributors)),
                                f"连续24小时内工作 {total:g} 小时，超过 "
                                f"{MAX_WORK_24H:g} 小时",
                            )
                        )

        # --- 本地自然周不超过 60 小时（跨周班次按时刻拆分） ---
        from .timeutil import week_bounds_utc

        week_keys: set[tuple[datetime, datetime]] = set()
        for new_sid in new_for_emp:
            s, e = windows[new_sid]
            week_keys.add(week_bounds_utc(s, tz))
            week_keys.add(week_bounds_utc(e - timedelta(microseconds=1), tz))
        for w_s, w_e in week_keys:
            contributors = tuple(
                sid
                for sid in ordered
                if _overlap_hours(*windows[sid], w_s, w_e) > 0
            )
            total = sum(
                _overlap_hours(*windows[sid], w_s, w_e) for sid in contributors
            )
            if total > MAX_WORK_WEEK:
                for new_sid in new_for_emp:
                    if _overlap_hours(*windows[new_sid], w_s, w_e) > 0:
                        add_violation(
                            Violation(
                                _WORK_WEEK,
                                emp_id,
                                new_sid,
                                tuple(sorted(contributors)),
                                f"自然周内工作 {total:g} 小时，超过 "
                                f"{MAX_WORK_WEEK:g} 小时",
                            )
                        )

        # --- 证书：覆盖整段班次；不足 30 天预警 ---
        cert_expiry: dict[str, datetime] = {}
        for cert in emp.certificates:
            expiry = datetime.combine(
                cert.expires_on + timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            prev = cert_expiry.get(cert.tag)
            if prev is None or expiry > prev:
                cert_expiry[cert.tag] = expiry

        for new_sid in sorted(new_for_emp):
            inst_row = store.get_shift_instance_row(new_sid)
            tpl = store.get_shift_template(inst_row["template_id"])
            s, e = windows[new_sid]
            for tag in tpl.required_certificates:
                expiry = cert_expiry.get(tag)
                if expiry is None:
                    add_violation(
                        Violation(
                            _CERT_MISSING,
                            emp_id,
                            new_sid,
                            (new_sid,),
                            f"缺少所需证书: {tag}",
                        )
                    )
                elif expiry < e:
                    add_violation(
                        Violation(
                            _CERT_EXPIRED,
                            emp_id,
                            new_sid,
                            (new_sid,),
                            f"证书 {tag} 在班次结束前失效",
                        )
                    )
                elif expiry - s < timedelta(days=CERT_WARN_DAYS):
                    warnings.append(
                        CertificateWarning(
                            employee_id=emp_id,
                            shift_id=new_sid,
                            certificate_tag=tag,
                            expires_utc=expiry,
                        )
                    )

    # --- 编制：按变更后人数 ---
    added_shifts = sorted({sid for _, sid in additions})
    removed_by_shift: dict[str, set[str]] = {}
    for emp, sid in removals:
        removed_by_shift.setdefault(sid, set()).add(emp)
    added_by_shift: dict[str, set[str]] = {}
    for emp, sid in additions:
        added_by_shift.setdefault(sid, set()).add(emp)
    for sid in added_shifts:
        inst_row = store.get_shift_instance_row(sid)
        tpl = store.get_shift_template(inst_row["template_id"])
        post_assignees = (
            set(store.list_shift_assignees(sid))
            - removed_by_shift.get(sid, set())
        ) | added_by_shift.get(sid, set())
        if len(post_assignees) > tpl.headcount:
            for emp_id in sorted(added_by_shift.get(sid, set())):
                add_violation(
                    Violation(
                        _OVER_CAPACITY,
                        emp_id,
                        sid,
                        (sid,),
                        f"班次编制 {tpl.headcount} 人，变更后为 "
                        f"{len(post_assignees)} 人",
                    )
                )

    return violations, warnings
