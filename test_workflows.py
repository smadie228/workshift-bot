"""Offline tests for plan overrides, tracked breaks, payout adjustments and anomalies."""
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
import tempfile,unittest
import core,workflows

UTC=timezone.utc

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=core.connect(Path(self.temp.name)/"workflows.sqlite3")
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def test_breaks_two_sessions_with_default_legacy_rate_protected(self):
        start=datetime(2024,7,1,13,0,tzinfo=UTC)
        self.assertEqual(core.toggle(self.db,45001,start)[0],"in")
        self.assertEqual(core.open_shift(self.db)["break_minutes"],30)
        first=workflows.start_break(self.db,start+timedelta(hours=2))
        self.assertEqual(core.open_shift(self.db)["break_minutes"],0)
        with self.assertRaisesRegex(ValueError,"идёт"):
            workflows.start_break(self.db,start+timedelta(hours=2,minutes=1))
        with self.assertRaisesRegex(ValueError,"Перерыв"):
            core.toggle(self.db,45002,start+timedelta(hours=3))
        b=workflows.stop_break(self.db,start+timedelta(hours=2,minutes=15))
        self.assertEqual(b["minutes"],15)
        workflows.start_break(self.db,start+timedelta(hours=4))
        workflows.stop_break(self.db,start+timedelta(hours=4,minutes=5))
        action,shift=core.toggle(self.db,45002,start+timedelta(hours=8))
        self.assertEqual(action,"out")
        self.assertEqual(shift["break_minutes"],20)
        self.assertAlmostEqual(core.paid_hours(shift),7+2/3,places=5)
        self.assertIsNone(workflows.active_break(self.db))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM break_sessions").fetchone()[0],2)

    def test_missed_break_no_double_count_and_failure_rolls_back(self):
        start=datetime(2024,7,1,13,0,tzinfo=UTC)
        core.toggle(self.db,45101,start)
        with self.assertRaisesRegex(ValueError,"перерыв не идёт"):
            workflows.stop_break(self.db,start+timedelta(hours=1))
        self.assertEqual(core.open_shift(self.db)["break_minutes"],30)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM break_sessions").fetchone()[0],0)

    def test_manual_plan_overrides_import_without_deleting_archive(self):
        future=(datetime.now(core.TZ).date()+timedelta(days=2)).isoformat()
        self.db.execute("""INSERT INTO history_source_rows
            (source,sheet,excel_row,day,status,imported_utc)
            VALUES(?,?,?,?,?,?)""",
            ("fictional.xlsx","Sheet",7,future,"План",core.iso(core.now())))
        self.assertEqual(workflows.plan_day(self.db,future)["source"],"import")
        plan=workflows.set_plan(self.db,future,"off")
        self.assertEqual(plan["status"],"off")
        self.assertEqual(plan["source"],"manual")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM history_source_rows").fetchone()[0],1)
        workflows.set_plan(self.db,future,"shift","16:00","02:00")
        self.assertEqual(workflows.plan_day(self.db,future)["start"],"16:00")
        self.assertGreaterEqual(len(workflows.upcoming_plan(self.db)),1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='plan_change'").fetchone()[0],2)

    def test_adjustments_separate_from_original_pay(self):
        sid=core.corrected_shift(self.db,"2024-07-05","09:00","18:00",30)
        before=core.totals(self.db,"2024-07")["earnings"]
        workflows.add_adjustment(self.db,"2024-07-05","bonus","950.50")
        workflows.add_adjustment(self.db,"2024-07-05","deduction","120")
        workflows.add_adjustment(self.db,"2024-07-05","reimbursement","40")
        result=workflows.adjustment_totals(self.db,"2024-07")
        self.assertEqual(result["bonus"],950.5)
        self.assertEqual(result["deduction"],120)
        self.assertEqual(result["net_adjustment"],870.5)
        self.assertEqual(core.totals(self.db,"2024-07")["earnings"],before)

    def test_no_automatic_writes_from_anomaly_scan(self):
        counts=lambda : (
          self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
          self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0])
        pre=counts()
        rows=workflows.issues(self.db)
        self.assertEqual(counts(),pre)
        self.assertIsInstance(rows,list)

if __name__=="__main__":
    unittest.main()
