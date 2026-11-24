"""Isolated reliability tests. Never connect to production DB or Telegram."""
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from openpyxl import Workbook,load_workbook
import core
import finance
import report
import report_catalog
import friendly_ui
import pro_assistant
import office_upload
from import_history import create_tables

class EnterpriseTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)
        self.db=core.connect(self.path/"test.sqlite3")
        create_tables(self.db)
        self.original_data=report_catalog.DATA_DIR
        report_catalog.DATA_DIR=self.path
        office_upload.DATA_DIR=self.path
    def tearDown(self):
        self.db.close()
        report_catalog.DATA_DIR=self.original_data
        office_upload.DATA_DIR=self.original_data
        self.folder.cleanup()

    def sample(self):
        return core.corrected_shift(self.db,"2026-10-05","16:00","02:00",30)

    def test_salary_rate_does_not_recalculate_old_month(self):
        sid=self.sample()
        previous=core.totals(self.db,"2026-10")["earnings"]
        target=(datetime.now(core.TZ).date()+timedelta(days=2)).isoformat()
        finance.schedule_rate(self.db,350,target)
        self.assertEqual(core.totals(self.db,"2026-10")["earnings"],previous)
        self.assertEqual(finance.rate_at(self.db,"2026-10-05"),300)
        self.assertEqual(finance.rate_at(self.db,target),350)

    def test_manual_shift_cannot_overlap(self):
        self.sample()
        with self.assertRaisesRegex(ValueError,"пересекается"):
            core.corrected_shift(self.db,"2026-10-06","01:00","07:00",0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],1)

    def test_payment_ledger_and_export(self):
        self.sample()
        finance.add_payment(self.db,"2026-10",1,"5000.25","2026-10-20")
        self.assertAlmostEqual(finance.payment_totals(self.db,"2026-10")[1],5000.25)
        path=report.report(self.db,self.path/"full.xlsx")
        wb=load_workbook(path,read_only=True,data_only=True)
        self.assertIn("Обзор",wb.sheetnames)
        self.assertIn("Поступления",wb.sheetnames)
        self.assertIn("Сверка с офисом",wb.sheetnames)
        self.assertIn("История ставок",wb.sheetnames)
        self.assertAlmostEqual(wb["Поступления"].cell(7,3).value,5000.25)
        self.assertAlmostEqual(core.totals(self.db,"2026-10")["earnings"],2850)
        wb.close()

    def test_office_xlsx_preview_does_not_write_office_hours(self):
        wb=Workbook()
        ws=wb.active
        ws.append(["Дата","05.10.2026","06.10.2026"])
        ws.append(["Часы",9.4,8.2])
        path=self.path/"office.xlsx"
        wb.save(path)
        names,dates=office_upload.inspect(path)
        self.assertEqual(len(names),1)
        self.assertEqual(dates,2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM office_days").fetchone()[0],0)

    def test_views_and_assistant_are_private_read_only(self):
        self.sample()
        before=self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        for name,view in friendly_ui.VIEWS.items():
            txt,menu=view(self.db)
            self.assertLess(len(txt),4000,name)
            self.assertTrue(menu.inline_keyboard,name)
            for row in menu.inline_keyboard:
                for b in row:
                    self.assertLessEqual(len(b.callback_data or ""),64)
        self.assertIn("2 850",pro_assistant.answer(self.db,"pay") if datetime.now(core.TZ).strftime("%Y-%m")=="2026-10" else
                      f"{core.totals(self.db,'2026-10')['earnings']:,.0f}".replace(","," "))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],before)

    def test_stale_shift_needs_manual_close(self):
        from datetime import timezone
        start=datetime(2026,11,9,13,0,tzinfo=timezone.utc)
        action,s=core.toggle(self.db,801,start)
        self.assertEqual(action,"in")
        with self.assertRaisesRegex(ValueError,"дольше 24 часов"):
            core.toggle(self.db,802,start+timedelta(hours=49))
        self.assertIsNotNone(core.open_shift(self.db))
        core.close_open_shift(self.db,s["id"],"02:10",30)
        self.assertIsNone(core.open_shift(self.db))
        self.assertGreater(core.totals(self.db,"2026-11")["hours"],0)

    def test_future_completed_shift_rejected(self):
        day=(datetime.now(core.TZ).date()+timedelta(days=3)).isoformat()
        with self.assertRaisesRegex(ValueError,"будущего"):
            core.corrected_shift(self.db,day,"16:00","23:00",0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],0)

if __name__=="__main__":
    unittest.main()
