"""Isolated coverage for recurring plans, safe reversals, annual analysis and PDF."""
import json,os,tempfile,unittest,secrets
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import core,workflows,finance,planning,revisions,analytics,report_pdf,bot,planning_ui
from test_callbacks import FakeQuery

class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'test.sqlite3';self.db=core.connect(self.path)
        self.ref=datetime(2030,1,1,8,tzinfo=timezone.utc)
        self.spec={'start':'2030-01-02','end':'2030-01-09','pattern':'2x2','start_time':'16:00','end_time':'02:00'}
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def create(self):
        p=planning.preview(self.db,self.spec,self.ref)
        return planning.apply(self.db,self.spec,'a'*32,p['digest'],self.ref)
    def latest(self):return self.db.execute('SELECT max(id) FROM events').fetchone()[0]
    def undo(self,eid):
        p=revisions.preview(self.db,eid);return revisions.reverse(self.db,eid,p['digest'])
    def test_preview_never_writes(self):
        before='\n'.join(self.db.iterdump());p=planning.preview(self.db,self.spec,self.ref)
        self.assertEqual(before,'\n'.join(self.db.iterdump()));self.assertEqual((p['shift_count'],p['off_count']),(4,4))
    def test_apply_is_idempotent_and_plan_is_not_actual(self):
        first=self.create();second=self.create();self.assertEqual(first,second)
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],8)
        self.assertEqual(core.totals(self.db,'2030-01')['hours'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM shifts').fetchone()[0],0)
    def test_different_spec_cannot_reuse_confirmation(self):
        self.create();spec={**self.spec,'pattern':'3x3'};p=planning.preview(self.db,spec,self.ref)
        with self.assertRaises(ValueError):planning.apply(self.db,spec,'a'*32,p['digest'],self.ref)
    def test_existing_plans_and_imports_are_preserved(self):
        workflows.set_plan(self.db,'2030-01-02','off',note='Keep this exception')
        self.db.execute('INSERT INTO history_source_rows(source,sheet,excel_row,day,status,start_time,end_time,imported_utc) VALUES(?,?,?,?,?,?,?,?)',('fictional.xlsx','Plan',1,'2030-01-03','План','09:00','18:00',core.iso(core.now())))
        result=self.create();self.assertEqual(result['created'],6)
        self.assertEqual(workflows.plan_day(self.db,'2030-01-02')['status'],'off')
        self.assertEqual(workflows.plan_day(self.db,'2030-01-03')['source'],'import')
    def test_preview_change_rejected_without_partial_apply(self):
        p=planning.preview(self.db,self.spec,self.ref);workflows.set_plan(self.db,'2030-01-04','off')
        with self.assertRaises(ValueError):planning.apply(self.db,self.spec,'a'*32,p['digest'],self.ref)
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_series').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],1)
    def test_weekly_and_leap_year(self):
        spec={**self.spec,'start':'2032-02-01','end':'2032-02-29','pattern':'weekly','weekdays':[0,2,4]}
        p=planning.preview(self.db,spec,self.ref);self.assertEqual(len(p['days']),29)
        self.assertTrue(all(datetime.fromisoformat(r['day']).weekday() in (0,2,4) for r in p['days'] if r['status']=='shift'))
    def test_invalid_bounds_and_types(self):
        for override in ({'start':'2020-01-01'},{'end':'2040-01-01'},{'end':'2029-12-01'},{'start_time':'24:00'},{'end_time':'16:00'},{'pattern':'weekly','weekdays':[True]},{'pattern':'weekly','weekdays':[]}):
            with self.subTest(override=override),self.assertRaises(ValueError):planning.preview(self.db,{**self.spec,**override},self.ref)
    def test_cancel_preserves_manual_changes_and_history(self):
        result=self.create();workflows.set_plan(self.db,'2030-01-04','shift','10:00','19:00')
        p=planning.cancel_preview(self.db,result['id'],self.ref)
        self.assertEqual(len(p['days']),7);self.assertEqual(len(p['kept']),1)
        out=planning.cancel(self.db,result['id'],p['digest'],self.ref);self.assertEqual(out['removed'],7)
        self.assertEqual(workflows.plan_day(self.db,'2030-01-04')['start'],'10:00')
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_series_days').fetchone()[0],8)
        self.assertTrue(planning.cancel(self.db,result['id'],p['digest'],self.ref)['already_cancelled'])
    def test_cancel_preserves_started_and_actual_days(self):
        result=self.create();p=planning.cancel_preview(self.db,result['id'],datetime(2030,1,6,18,tzinfo=timezone.utc))
        self.assertTrue(all(d>'2030-01-06' for d in p['days']))
    def test_cancel_rechecks_latest_edit(self):
        result=self.create();p=planning.cancel_preview(self.db,result['id'],self.ref)
        workflows.set_plan(self.db,'2030-01-02','off')
        with self.assertRaises(ValueError):planning.cancel(self.db,result['id'],p['digest'],self.ref)
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],8)
    def test_atomic_rollback_on_audit_failure(self):
        p=planning.preview(self.db,self.spec,self.ref)
        with patch('planning.log',side_effect=ValueError('audit failure')),self.assertRaises(ValueError):planning.apply(self.db,self.spec,'a'*32,p['digest'],self.ref)
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_series_days').fetchone()[0],0)
    def test_parallel_replay_creates_one_series(self):
        p=planning.preview(self.db,self.spec,self.ref);barrier=Barrier(2)
        def run(_):
            db=core.connect(self.path)
            try:barrier.wait(timeout=5);return planning.apply(db,self.spec,'a'*32,p['digest'],self.ref)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],8)
    def test_false_arrival_preserves_original_in_reversal_archive(self):
        _,shift=core.toggle(self.db,1,core.now()-timedelta(minutes=2));eid=self.latest();snapshot=dict(shift)
        self.undo(eid);self.assertIsNone(core.open_shift(self.db))
        original=json.loads(self.db.execute('SELECT original_json FROM operation_reversals WHERE event_id=?',(eid,)).fetchone()[0])
        self.assertEqual(original['record'],snapshot)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone())
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM processed_updates WHERE update_id=1').fetchone())
        self.assertTrue(revisions.reverse(self.db,eid,'stale')['already_reversed'])
    def test_false_departure_reopens_same_shift(self):
        core.toggle(self.db,1,core.now()-timedelta(hours=2));_,s=core.toggle(self.db,2,core.now()-timedelta(minutes=5))
        self.undo(self.latest());self.assertEqual(core.open_shift(self.db)['id'],s['id'])
    def test_departure_cannot_reopen_when_new_shift_open(self):
        core.toggle(self.db,1,core.now()-timedelta(hours=2));core.toggle(self.db,2,core.now()-timedelta(minutes=10));eid=self.latest()
        core.toggle(self.db,3,core.now()-timedelta(minutes=2))
        with self.assertRaises(ValueError):revisions.preview(self.db,eid)
    def test_shift_with_later_break_is_not_deleted(self):
        core.toggle(self.db,1,core.now()-timedelta(hours=2));eid=self.latest();workflows.start_break(self.db)
        with self.assertRaises(ValueError):revisions.preview(self.db,eid)
    def test_correction_restores_exact_previous_record(self):
        sid=core.corrected_shift(self.db,'2024-07-01','09:00','18:00',30);before=dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone())
        core.corrected_shift(self.db,'2024-07-01','09:05','18:15',25,sid);self.undo(self.latest())
        self.assertEqual(before,dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone()))
    def test_money_reversal_keeps_evidence_and_separates_accruals(self):
        finance.add_payment(self.db,'2024-07',1,200,'2024-07-25','Fictional');eid=self.latest();self.undo(eid)
        self.assertEqual(finance.payment_totals(self.db,'2024-07')[1],0)
        workflows.add_adjustment(self.db,'2024-07-12','bonus',30);self.undo(self.latest())
        self.assertEqual(workflows.adjustment_totals(self.db,'2024-07')['bonus'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM operation_reversals').fetchone()[0],2)
    def test_stale_undo_confirmation_does_not_touch_newer_record(self):
        sid=core.corrected_shift(self.db,'2024-07-01','09:00','18:00',30);eid=self.latest();p=revisions.preview(self.db,eid)
        core.corrected_shift(self.db,'2024-07-01','09:01','18:00',30,sid)
        with self.assertRaises(ValueError):revisions.reverse(self.db,eid,p['digest'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM shifts').fetchone()[0],1)
    def test_reversal_rolls_back_evidence_and_change_together(self):
        core.toggle(self.db,1,core.now()-timedelta(minutes=1));eid=self.latest();p=revisions.preview(self.db,eid)
        with patch('revisions.log',side_effect=ValueError('audit')),self.assertRaises(ValueError):revisions.reverse(self.db,eid,p['digest'])
        self.assertIsNotNone(core.open_shift(self.db));self.assertEqual(self.db.execute('SELECT count(*) FROM operation_reversals').fetchone()[0],0)
    def test_annual_totals_read_only(self):
        core.corrected_shift(self.db,'2024-07-01','09:00','18:00',30);finance.add_payment(self.db,'2024-03',1,500,'2024-04-01')
        before='\n'.join(self.db.iterdump());year=analytics.year_analytics(self.db,2024)
        self.assertEqual(year['hours'],8.5);self.assertEqual(year['earned'],2550);self.assertEqual(year['received'],500)
        self.assertEqual(len(year['months']),12);self.assertEqual(year['months'][2]['received'],500)
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_pdf_is_read_only_has_cyrillic_and_no_active_content(self):
        from pypdf import PdfReader
        core.corrected_shift(self.db,'2024-07-01','16:00','02:00',30)
        before='\n'.join(self.db.iterdump())
        for func,period in ((report_pdf.monthly_pdf,'2024-07'),(report_pdf.annual_pdf,2024)):
            path=func(self.db,period,self.root/(str(period)+'.pdf'));self.assertTrue(path.read_bytes().startswith(b'%PDF-'))
            reader=PdfReader(path);text='\n'.join(p.extract_text() for p in reader.pages)
            self.assertIn('Мои смены',text);self.assertIn('2 850,00',text);self.assertNotIn('/OpenAction',reader.trailer['/Root'])
            self.assertLessEqual(len(reader.pages),4)
            if os.name!='nt':self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_parse_day_always_returns_iso_string(self):
        from extra_wizard import parse_day
        for value in ('сегодня','12.07','2024-07-12','12.07.2024'):
            parsed=parse_day(value);self.assertIsInstance(parsed,str);self.assertEqual(len(parsed),10)

class CompletionRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'test.sqlite3');core.put_config(self.db,'owner_id','111')
        self.ctx=SimpleNamespace(application=SimpleNamespace(bot_data={'db':self.db}),user_data={},bot=SimpleNamespace(send_message=AsyncMock(),send_document=AsyncMock()))
    def tearDown(self):self.db.close();self.tmp.cleanup()
    async def click(self,data,uid=111):
        q=FakeQuery(data);u=SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=uid),effective_chat=SimpleNamespace(id=uid,type='private'),message=None)
        await bot.inline_action(u,self.ctx);return q
    async def test_complete_recurrence_flow_and_stale_button(self):
        await self.click('series:new');token=self.ctx.user_data['series_flow']['token']
        for cmd in ('pattern:2x2','start:tomorrow','span:14','clock:16:00:02:00'):await self.click('series:'+cmd+':'+token)
        self.assertEqual(self.ctx.user_data['series_flow']['step'],'confirm')
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],0)
        await self.click('series:save:wrong');self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],0)
        await self.click('series:save:'+token);self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],14)
        await self.click('series:save:'+token);self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],14)
    async def test_unknown_user_cannot_plan_or_reverse(self):
        for action in ('series:new','audit:0','undo:view:1','year:2024','file:pdf:2024-07'):
            q=await self.click(action,999);self.assertTrue(q.alerts[0][1]['show_alert'])
        self.assertEqual(self.ctx.bot.send_message.await_count,0);self.assertEqual(self.ctx.bot.send_document.await_count,0)
    async def test_undo_confirmation_bound_to_preview(self):
        core.toggle(self.db,1,core.now()-timedelta(minutes=1));eid=self.db.execute('SELECT max(id) FROM events').fetchone()[0]
        await self.click('undo:view:'+str(eid));token=self.ctx.user_data['undo_flow']['token']
        await self.click('undo:confirm:wrong');self.assertIsNotNone(core.open_shift(self.db))
        await self.click('undo:confirm:'+token);self.assertIsNone(core.open_shift(self.db))
    async def test_year_and_history_routes(self):
        q=await self.click('year:2024');self.assertIn('Годовой обзор',q.edits[0][0])
        await self.click('audit:0');self.assertIn('История',self.ctx.bot.send_message.call_args.kwargs['text'])
