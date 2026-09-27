"""Certificates, overstaffing, overlap, unassign, compliance log."""
import datetime as dt
import unittest

from shiftcomply import Engine, Store

DAY = dt.date(2026, 11, 2)


class CertTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.dept = self.store.add_department("ER", "America/New_York")
        self.emp = self.store.add_employee(self.dept, "Alice")
        self.engine = Engine(self.store)
        self.tmpl = self.store.add_template(
            self.dept, "7:00 AM", "7:00 PM", 1, ("ACLS",))
        self.shift = self.store.create_shift(self.tmpl, DAY)

    def test_missing_cert_rejected_and_logged(self):
        r = self.engine.assign(self.emp, self.shift, actor="sup")
        self.assertFalse(r.ok)
        self.assertEqual([v.kind for v in r.violations], ["CERT"])
        logs = self.store.log_entries()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["kind"], "CERT")
        self.assertEqual(logs[0]["actor"], "sup")
        dt.datetime.fromisoformat(logs[0]["ts_utc"])  # parses as UTC timestamp

    def test_expired_cert_rejected(self):
        # Shift is 2026-11-02 11:00Z - 23:00Z; cert expired end of 2026-11-01 UTC.
        self.store.add_certificate(self.emp, "ACLS", dt.date(2026, 11, 1))
        r = self.engine.assign(self.emp, self.shift, actor="sup")
        self.assertFalse(r.ok)
        self.assertEqual([v.kind for v in r.violations], ["CERT"])

    def test_expiry_day_itself_covers_shift_ending_that_day(self):
        # Cert valid through end of expiry day UTC; shift ends 2026-11-02 23:00Z.
        self.store.add_certificate(self.emp, "ACLS", dt.date(2026, 11, 2))
        r = self.engine.assign(self.emp, self.shift, actor="sup")
        self.assertTrue(r.ok, r.violations)
        # Expiry moment (2026-11-03 00:00Z) is < 30 days from start -> warning.
        self.assertEqual([w.kind for w in r.warnings], ["CERT_EXPIRING"])

    def test_far_future_expiry_no_warning(self):
        self.store.add_certificate(self.emp, "ACLS", dt.date(2027, 1, 1))
        r = self.engine.assign(self.emp, self.shift, actor="sup")
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(r.warnings, [])


class OverstaffTest(unittest.TestCase):
    def test_overstaff_rejected(self):
        store = Store()
        dept = store.add_department("ER", "America/New_York")
        e1 = store.add_employee(dept, "A")
        e2 = store.add_employee(dept, "B")
        t = store.add_template(dept, "7:00 AM", "7:00 PM", 1)  # staffing 1
        shift = store.create_shift(t, DAY)
        eng = Engine(store)
        self.assertTrue(eng.assign(e1, shift, actor="sup").ok)
        r = eng.assign(e2, shift, actor="sup")
        self.assertFalse(r.ok)
        self.assertEqual([v.kind for v in r.violations], ["OVERSTAFF"])
        self.assertEqual(store.shift_ids_for_employee(e2), [])


class OverlapAndUnassignTest(unittest.TestCase):
    def test_overlap_rejected_then_unassign_allows(self):
        store = Store()
        dept = store.add_department("ER", "America/New_York")
        emp = store.add_employee(dept, "A")
        t1 = store.add_template(dept, "7:00 AM", "7:00 PM", 1)
        t2 = store.add_template(dept, "6:00 PM", "10:00 PM", 1)
        s1 = store.create_shift(t1, DAY)
        s2 = store.create_shift(t2, DAY)
        eng = Engine(store)
        self.assertTrue(eng.assign(emp, s1, actor="sup").ok)
        r = eng.assign(emp, s2, actor="sup")
        self.assertFalse(r.ok)
        self.assertEqual([v.kind for v in r.violations], ["OVERLAP"])
        # unassign does no validation and frees the slot.
        r2 = eng.unassign(emp, s1, actor="sup")
        self.assertTrue(r2.ok)
        self.assertEqual(store.shift_ids_for_employee(emp), [])
        self.assertTrue(eng.assign(emp, s2, actor="sup").ok)


if __name__ == "__main__":
    unittest.main()
