"""排班合规引擎测试（时区统一使用 America/New_York）。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from scheduling import (
    Certificate,
    ComplianceEngine,
    Department,
    Employee,
    ShiftInstance,
    ShiftTemplate,
    Store,
)
from scheduling.store import shift_instance_id
from scheduling.timeutil import parse_clock_12h, shift_window_utc
from zoneinfo import ZoneInfo

NY = "America/New_York"
FAR_FUTURE = date(2030, 1, 1)


class EngineTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.engine = ComplianceEngine(self.store)
        self.store.add_department(Department("h", NY))
        self.tz = ZoneInfo(NY)

    def make_employee(self, emp_id: str, certs=("BLS",), until=FAR_FUTURE):
        self.store.add_employee(
            Employee(
                emp_id,
                "h",
                tuple(Certificate(tag, until) for tag in certs),
            )
        )

    def make_template(self, tpl_id, start_text, end_text, headcount=1,
                      required=("BLS",)):
        self.store.add_shift_template(
            ShiftTemplate(
                tpl_id, "h", start_text, end_text, headcount, tuple(required)
            )
        )

    def make_shift(self, tpl_id, local_date) -> str:
        self.store.add_shift_instance(
            ShiftInstance(tpl_id, local_date)
        )
        return shift_instance_id(tpl_id, local_date)

    def seed_assign(self, emp_id, shift_id) -> None:
        """绕过引擎直接落库，模拟既有排班。"""
        self.store.assign(emp_id, shift_id)


class DstRestTest(EngineTestBase):
    """2026 秋夜：墙钟隔9h实际隔10h可排；春夜：墙钟隔10h实际隔9h拒绝。"""

    def test_fall_back_gap_allowed(self) -> None:
        self.make_employee("e1")
        # 第一班 22:60? -> 23:00 -> 次日 00:30（1.5h，重复小时在下班后）
        self.make_template("TA", "11:00 PM", "12:30 AM")
        # 第二班 09:30 -> 17:30
        self.make_template("TB", "9:30 AM", "5:30 PM")
        s1 = self.make_shift("TA", date(2026, 10, 31))
        s2 = self.make_shift("TB", date(2026, 11, 1))

        w1 = self.store.get_shift_window_utc(s1)
        w2 = self.store.get_shift_window_utc(s2)
        # 墙钟隔 9 小时，实际隔 10 小时
        self.assertEqual(
            (w2[0] - w1[1]).total_seconds() / 3600.0, 10.0
        )

        r1 = self.engine.assign("mgr", "e1", s1)
        self.assertTrue(r1.ok, r1.violations)
        r2 = self.engine.assign("mgr", "e1", s2)
        self.assertTrue(r2.ok, r2.violations)

    def test_spring_forward_gap_rejected(self) -> None:
        self.make_employee("e1")
        self.make_template("TA", "11:00 PM", "12:30 AM")
        self.make_template("TB", "10:30 AM", "6:30 PM")
        s1 = self.make_shift("TA", date(2026, 3, 7))
        s2 = self.make_shift("TB", date(2026, 3, 8))

        w1 = self.store.get_shift_window_utc(s1)
        w2 = self.store.get_shift_window_utc(s2)
        # 墙钟隔 10 小时，实际隔 9 小时
        self.assertEqual(
            (w2[0] - w1[1]).total_seconds() / 3600.0, 9.0
        )

        r1 = self.engine.assign("mgr", "e1", s1)
        self.assertTrue(r1.ok, r1.violations)
        r2 = self.engine.assign("mgr", "e1", s2)
        self.assertFalse(r2.ok)
        self.assertIn("insufficient_rest", [v.code for v in r2.violations])
        # 状态不变
        self.assertEqual(self.store.list_employee_shifts("e1"), [s1])
        # 违规已追加日志
        logs = self.store.list_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["actor"], "mgr")

class Rolling24hTest(EngineTestBase):
    """三连班跨午夜：每个自然日都不超过16小时，但滚动24小时超限。"""

    def test_natural_days_fine_rolling_window_exceeds(self) -> None:
        self.make_employee("e1")
        # A 23:00-01:00（2h，跨午夜）；B 05:00-15:00（10h）；
        # C 19:00-07:00（12h，跨午夜）。
        self.make_template("TA", "11:00 PM", "1:00 AM")
        self.make_template("TB", "5:00 AM", "3:00 PM")
        self.make_template("TC", "7:00 PM", "7:00 AM")
        s1 = self.make_shift("TA", date(2026, 6, 22))  # Mon 晚
        s2 = self.make_shift("TB", date(2026, 6, 23))  # Tue 白天
        s3 = self.make_shift("TC", date(2026, 6, 23))  # Tue 晚 -> Wed
        self.seed_assign("e1", s1)
        self.seed_assign("e1", s2)

        # 每个自然日工作均不超过16小时
        windows = {
            sid: self.store.get_shift_window_utc(sid)
            for sid in (s1, s2, s3)
        }
        for day in [date(2026, 6, d) for d in range(22, 25)]:
            day_start = datetime(
                day.year, day.month, day.day, tzinfo=self.tz
            ).astimezone(timezone.utc)
            day_end = day_start + timedelta(hours=24)
            worked = 0.0
            for a, b in windows.values():
                lo, hi = max(a, day_start), min(b, day_end)
                if hi > lo:
                    worked += (hi - lo).total_seconds() / 3600.0
            self.assertLessEqual(worked, 16.0)

        # 但滚动24小时窗口（如 Tue 07:00 -> Wed 07:00）合计 20 小时
        r = self.engine.assign("mgr", "e1", s3)
        self.assertFalse(r.ok)
        codes = [v.code for v in r.violations]
        self.assertIn("exceeds_24h_window", codes)
        # 整笔拒绝，状态不变
        self.assertEqual(
            set(self.store.list_employee_shifts("e1")), {s1, s2}
        )

class WeeklyHoursTest(EngineTestBase):
    """跨周日午夜的班次，按下周一为界拆分后让下一周刚好超过60小时。"""

    def test_cross_sunday_midnight_pushes_next_week_over_60(self) -> None:
        self.make_employee("e1")
        # 下一周（周一11/2 起）5 个 12 小时夜班：19:00 -> 07:00
        self.make_template("N", "7:00 PM", "7:00 AM")
        # 周日 18:00 -> 06:00（跨午夜，6h 落在下一周）
        self.make_template("S", "6:00 PM", "6:00 AM")
        weekdays = [
            self.make_shift("N", date(2026, 11, d)) for d in (2, 3, 4, 5, 6)
        ]
        sunday = self.make_shift("S", date(2026, 11, 1))
        for sid in weekdays:
            self.seed_assign("e1", sid)

        r = self.engine.assign("mgr", "e1", sunday)
        self.assertFalse(r.ok)
        codes = [v.code for v in r.violations]
        self.assertIn("exceeds_weekly_hours", codes)
        # 休息间隔、滚动24h 均不违规：相邻间隔均 >=12h
        self.assertNotIn("insufficient_rest", codes)
        self.assertNotIn("exceeds_24h_window", codes)
        self.assertNotIn("shift_overlap", codes)
        # 状态不变
        self.assertEqual(
            set(self.store.list_employee_shifts("e1")), set(weekdays)
        )
        week_violations = [
            v for v in r.violations if v.code == "exceeds_weekly_hours"
        ]
        self.assertEqual(len(week_violations), 1)  # 仅下一周超限
        self.assertEqual(week_violations[0].shift_id, sunday)

        # 60h 的边界：去掉一个工作日夜班（48h + 6h = 54h）即可通过
        self.store.unassign("e1", weekdays[4])
        r2 = self.engine.assign("mgr", "e1", sunday)
        self.assertTrue(r2.ok, r2.violations)


class SwapTest(EngineTestBase):
    def _build_swap_base(self):
        """s1/s2 重叠；既有 e1->s1, e2->s2。交换后双方各自仍只有一班。"""
        self.make_employee("e1")
        self.make_employee("e2")
        self.make_template("T1", "7:00 AM", "7:00 PM")  # 12h 日班
        self.make_template("T2", "7:00 AM", "7:00 PM")
        s1 = self.make_shift("T1", date(2026, 6, 1))
        s2 = self.make_shift("T2", date(2026, 6, 1))
        self.seed_assign("e1", s1)
        self.seed_assign("e2", s2)
        return s1, s2

    def test_swap_passes_but_sequential_assign_would_be_rejected(self) -> None:
        s1, s2 = self._build_swap_base()

        # 拆成两次 assign 依次校验：e1 尚未离开 s1 就拿 s2，必然重叠误拒
        sequential = self.engine.assign("mgr", "e1", s2)
        self.assertFalse(sequential.ok)
        self.assertIn(
            "shift_overlap", [v.code for v in sequential.violations]
        )
        # 顺序化尝试已被整笔拒绝、状态不变（日志已追加）
        self.assertEqual(self.store.list_employee_shifts("e1"), [s1])

        # 一次性对交换后的状态判定：应通过且原子生效
        r = self.engine.swap("mgr", "e1", s1, "e2", s2)
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(self.store.list_employee_shifts("e1"), [s2])
        self.assertEqual(self.store.list_employee_shifts("e2"), [s1])
        self.assertEqual(self.store.list_shift_assignees(s1), ["e2"])
        self.assertEqual(self.store.list_shift_assignees(s2), ["e1"])

    def test_swap_with_one_side_violation_changes_nothing(self) -> None:
        s1, s2 = self._build_swap_base()
        # 给 e1 再加一个与 s2 重叠之外、且与 s1 不冲突的晚班 s3；
        # 交换后 e2 拿到 s1 没问题，但 e1 同时持 s2 与 s3 -> 违规。
        self.make_template("T3", "7:00 PM", "7:00 AM")  # 与 s1 首尾相接
        s3 = self.make_shift("T3", date(2026, 6, 1))
        self.seed_assign("e1", s3)

        r = self.engine.swap("mgr", "e1", s1, "e2", s2)
        self.assertFalse(r.ok)
        codes = [v.code for v in r.violations]
        # s2 与 s3 首尾相接（间隔0），按连续工作处理：休息不足，
        # 且连续24小时内工作达24小时
        self.assertIn("insufficient_rest", codes)
        self.assertIn("exceeds_24h_window", codes)
        # 双方排班都不变
        self.assertEqual(set(self.store.list_employee_shifts("e1")), {s1, s3})
        self.assertEqual(set(self.store.list_employee_shifts("e2")), {s2})
        # 编制仍为各班 1 人
        self.assertEqual(self.store.list_shift_assignees(s1), ["e1"])
        self.assertEqual(self.store.list_shift_assignees(s2), ["e2"])
        self.assertEqual(self.store.list_shift_assignees(s3), ["e1"])


class CertificateTest(EngineTestBase):
    def _one_shift(self, start_text="7:00 AM", end_text="3:00 PM",
                   local_date=date(2026, 6, 1), required=("BLS",)):
        self.make_template("T", start_text, end_text, required=required)
        return self.make_shift("T", local_date)

    def test_missing_certificate_rejected(self) -> None:
        self.make_employee("e1", certs=("ACLS",))  # 没有 BLS
        sid = self._one_shift()
        r = self.engine.assign("mgr", "e1", sid)
        self.assertFalse(r.ok)
        self.assertEqual(r.violations[0].code, "certificate_missing")
        self.assertEqual(self.store.list_employee_shifts("e1"), [])

    def test_expiry_day_utc_is_valid(self) -> None:
        # 班次 07:00-15:00 纽约 = 11:00-19:00 UTC；
        # 到期日 2026-06-01 当天 UTC 结束（06-02 00:00 UTC）前覆盖整段。
        self.make_employee("e1", until=date(2026, 6, 1))
        sid = self._one_shift()
        r = self.engine.assign("mgr", "e1", sid)
        self.assertTrue(r.ok, r.violations)

    def test_certificate_expires_during_shift_rejected(self) -> None:
        # 到期于 06-01 15:00 UTC，班次到 19:00 UTC，未覆盖整段
        self.make_employee("e1", until=date(2026, 6, 1))
        # 用一个跨 UTC 午夜的班次：10:00 PM -> 8:00 AM（02:00 -> 12:00 UTC+1）
        sid = self._one_shift("10:00 PM", "8:00 AM", date(2026, 6, 1))
        r = self.engine.assign("mgr", "e1", sid)
        self.assertFalse(r.ok)
        self.assertEqual(r.violations[0].code, "certificate_expired")

    def test_warning_within_30_days_but_not_at_boundary(self) -> None:
        # 班次开始 2026-06-01 11:00 UTC；到期时刻 2026-06-31 00:00 UTC
        # 即 2026-07-01 00:00 UTC，间隔 29天13小时 < 30天 => 预警
        self.make_employee("e1", until=date(2026, 6, 30))
        sid = self._one_shift()
        r = self.engine.assign("mgr", "e1", sid)
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(r.warnings[0].certificate_tag, "BLS")

    def test_no_warning_at_exactly_30_days_or_more(self) -> None:
        self.make_employee("e1", until=date(2026, 7, 1))
        sid = self._one_shift()
        r = self.engine.assign("mgr", "e1", sid)
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(r.warnings, ())


class CapacityAndMiscTest(EngineTestBase):
    def test_headcount_enforced(self) -> None:
        self.make_employee("e1")
        self.make_employee("e2")
        self.make_template("T", "7:00 AM", "3:00 PM", headcount=1)
        sid = self.make_shift("T", date(2026, 6, 1))
        self.assertTrue(self.engine.assign("mgr", "e1", sid).ok)
        r = self.engine.assign("mgr", "e2", sid)
        self.assertFalse(r.ok)
        self.assertEqual(r.violations[0].code, "over_capacity")
        self.assertEqual(self.store.list_shift_assignees(sid), ["e1"])

    def test_unassign_does_no_validation(self) -> None:
        self.make_employee("e1", certs=())  # 无任何所需证书
        self.make_template("T", "7:00 AM", "3:00 PM")
        sid = self.make_shift("T", date(2026, 6, 1))
        self.seed_assign("e1", sid)  # 既有非法状态
        # unassign 不校验，直接成功
        r = self.engine.unassign("anyone", "e1", sid)
        self.assertTrue(r.ok)
        self.assertEqual(self.store.list_employee_shifts("e1"), [])
        self.assertEqual(self.store.list_logs(), [])

    def test_invalid_12h_text_rejected_at_load(self) -> None:
        for bad in ("13:00 PM", "0:15 AM", "7:00", "12:00 XM"):
            with self.assertRaises(ValueError):
                self.store.add_shift_template(
                    ShiftTemplate(
                        "X" + bad, "h", bad, "3:00 PM", 1, ()
                    )
                )

    def test_invalid_timezone_rejected_at_load(self) -> None:
        with self.assertRaises(Exception):
            self.store.add_department(Department("bad", "Not/AZone"))


class CrossWeekAndDurationTest(EngineTestBase):
    def test_shifts_across_week_boundary_participate_in_rest_check(self) -> None:
        # 周日晚班跨入新一周；紧接着周一16:00的班次在不同自然周，
        # 实际只隔 9 小时，必须被休息规则抓住（不能只看本周）。
        self.make_employee("e1")
        self.make_template("NS", "7:00 PM", "7:00 AM")   # Sun 19:00->Mon 07:00
        self.make_template("MT", "4:00 PM", "11:00 PM")  # Mon 16:00->23:00
        sunday = self.make_shift("NS", date(2026, 11, 1))
        monday = self.make_shift("MT", date(2026, 11, 2))
        self.assertTrue(self.engine.assign("mgr", "e1", sunday).ok)
        r = self.engine.assign("mgr", "e1", monday)
        self.assertFalse(r.ok)
        self.assertIn(
            "insufficient_rest", [v.code for v in r.violations]
        )

    def test_real_elapsed_hours_on_dst_night(self) -> None:
        # 秋夜夜班 11h 制模板但真实时长 13h：19:00 Nov1(EDT) -> 08:00 Nov2
        # 用 7:00 PM -> 8:00 AM 覆盖回落夜（2026-11-01 02:00 重复）。
        self.make_template("F", "7:00 PM", "8:00 AM")
        sid = self.make_shift("F", date(2026, 11, 1))
        s, e = self.store.get_shift_window_utc(sid)
        self.assertEqual((e - s).total_seconds() / 3600.0, 13.0)

        # 春夜 7:00 PM -> 6:00 AM：墙钟跨度11h，跨过02:00跳变，真实10h
        self.make_template("G", "7:00 PM", "6:00 AM")
        sid2 = self.make_shift("G", date(2026, 3, 7))
        s, e = self.store.get_shift_window_utc(sid2)
        self.assertEqual((e - s).total_seconds() / 3600.0, 10.0)

    def test_exactly_30_days_is_not_a_warning_utc(self) -> None:
        # 用 UTC 部门精确构造：班次 00:00 UTC 开始，到期时刻恰好 +30 天。
        self.store.add_department(Department("u", "UTC"))
        self.store.add_employee(
            Employee(
                "u1",
                "u",
                (Certificate("BLS", date(2026, 7, 1)),),  # 07-02 00:00 UTC 失效
            )
        )
        self.store.add_shift_template(
            ShiftTemplate("U", "u", "12:00 AM", "8:00 AM", 1, ("BLS",))
        )
        self.store.add_shift_instance(ShiftInstance("U", date(2026, 6, 2)))
        sid = shift_instance_id("U", date(2026, 6, 2))  # 开始即 06-02 00:00 UTC
        r = ComplianceEngine(self.store).assign("mgr", "u1", sid)
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(r.warnings, ())



if __name__ == "__main__":
    unittest.main()
