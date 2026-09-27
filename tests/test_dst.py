"""DST transition scenarios in America/New_York."""
import datetime as dt
import unittest

from shiftcomply import Engine, Store

NY = "America/New_York"


class DstRestTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.dept = self.store.add_department("ER", NY)
        self.emp = self.store.add_employee(self.dept, "Alice")
        self.engine = Engine(self.store)

    def _shift(self, start_wall, end_wall, local_date):
        t = self.store.add_template(self.dept, start_wall, end_wall, 1)
        return self.store.create_shift(t, local_date)

    def test_fall_back_wall9h_real10h_allowed(self):
        # 2026-11-01 02:00 EDT -> EST. Shift A ends 01:00 (fold=0 -> EDT, 05:00Z).
        # Shift B starts 10:00 EST (15:00Z). Wall gap 9h, real gap 10h -> OK.
        a = self._shift("9:00 PM", "1:00 AM", dt.date(2026, 10, 31))
        b = self._shift("10:00 AM", "6:00 PM", dt.date(2026, 11, 1))
        r1 = self.engine.assign(self.emp, a, actor="sup")
        self.assertTrue(r1.ok, r1.violations)
        r2 = self.engine.assign(self.emp, b, actor="sup")
        self.assertTrue(r2.ok, r2.violations)

    def test_spring_forward_wall10h_real9h_rejected(self):
        # 2026-03-08 02:00 EST -> EDT. Shift A ends 01:00 EST (06:00Z).
        # Shift B starts 11:00 EDT (15:00Z). Wall gap 10h, real gap 9h -> REST.
        a = self._shift("9:00 PM", "1:00 AM", dt.date(2026, 3, 7))
        b = self._shift("11:00 AM", "7:00 PM", dt.date(2026, 3, 8))
        r1 = self.engine.assign(self.emp, a, actor="sup")
        self.assertTrue(r1.ok, r1.violations)
        r2 = self.engine.assign(self.emp, b, actor="sup")
        self.assertFalse(r2.ok)
        self.assertEqual([v.kind for v in r2.violations], ["REST"])
        # State unchanged: employee still only on shift A.
        self.assertEqual(self.store.shift_ids_for_employee(self.emp), [a])
        # Violation logged with actor.
        logs = self.store.log_entries()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["actor"], "sup")
        self.assertEqual(logs[0]["kind"], "REST")


if __name__ == "__main__":
    unittest.main()
