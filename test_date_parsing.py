"""Yearless date inputs must be parsed with an explicit current year."""
import calendar
import unittest
from datetime import datetime

import edit_wizard
import extra_wizard
import finance_wizard
from core import TZ


class YearlessDateParsingTests(unittest.TestCase):
    def test_yearless_dates_resolve_to_current_moscow_year(self):
        year = datetime.now(TZ).year
        expected = f"{year}-11-02"
        self.assertEqual(edit_wizard.date_from_text("02.11"), expected)
        self.assertEqual(extra_wizard.parse_day("02.11"), expected)
        self.assertEqual(finance_wizard.check_date("02.11"), expected)

    def test_explicit_leap_day_is_preserved(self):
        expected = "2024-02-29"
        self.assertEqual(edit_wizard.date_from_text("29.02.2024"), expected)
        self.assertEqual(extra_wizard.parse_day("29.02.2024"), expected)
        self.assertEqual(finance_wizard.check_date("29.02.2024"), expected)

    def test_yearless_leap_day_is_rejected_in_non_leap_current_year(self):
        if calendar.isleap(datetime.now(TZ).year):
            self.skipTest("Current Moscow year is a leap year")
        for parser in (
            edit_wizard.date_from_text,
            extra_wizard.parse_day,
            finance_wizard.check_date,
        ):
            with self.subTest(parser=parser.__module__):
                with self.assertRaises(ValueError):
                    parser("29.02")


if __name__ == "__main__":
    unittest.main()