class ConfirmationGuardTests(unittest.TestCase):
    def test_confirmation_is_bound_to_values_and_expires(self):
        import confirmation
        flow={'step':'confirm','amount':'12.00','date':'2024-07-01'}
        token=confirmation.issue(flow);self.assertTrue(confirmation.valid(flow,token))
        flow['amount']='20.00';self.assertFalse(confirmation.valid(flow,token))
        new=confirmation.issue(flow);self.assertNotEqual(token,new);self.assertTrue(confirmation.valid(flow,new))
        flow['_confirmation_at']-=601;self.assertFalse(confirmation.valid(flow,new))
    def test_confirmation_from_other_flow_never_matches(self):
        import confirmation
        a={'step':'confirm','amount':10};b=dict(a)
        token=confirmation.issue(a);confirmation.issue(b);self.assertFalse(confirmation.valid(b,token))
    def test_too_small_money_is_not_stored_as_zero(self):
        for value in ('0.001','0.004','nan','inf'):
            with self.assertRaises(ValueError):finance._amount(value)
        self.assertEqual(str(finance._amount('0.01')),'0.01')

class AnalyticsHttpTests(unittest.TestCase):
    import test_miniapp as _tests
    setUp=_tests.MiniAppTests.setUp
    tearDown=_tests.MiniAppTests.tearDown
    request=_tests.MiniAppTests.request
    def test_annual_api_requires_owner_signature(self):
        from test_miniapp import signed
        self.assertEqual(self.request('/mini/api/analytics',{'year':2024})[0],403)
        status,data,_=self.request('/mini/api/analytics',{'year':2024},signed())
        self.assertEqual(status,200);self.assertEqual(len(data['months']),12)
    def test_annual_api_strict_year_validation(self):
        from test_miniapp import signed
        for year in (True,'2024',None,1999,2101):
            self.assertEqual(self.request('/mini/api/analytics',{'year':year},signed())[0],400)
    def test_annual_api_leaves_database_unchanged(self):
        from test_miniapp import signed
        db=core.connect(self.dbpath);before='\n'.join(db.iterdump());db.close()
        self.assertEqual(self.request('/mini/api/analytics',{'year':2024},signed())[0],200)
        db=core.connect(self.dbpath);self.assertEqual(before,'\n'.join(db.iterdump()));db.close()