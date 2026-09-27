"""Weekly 60h rule: a shift crossing Sunday midnight splits across weeks."""
import datetime as dt
import unittest

from shiftcomply import Engine, Store


class WeeklyLimitTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.dept = self.store.add_department("ICU", "America/New_York")
        self.emp = self.store.add_employee(self.dept, "Carol")
        self.engine = Engine(self.store)
        t_day = self.store.add_template(self.dept, "8:00 AM", "8:00 PM", 1)
        t_short = self.store.add_template(self.dept, "8:00 AM", "1:00 PM", 1)
        t_night = self.store.add_template(self.dept, "8:00 PM", "8:00 AM", 1)
        # Week of Mon 2026-11-09 .. Mon 2026-11-16 local: pre-fill 53h.
        self.prefill = [
            self.store.create_shift(t_day, dt.date(2026, 11, d))
            for d in (11, 12, 13, 14)  # Wed-Sat, 12h each = 48h
        ]
        self.prefill.append(
            self.store.create_shift(t_short, dt.date(2026, 11, 15))  # Sun 5h
        )
        # Crossing shift: Sun 2026-11-08 8PM -> Mon 2026-11-09 8AM.
        # 4h lands in the prior week, 8h in the Nov-09 week -> 53 + 8 = 61 > 60.
        self.crossing = self.store.create_shift(t_night, dt.date(2026, 11, 8))

    def test_crossing_shift_pushes_next_week_over_60h(self):
        for sid in self.prefill:
            r = self.engine.assign(self.emp, sid, actor="sup")
            self.assertTrue(r.ok, r.violations)
        r = self.engine.assign(self.emp, self.crossing, actor="sup")
        self.assertFalse(r.ok)
        self.assertEqual([v.kind for v in r.violations], ["MAX_WEEK"])
        self.assertEqual(
            self.store.shift_ids_for_employee(self.emp), list(self.prefill)
        )

    def test_without_prefill_crossing_shift_is_fine(self):
        r = self.engine.assign(self.emp, self.crossing, actor="sup")
        self.assertTrue(r.ok, r.violations)


if __name__ == "__main__":
    unittest.main()
