"""Fictional-data regressions for a read-only agenda and owner-only navigation."""
import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import core,agenda,miniapp_server,quick_routes,bot

class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'fixture.sqlite3')
        self.at=datetime(2030,12,31,12,tzinfo=core.TZ)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def plan(self,day,status='shift',start='16:00',end='02:00'):
        self.db.execute('INSERT INTO plan_days VALUES(?,?,?,?,?,?,?)',(day,status,start if status=='shift' else None,end if status=='shift' else None,'fictional',core.iso(core.now()),core.iso(core.now())))
    def test_seven_dates_cross_year_and_unknown_is_not_off(self):
        data=agenda.daily_agenda(self.db,self.at)
        self.assertEqual(len(data['days']),7);self.assertEqual(data['days'][-1]['day'],'2031-01-06')
        self.assertEqual(data['counts'],{'work':0,'off':0,'unspecified':7})
        self.assertTrue(all(r['status']=='unspecified' for r in data['days']))
    def test_next_shift_crosses_month_without_changing_data(self):
        self.plan('2031-01-02');before='\n'.join(self.db.iterdump());data=agenda.daily_agenda(self.db,self.at)
        self.assertEqual(data['next_shift']['day'],'2031-01-02');self.assertTrue(data['next_shift']['next_day'])
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_current_plan_after_midnight_refers_to_previous_day(self):
        self.plan('2030-12-31');data=agenda.daily_agenda(self.db,datetime(2031,1,1,1,tzinfo=core.TZ))
        self.assertEqual(data['current_plan']['day'],'2030-12-31');self.assertIsNone(data['next_shift'])
    def test_ended_plan_is_not_upcoming(self):
        self.plan('2030-12-31');data=agenda.daily_agenda(self.db,datetime(2031,1,1,2,tzinfo=core.TZ))
        self.assertIsNone(data['current_plan']);self.assertIsNone(data['next_shift'])
    def test_future_and_current_are_separate(self):
        self.plan('2030-12-31');self.plan('2031-01-03')
        data=agenda.daily_agenda(self.db,datetime(2030,12,31,17,tzinfo=core.TZ))
        self.assertEqual(data['current_plan']['day'],'2030-12-31');self.assertEqual(data['next_shift']['day'],'2031-01-03')
    def test_actual_day_not_offered_as_upcoming(self):
        self.plan('2030-12-31');core.toggle(self.db,1,self.at)
        data=agenda.daily_agenda(self.db,self.at);self.assertIsNone(data['next_shift']);self.assertEqual(data['days'][0]['status'],'open')
    def test_closed_day_is_not_next_shift(self):
        self.plan('2030-12-31');core.toggle(self.db,1,self.at-timedelta(hours=2));core.toggle(self.db,2,self.at-timedelta(minutes=1))
        data=agenda.daily_agenda(self.db,self.at);self.assertIsNone(data['next_shift']);self.assertEqual(data['days'][0]['status'],'worked')
    def test_off_day_counted_without_creating_time(self):
        self.plan('2030-12-31','off');self.plan('2031-01-01')
        data=agenda.daily_agenda(self.db,self.at);self.assertEqual(data['counts'],{'work':1,'off':1,'unspecified':5});self.assertEqual(core.totals(self.db)['earnings'],0)
    def test_timezone_is_moscow_not_utc_calendar_date(self):
        data=agenda.daily_agenda(self.db,datetime(2030,12,31,22,tzinfo=timezone.utc));self.assertEqual(data['today'],'2031-01-01')
    def test_naive_reference_rejected(self):
        with self.assertRaises(ValueError):agenda.daily_agenda(self.db,datetime(2030,1,1))
    def test_horizon_is_bounded(self):
        self.plan('2032-01-01');data=agenda.daily_agenda(self.db,self.at);self.assertIsNone(data['next_shift']);self.assertEqual(data['search_days'],90)
    def test_imported_plan_used_and_manual_exception_wins(self):
        self.db.execute('INSERT INTO history_source_rows(source,sheet,excel_row,day,status,start_time,end_time,imported_utc) VALUES(?,?,?,?,?,?,?,?)',('fixture.xlsx','Plan',1,'2031-01-01','План','09:00','18:00',core.iso(core.now())))
        self.assertEqual(agenda.daily_agenda(self.db,self.at)['next_shift']['start'],'09:00')
        self.plan('2031-01-01','off');self.assertIsNone(agenda.daily_agenda(self.db,self.at)['next_shift'])
    def test_invalid_import_clock_not_next_shift(self):
        self.plan('2031-01-01',start='bad');self.assertIsNone(agenda.daily_agenda(self.db,self.at)['next_shift'])
    def test_selected_month_does_not_move_agenda(self):
        a=miniapp_server.summarize(self.db,'2024-07');b=miniapp_server.summarize(self.db,'2030-12')
        self.assertEqual(a['agenda']['today'],b['agenda']['today']);self.assertEqual(a['agenda']['days'],b['agenda']['days'])

