"""Самопроверка базы, без реальных смен и Telegram."""
import tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from core import connect,toggle,shifts,paid_hours,totals,office_report,set_office,corrected_shift
class ShiftTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/"test.sqlite3")
 def tearDown(self):
  self.db.close();self.temp.cleanup()
 def test_toggle_idempotence_midnight(self):
  start=datetime(2026,11,12,13,8,tzinfo=timezone.utc)
  end=datetime(2026,11,12,23,10,tzinfo=timezone.utc)
  self.assertEqual(toggle(self.db,123,start)[0],"in")
  self.assertEqual(toggle(self.db,123,start)[0],"duplicate")
  self.assertEqual(toggle(self.db,124,end)[0],"out")
  self.assertAlmostEqual(paid_hours(shifts(self.db)[0]),9.533333333,places=3)
  self.assertAlmostEqual(totals(self.db,"2026-11")["earnings"],2860,places=0)
 def test_manual_office_not_overwritten(self):
  set_office(self.db,"2026-11-12",9.23)
  sid=corrected_shift(self.db,"2026-11-12","16:08","02:10",30)
  self.assertAlmostEqual(paid_hours(shifts(self.db)[0]),9.5333333,places=3)
  self.assertAlmostEqual(office_report(self.db)[0][3],0.3033333,places=3)
  with self.assertRaises(ValueError):corrected_shift(self.db,"2026-11-12","16:08","02:10",30)
  corrected_shift(self.db,"2026-11-12","16:10","02:12",30,sid)
  self.assertEqual(len(shifts(self.db)),1)
  self.assertAlmostEqual(office_report(self.db)[0][1],9.23,places=2)
if __name__=="__main__":unittest.main()
