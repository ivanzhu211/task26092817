"""Swap semantics: judged once against the post-swap state, applied atomically."""
import datetime as dt
import unittest

from shiftcomply import Engine, Store

DAY = dt.date(2026, 11, 2)  # Monday


class SwapTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.dept = self.store.add_department("ER", "America/New_York")
        self.a = self.store.add_employee(self.dept, "Ann")
        self.b = self.store.add_employee(self.dept, "Ben")
        self.engine = Engine(self.store)

    def _shift(self, start_wall, end_wall, local_date):
        t = self.store.add_template(self.dept, start_wall, end_wall, 1)
        return self.store.create_shift(t, local_date)

    def test_swap_passes_where_sequential_assigns_would_fail(self):
        # S1 and S2 overlap in time, so "assign A to S2 while A still holds
        # S1" (and vice versa) would each be rejected. The post-swap state
        # gives each employee exactly one shift and must pass.
        s1 = self._shift("8:00 AM", "4:00 PM", DAY)
        s2 = self._shift("12:00 PM", "8:00 PM", DAY)
        self.assertTrue(self.engine.assign(self.a, s1, actor="sup").ok)
        self.assertTrue(self.engine.assign(self.b, s2, actor="sup").ok)
        # Sanity: naive sequential assign really is rejected.
        self.assertFalse(self.engine.assign(self.a, s2, actor="sup").ok)
        r = self.engine.swap(self.a, s1, self.b, s2, actor="sup")
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(self.store.shift_ids_for_employee(self.a), [s2])
        self.assertEqual(self.store.shift_ids_for_employee(self.b), [s1])

    def test_swap_with_violation_leaves_both_sides_unchanged(self):
        s1 = self._shift("8:00 AM", "4:00 PM", DAY)            # Mon 08-16
        s2 = self._shift("6:00 PM", "2:00 AM", DAY + dt.timedelta(days=1))  # Tue 18 - Wed 02
        s3 = self._shift("8:00 PM", "2:00 AM", DAY)            # Mon 20 - Tue 02
        self.assertTrue(self.engine.assign(self.a, s1, actor="sup").ok)
        self.assertTrue(self.engine.assign(self.b, s2, actor="sup").ok)
        self.assertTrue(self.engine.assign(self.b, s3, actor="sup").ok)
        # Post-swap B would hold S1 (ends Mon 16:00) and S3 (starts Mon 20:00):
        # 4h rest < 10h -> whole swap rejected, nothing changes.
        r = self.engine.swap(self.a, s1, self.b, s2, actor="mgr-7")
        self.assertFalse(r.ok)
        self.assertIn("REST", [v.kind for v in r.violations])
        self.assertEqual(self.store.shift_ids_for_employee(self.a), [s1])
        self.assertEqual(
            sorted(self.store.shift_ids_for_employee(self.b)), sorted([s2, s3])
        )
        logs = self.store.log_entries()
        self.assertTrue(any(row["actor"] == "mgr-7" and row["kind"] == "REST"
                            for row in logs))


if __name__ == "__main__":
    unittest.main()