class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'fixture.sqlite3');core.put_config(self.db,'owner_id','111')
        self.ctx=SimpleNamespace(args=[],user_data={},application=SimpleNamespace(bot_data={'db':self.db}),bot=SimpleNamespace(send_message=AsyncMock(),send_document=AsyncMock()))
        self.update=SimpleNamespace(effective_user=SimpleNamespace(id=111),effective_chat=SimpleNamespace(id=111,type='private'),message=SimpleNamespace(reply_text=AsyncMock()),callback_query=None)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    async def route(self,payload):self.ctx.args=[payload];await bot.started(self.update,self.ctx)
    async def test_report_opens_requested_month_without_file_or_write(self):
        before='\n'.join(self.db.iterdump());await self.route('q_reports_202407')
        text=self.update.message.reply_text.call_args.args[0];self.assertIn('Июль 2024',text)
        self.assertEqual(self.ctx.bot.send_document.await_count,0);self.assertEqual(before,'\n'.join(self.db.iterdump()))
    async def test_payment_prefills_month_only_not_amount(self):
        before='\n'.join(self.db.iterdump());await self.route('q_payment_202407')
        self.assertEqual(self.ctx.user_data['finance_flow'],{'kind':'payment','step':'period','month':'2024-07'})
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    async def test_day_route_preserves_exact_date(self):
        await self.route('q_day_20240712');self.assertIn('12.07.2024',self.update.message.reply_text.call_args.args[0])
    async def test_other_owner_cannot_open_financial_route(self):
        self.update.effective_user.id=222;await self.route('q_payment_202407')
        self.assertNotIn('finance_flow',self.ctx.user_data);self.assertEqual(self.ctx.bot.send_message.await_count,0)
    async def test_group_chat_cannot_open_route(self):
        self.update.effective_chat.type='group';await self.route('q_reports_202407');self.assertEqual(self.update.message.reply_text.await_count,0)
    async def test_direct_handler_checks_owner(self):
        self.update.effective_user.id=222;await quick_routes.open_destination(self.update,self.ctx,'q_payment_202407');self.assertNotIn('finance_flow',self.ctx.user_data)
    async def test_invalid_link_cannot_reset_existing_input(self):
        self.ctx.user_data['finance_flow']={'step':'amount'};await self.route('q_reports_202499');self.assertEqual(self.ctx.user_data['finance_flow'],{'step':'amount'})
        self.assertIn('Ссылка',self.update.message.reply_text.call_args.args[0])
    async def test_valid_navigation_discards_other_unconfirmed_flows(self):
        self.ctx.user_data.update(extra_flow={'step':'confirm'},correction={'step':'date'},await_office_document=True)
        await self.route('q_plan');self.assertNotIn('extra_flow',self.ctx.user_data);self.assertNotIn('correction',self.ctx.user_data);self.assertNotIn('await_office_document',self.ctx.user_data)
    async def test_malformed_and_unsupported_destinations(self):
        for text in ('q_reports_199912','q_reports_210101','q_reports_20240229','q_day_20240230','q_day_202402','q_out','q_payment_../../','q_reports_202407_extra',None,'x'*65):
            with self.subTest(text=text),self.assertRaises(ValueError):quick_routes.parse_destination(text)
    async def test_supported_links_bounded_and_no_automatic_write(self):
        for text in ('q_home','q_edit','q_plan','q_reports_202407','q_payment_202407','q_day_20240229'):
            self.assertLessEqual(len(text),64);quick_routes.parse_destination(text)
