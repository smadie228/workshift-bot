"""Product regressions: one ledger, no invented confirmations, usable XLSX and chat."""
import tempfile,unittest,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
from openpyxl import load_workbook,Workbook
import core,finance,ledger,report_month,report,report_catalog,miniapp_server,friendly_ui,bot
from test_callbacks import FakeQuery

class LedgerUXTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=core.connect(self.root/'fixture.sqlite3')
        core.put_config(self.db,'owner_id','111')
        core.corrected_shift(self.db,'2024-07-10','16:00','02:00',30)
        self.db.execute("INSERT INTO rate_history VALUES(?,?,?,?)",('2024-07-15','400.00',core.iso(core.now()),'Fictional fixture'))
        core.corrected_shift(self.db,'2024-07-16','09:00','17:00',0)
        core.set_office(self.db,'2024-07-10',0,'Fictional confirmed zero')
        for day in ('2024-07-16','2024-07-20','2024-07-25'):
            self.db.execute('INSERT INTO plan_days VALUES(?,?,?,?,?,?,?)',(day,'shift','09:00','18:00','',core.iso(core.now()),core.iso(core.now())))
        self.ref=datetime(2024,7,22,12,tzinfo=timezone.utc)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def model(self):return ledger.month_ledger(self.db,'2024-07',self.ref)
    def test_read_only_projection_preserves_every_table(self):
        before='\n'.join(self.db.iterdump());data=self.model()
        self.assertEqual('\n'.join(self.db.iterdump()),before)
        self.assertEqual(len(data['days']),31)
    def test_single_totals_match_current_core_and_historical_rates(self):
        data=self.model();old=core.totals(self.db,'2024-07')
        self.assertEqual(data['summary']['earned'],old['earnings'])
        self.assertAlmostEqual(data['summary']['hours'],old['hours'])
        self.assertEqual(data['summary']['earned'],6050)
        self.assertEqual([r['rate'] for r in data['rates']],[300,400])
    def test_overnight_and_confirmed_zero_are_not_missing(self):
        rows={r['day']:r for r in self.model()['days']}
        self.assertTrue(rows['2024-07-10']['records'][0]['next_day'])
        self.assertEqual(rows['2024-07-10']['office_hours'],0)
        self.assertEqual(rows['2024-07-10']['delta_hours'],9.5)
        self.assertIsNone(rows['2024-07-16']['office_hours'])
        self.assertIsNone(rows['2024-07-16']['delta_hours'])
    def test_forecast_excludes_worked_and_missed_plan(self):
        data=self.model();forecast=data['forecast']
        self.assertEqual(forecast['future_shifts'],1)
        self.assertAlmostEqual(forecast['future_hours'],8.5)
        self.assertEqual(forecast['future_pay'],3400)
        self.assertEqual(data['days'][19]['status'],'missing')
        self.assertEqual(data['summary']['earned'],6050)
    def test_forecast_excludes_open_shift(self):
        core.toggle(self.db,12,datetime(2024,7,25,6,tzinfo=timezone.utc))
        data=self.model();self.assertEqual(data['forecast']['future_shifts'],0)
        self.assertEqual(data['days'][24]['status'],'open')
    def test_payments_and_adjustment_only_months_are_visible(self):
        finance.add_payment(self.db,'2024-03',1,50,'2024-04-01','Fictional')
        self.db.execute('INSERT INTO pay_adjustments(day,category,amount_rub,note,created_utc) VALUES(?,?,?,?,?)',('2024-04-02','bonus','25.00','Fictional',core.iso(core.now())))
        self.assertIn('2024-03',ledger.available_months(self.db));self.assertIn('2024-04',ledger.available_months(self.db))
        march=ledger.month_ledger(self.db,'2024-03',self.ref)
        self.assertEqual(march['summary']['earned'],0);self.assertEqual(march['summary']['received'],50)
    def test_periods_adjustments_are_not_received_money(self):
        self.db.execute('INSERT INTO pay_adjustments(day,category,amount_rub,note,created_utc) VALUES(?,?,?,?,?)',('2024-07-16','bonus','250.00','Fictional',core.iso(core.now())))
        finance.add_payment(self.db,'2024-07',1,1000,'2024-07-18','Fictional')
        data=self.model();a,b=data['periods']
        self.assertEqual(a['total'],2850);self.assertEqual(a['received'],1000)
        self.assertEqual(b['total'],3450);self.assertEqual(b['received'],0)
        self.assertEqual(data['summary']['total'],6300)
    def test_week_chart_belongs_to_selected_month(self):
        data=self.model()
        self.assertAlmostEqual(sum(w['hours'] for w in data['weeks']),17.5)
        self.assertTrue(all(w['label'].endswith('.07') for w in data['weeks']))
    def test_month_validation_and_leap_day(self):
        for value in ('2024-2','2024-13','1999-12',None,3):
            with self.assertRaises(ValueError):ledger.parse_month(value)
        self.assertEqual(len(ledger.month_ledger(self.db,'2024-02',self.ref)['days']),29)
    def test_monthly_workbook_formulas_and_cached_values(self):
        before='\n'.join(self.db.iterdump())
        path=report_month.monthly_report(self.db,'2024-07',self.root/'month.xlsx')
        formulas=load_workbook(path,data_only=False);values=load_workbook(path,data_only=True)
        self.assertEqual(values.sheetnames,['Обзор','Табель','Смены','Деньги'])
        self.assertAlmostEqual(values['Обзор']['B7'].value.total_seconds()/3600,17.5)
        self.assertEqual(values['Обзор']['B8'].value,6050)
        self.assertEqual(values['Обзор']['B10'].value,6050)
        self.assertEqual(values['Табель']['G16'].value.total_seconds(),0)
        self.assertIsNone(values['Табель']['G22'].value)
        self.assertEqual(formulas['Смены']['G7'].data_type,'f')
        self.assertEqual(values['Смены']['G7'].value,2850)
        self.assertIsInstance(values['Табель']['A7'].value,datetime)
        self.assertEqual(formulas['Табель']['A39'].value,'Итого по фильтру')
        self.assertEqual(values['Табель']['F39'].value,6050)
        self.assertEqual(len(formulas['Табель'].tables),1)
        self.assertEqual(formulas['Обзор']._charts[0].anchor._from.row,21)
        self.assertEqual('\n'.join(self.db.iterdump()),before)
        formulas.close();values.close()
    def test_empty_month_and_formula_injection(self):
        finance.add_payment(self.db,'2024-03',1,1,'2024-03-20','=HYPERLINK("bad","bad")')
        path=report_month.monthly_report(self.db,'2024-03',self.root/'empty.xlsx')
        book=load_workbook(path,data_only=False)
        self.assertNotEqual(book['Деньги']['E7'].data_type,'f')
        self.assertTrue(book['Деньги']['E7'].value.startswith("'="))
        book.close();book=load_workbook(path,data_only=True)
        self.assertEqual(book['Обзор']['B8'].value,0);book.close()
    def test_archive_chart_does_not_cover_table_and_actual_dates_are_visible(self):
        path=report.report(self.db,self.root/'all.xlsx');book=load_workbook(path)
        table=next(iter(book['Обзор'].tables.values()))
        end=int(table.ref.split(':')[1][1:])
        self.assertGreater(book['Обзор']._charts[0].anchor._from.row,end)
        self.assertIn('2024-07-16',[r[0] for r in book['Сверка с офисом'].iter_rows(min_row=7,values_only=True)])
        self.assertTrue(book['Июль 2024'].tables);book.close()
    def test_rows_after_ten_thousand_are_not_overwritten(self):
        book=Workbook();sheet=book.active
        report_catalog.addrow(sheet,10001,['first']);report_catalog.addrow(sheet,10002,['second'])
        self.assertEqual(sheet['A10001'].value,'first');self.assertEqual(sheet['A10002'].value,'second')
    def test_dashboard_api_and_workbook_use_same_ledger(self):
        model=ledger.month_ledger(self.db,'2024-07')
        result=miniapp_server.summarize(self.db,'2024-07')
        self.assertEqual(result['summary'],model['summary']);self.assertEqual(result['periods'],model['periods'])
        self.assertEqual(len(result['ledger']),31)
    def test_home_is_compact_and_all_functions_have_routes(self):
        text,buttons=friendly_ui.home(self.db)
        self.assertLessEqual(sum(map(len,buttons.inline_keyboard)),5)
        self.assertLess(len(text),900)
        for key in ('more','exports','reports','plan','settings','office','edit'):
            self.assertIn(key,friendly_ui.VIEWS)
        text,buttons=friendly_ui.report_menu(self.db,'2024-07')
        self.assertIn('Июль 2024',text)
        self.assertIn('file:month:2024-07',[b.callback_data for row in buttons.inline_keyboard for b in row])

class LedgerRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=core.connect(self.root/'test.sqlite3');core.put_config(self.db,'owner_id','111')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def request(self,action,uid=111):
        q=FakeQuery(action);update=SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=uid),effective_chat=SimpleNamespace(id=uid,type='private'),message=None)
        ctx=SimpleNamespace(application=SimpleNamespace(bot_data={'db':self.db}),user_data={},bot=SimpleNamespace(send_document=AsyncMock(),send_message=AsyncMock()))
        return update,ctx,q
    async def test_table_and_reports_callback(self):
        for action in ('ledger:2024-07:0:all','ledger:2024-07:0:review','reports:2024-07','calc:2024-07'):
            update,ctx,q=self.request(action);await bot.inline_action(update,ctx)
            self.assertEqual(len(q.edits),1);self.assertIn('Июль 2024',q.edits[0][0])
    async def test_unknown_owner_and_malformed_date_cannot_export(self):
        for action,uid in [('file:month:2024-07',999),('file:month:../../bad',111),('ledger:2024-99:0:all',111)]:
            update,ctx,q=self.request(action,uid);await bot.inline_action(update,ctx)
            self.assertEqual(ctx.bot.send_document.await_count,0)
    async def test_month_download_is_selected_month_not_archive(self):
        update,ctx,q=self.request('file:month:2024-07')
        with patch.object(report_catalog,'DATA_DIR',self.root):await bot.inline_action(update,ctx)
        self.assertEqual(ctx.bot.send_document.await_count,1)
        self.assertEqual(ctx.bot.send_document.call_args.kwargs['filename'],'Мои смены 2024-07.xlsx')
        self.assertFalse((self.root/'Мои смены.xlsx').exists())