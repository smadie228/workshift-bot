"""No-network checks for the in-chat work calendar."""
import tempfile
import unittest
from pathlib import Path
import core
import calendar_views

class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=core.connect(Path(self.temp.name)/"calendar.sqlite3")
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def test_month_calendar_marks_completed_day(self):
        core.corrected_shift(self.db,"2024-07-10","09:00","18:00",30)
        text,menu=calendar_views.month_view(self.db,"2024-07")
        self.assertIn("Июль 2024",text)
        buttons=[b for row in menu.inline_keyboard for b in row]
        self.assertTrue(any(b.text=="✅10" and b.callback_data=="day:2024-07-10" for b in buttons))
        self.assertTrue(all(len(b.callback_data or "")<=64 for b in buttons))
        self.assertLessEqual(len(buttons),80)
    def test_day_shows_hours_and_confirmed_source(self):
        core.corrected_shift(self.db,"2024-07-11","17:00","01:00",30)
        text,menu=calendar_views.day_view(self.db,"2024-07-11")
        self.assertIn("17:00",text)
        self.assertIn("01:00",text)
        self.assertIn("след. день",text)
        self.assertIn("Перерыв: 30",text)
        self.assertIn("2024-07",menu.inline_keyboard[-1][0].callback_data)
    def test_empty_day_offers_guided_correction(self):
        text,menu=calendar_views.day_view(self.db,"2024-07-12")
        self.assertIn("нет",text.lower())
        self.assertIn("edit:day:2024-07-12",str(menu.inline_keyboard))
    def test_future_day_must_not_offer_factual_add(self):
        text,menu=calendar_views.day_view(self.db,"2099-06-01")
        self.assertNotIn("edit:day:",str(menu.inline_keyboard))
    def test_invalid_months_are_rejected(self):
        for month in ("2024-13","2024-00","oops","2101-01"):
            with self.assertRaises(ValueError):
                calendar_views.month_view(self.db,month)
