"""Rolling 24h rule: three back-to-back shifts across midnight.

Each calendar day holds <= 16h, but the rolling 24h window holds 24h.
"""
import datetime as dt
import unittest

from shiftcomply import Engine, Store


class Rolling24Test(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.dept = self.store.add_department("Fire", "America/New_York")
        self.emp = self.store.add_employee(self.dept, "Bob")
        self.engine = Engine(self.store)
        day = dt.date(2026, 11, 3)  # plain week, no DST transition
        t1 = self.store.add_template(self.dept, "8:00 AM", "4:00 PM", 1)
        t2 = self.store.add_template(self.dept, "4:00 PM", "12:00 AM", 1)
        t3 = self.store.add_template(self.dept, "12:00 AM", "8:00 AM", 1)
        self.s1 = self.store.create_shift(t1, day)               # 08-16 day1
        self.s2 = self.store.create_shift(t2, day)               # 16-24 day1
        self.s3 = self.store.create_shift(t3, day + dt.timedelta(days=1))  # 00-08 day2

    def test_third_shift_rejected_by_rolling_24h(self):
        # Back-to-back (gap 0) counts as continuous work: rest rule passes.
        self.assertTrue(self.engine.assign(self.emp, self.s1, actor="sup").ok)
        self.assertTrue(self.engine.assign(self.emp, self.s2, actor="sup").ok)
        # Day1 = 16h, day2 = 8h per calendar day, but rolling 24h = 24h > 16h.
        r = self.engine.assign(self.emp, self.s3, actor="sup")
        self.assertFalse(r.ok)
        self.assertEqual([v.kind for v in r.violations], ["MAX_24H"])
        self.assertEqual(self.store.shift_ids_for_employee(self.emp),
                         [self.s1, self.s2])


if __name__ == "__main__":
    unittest.main()
