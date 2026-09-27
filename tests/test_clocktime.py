import unittest

from shiftcomply import ClockTimeError, parse_12h


class Parse12hTest(unittest.TestCase):
    def test_valid(self):
        cases = {
            "7:00 PM": (19, 0),
            "12:30 AM": (0, 30),
            "12:00 PM": (12, 0),
            "12:00 AM": (0, 0),
            "1:05 am": (1, 5),
            "11:59 PM": (23, 59),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                t = parse_12h(text)
                self.assertEqual((t.hour, t.minute), expected)

    def test_invalid(self):
        for bad in ["13:00 PM", "0:15 AM", "7:00", "7:00 XM", "25:00 AM",
                    "7:60 PM", "7 PM", "", "abc", "12:30AM PM", None]:
            with self.subTest(text=bad):
                with self.assertRaises(ClockTimeError):
                    parse_12h(bad)


if __name__ == "__main__":
    unittest.main()
