"""Fictional-only atomic bulk planning, explicit unset and hostile input regressions."""
import json, tempfile, unittest, os, hashlib, hmac, time
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from urllib.parse import urlencode
import core, workflows, planning, calendar_plan, batch_plan, extra_wizard, bot, confirmation, miniapp_server, calendar_views
from test_callbacks import FakeQuery
import test_miniapp
from test_miniapp import signed

class BatchPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite3';self.db=core.connect(self.path)
        self.month='2099-07';self.days=['2099-07-10','2099-07-12','2099-07-31']
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def flow(self,status='shift'):
        return batch_plan.flow(self.db,self.month,self.days,status,'16:00' if status=='shift' else None,'02:00' if status=='shift' else None)
    def payload(self,kind='s',mask=None,month='209907',clock=('1600','0200')):
        mask=mask if mask is not None else sum(1<<(int(d[-2:])-1) for d in self.days)
        return f"b_{month}_{mask:08x}_{kind}_{clock[0]}_{clock[1]}_{batch_plan.revision(self.db,self.month)}"
    def dump(self):return '\n'.join(self.db.iterdump())
    def imported(self,day):
        self.db.execute('INSERT INTO history_source_rows(source,sheet,excel_row,day,status,start_time,end_time,imported_utc) VALUES(?,?,?,?,?,?,?,?)',('fictional.xlsx','Plan',int(day[-2:]),day,'План','09:00','18:00',core.iso(core.now())))
    def test_preview_has_no_database_side_effects(self):
        before=self.dump();flow=self.flow();self.assertEqual(before,self.dump());self.assertEqual(len(flow['before']),3)
    def test_apply_changes_only_selected_dates(self):
        batch_plan.apply(self.db,self.flow());self.assertEqual(len(self.db.execute('SELECT * FROM plan_days').fetchall()),3)
        self.assertEqual(workflows.plan_day(self.db,self.days[-1])['end'],'02:00');self.assertIsNone(workflows.plan_day(self.db,'2099-07-11'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM shifts').fetchone()[0],0)
    def test_conflict_rejects_entire_batch(self):
        selected=self.flow();workflows.set_plan(self.db,self.days[1],'off');before=self.dump()
        with self.assertRaises(ValueError):batch_plan.apply(self.db,selected)
        self.assertEqual(before,self.dump())
    def test_audit_failure_rolls_back_every_day(self):
        selected=self.flow();before=self.dump();original=workflows.log;calls=[]
        def fail(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise ValueError('fictional audit failure')
            return original(*args,**kwargs)
        with patch('workflows.log',side_effect=fail),self.assertRaises(ValueError):batch_plan.apply(self.db,selected)
        self.assertEqual(before,self.dump())
    def test_replay_never_creates_second_event(self):
        selected=self.flow();batch_plan.apply(self.db,selected);before=self.dump()
        with self.assertRaises(ValueError):batch_plan.apply(self.db,selected)
        self.assertEqual(before,self.dump())
    def test_parallel_confirmations_only_one_applies(self):
        selected=self.flow();barrier=Barrier(2)
        def run(_):
            db=core.connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:batch_plan.apply(db,selected);return 'saved'
                except ValueError:return 'stale'
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(sorted(results),['saved','stale']);self.assertEqual(self.db.execute("SELECT count(*) FROM events WHERE kind='plan_batch_change'").fetchone()[0],1)
    def test_deep_link_fits_and_includes_day_31(self):
        payload=self.payload();self.assertLessEqual(len(payload),64);self.assertEqual(batch_plan.parse(payload)[1],self.days)
    def test_all_31_dates_fit(self):
        self.assertEqual(len(batch_plan.parse(self.payload(mask=(1<<31)-1))[1]),31)
    def test_empty_or_overflow_masks_rejected(self):
        for mask in (0,1<<31,0xffffffff):
            with self.subTest(mask=mask),self.assertRaises(ValueError):batch_plan.parse(self.payload(mask=mask))
    def test_leap_year_bounds(self):
        self.assertEqual(batch_plan.parse(self.payload(month='203202',mask=1<<28))[1],['2032-02-29'])
        for month in ('203102','203204'):
            with self.assertRaises(ValueError):batch_plan.parse(self.payload(month=month,mask=1<<30))
    def test_unset_link_has_no_clock(self):
        self.assertEqual(batch_plan.parse(self.payload('n',clock=('0000','0000')))[2:5],('unset',None,None))
        with self.assertRaises(ValueError):batch_plan.parse(self.payload('n'))
    def test_invalid_dates_types_and_clocks(self):
        for days in ([],self.days*2,['2099-08-01'],['2099-07-32'],[True]):
            with self.subTest(days=days),self.assertRaises(ValueError):batch_plan.flow(self.db,self.month,days,'off')
        for payload in (None,True,'x'*65,self.payload().replace('_s_','_x_'),self.payload(clock=('2500','0200')),self.payload(clock=('0200','0200'))):
            with self.assertRaises(ValueError):batch_plan.parse(payload)
    def test_past_date_rejected(self):
        with self.assertRaises(ValueError):batch_plan.flow(self.db,'2024-07',['2024-07-01'],'off')
    def test_unset_does_not_restore_import(self):
        day=self.days[0];self.imported(day);sources=[tuple(r) for r in self.db.execute('SELECT * FROM history_source_rows')]
        workflows.clear_plan(self.db,day);self.assertIsNone(workflows.plan_day(self.db,day));self.assertTrue(workflows.is_plan_cleared(self.db,day))
        self.assertEqual(sources,[tuple(r) for r in self.db.execute('SELECT * FROM history_source_rows')])
    def test_set_after_unset_restores_explicit_plan(self):
        day=self.days[0];workflows.clear_plan(self.db,day);workflows.set_plan(self.db,day,'off')
        self.assertFalse(workflows.is_plan_cleared(self.db,day));self.assertEqual(workflows.plan_day(self.db,day)['status'],'off')
    def test_clear_does_not_change_factual_or_financial_rows(self):
        core.corrected_shift(self.db,'2024-07-01','09:00','18:00',30)
        before={n:[tuple(r) for r in self.db.execute('SELECT * FROM '+n)] for n in ('shifts','rate_history','received_payments','office_days','pay_adjustments')}
        batch_plan.apply(self.db,self.flow('unset'))
        for n,v in before.items():self.assertEqual(v,[tuple(r) for r in self.db.execute('SELECT * FROM '+n)])
    def test_removed_dates_reserved_against_series(self):
        workflows.clear_plan(self.db,self.days[0]);spec={'start':self.days[0],'end':self.days[1],'pattern':'2x2','start_time':'16:00','end_time':'02:00'}
        data=planning.preview(self.db,spec);self.assertIn(self.days[0],[r['day'] for r in data['skipped']])
    def test_unset_exception_survives_series_cancel(self):
        day=self.days[0];spec={'start':day,'end':day,'pattern':'2x2','start_time':'16:00','end_time':'02:00'}
        p=planning.preview(self.db,spec);s=planning.apply(self.db,spec,'e'*32,p['digest']);workflows.clear_plan(self.db,day)
        p=planning.cancel_preview(self.db,s['id']);planning.cancel(self.db,s['id'],p['digest'])
        self.assertTrue(workflows.is_plan_cleared(self.db,day));self.assertIsNone(workflows.plan_day(self.db,day))
    def test_single_unset_roundtrip(self):
        day=self.days[0];payload='p_'+day.replace('-','')+'_n_0000_0000_'+calendar_plan.revision(self.db,day)
        args=calendar_plan.parse(payload);selected=calendar_plan.flow(self.db,*args[:4],expected=args[4]);calendar_plan.apply(self.db,selected)
        self.assertTrue(workflows.is_plan_cleared(self.db,day))
    def test_revision_changes_on_clear_and_edit_back(self):
        before=batch_plan.revision(self.db,self.month);workflows.clear_plan(self.db,self.days[0]);self.assertNotEqual(before,batch_plan.revision(self.db,self.month))
        workflows.set_plan(self.db,self.days[0],'off');selected=self.flow();workflows.clear_plan(self.db,self.days[0]);workflows.set_plan(self.db,self.days[0],'off')
        with self.assertRaises(ValueError):batch_plan.apply(self.db,selected)
    def test_preview_message_under_telegram_limit(self):
        days=[f'2099-07-{i:02d}' for i in range(1,32)];selected=batch_plan.flow(self.db,self.month,days,'unset')
        self.assertLess(len(batch_plan.prompt(selected)),4096);self.assertIn('31.07',batch_plan.prompt(selected))
    def test_read_api_no_mutations_and_supports_features(self):
        before=self.dump();state=miniapp_server.summarize(self.db,self.month);self.assertEqual(before,self.dump())
        self.assertTrue(state['plan_editor']['supports_batch']);self.assertTrue(state['plan_editor']['supports_unset']);self.assertEqual(state['plan_editor']['batch_revision'],batch_plan.revision(self.db,self.month))
    def test_calendar_does_not_show_cleared_import(self):
        day=self.days[0];self.imported(day);workflows.clear_plan(self.db,day)
        _,kb=calendar_views.month_view(self.db,self.month);button=next(b for row in kb.inline_keyboard for b in row if b.callback_data=='day:'+day)
        self.assertEqual(button.text,'10')
    def test_next_plans_not_hidden_by_many_cleared_dates(self):
        for i in range(1,25):
            day=f'2099-07-{i:02d}';self.imported(day);workflows.clear_plan(self.db,day)
        workflows.set_plan(self.db,'2099-07-31','shift','09:00','18:00')
        self.assertEqual(workflows.upcoming_plan(self.db,'2099-07-01',limit=1)[0]['day'],'2099-07-31')
    def test_plan_export_accepts_cleared_import(self):
        import report_catalog
        self.imported(self.days[0]);workflows.clear_plan(self.db,self.days[0])
        with patch.object(report_catalog,'DATA_DIR',Path(self.tmp.name)):
            self.assertTrue(report_catalog.plan(self.db).is_file())

class BatchChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'test.sqlite3');core.put_config(self.db,'owner_id','111')
        self.ctx=SimpleNamespace(args=['b_209907_00000003_o_0000_0000_'+batch_plan.revision(self.db,'2099-07')],user_data={},application=SimpleNamespace(bot_data={'db':self.db}),bot=SimpleNamespace(send_message=AsyncMock()))
        self.update=SimpleNamespace(effective_user=SimpleNamespace(id=111),effective_chat=SimpleNamespace(id=111,type='private'),message=SimpleNamespace(reply_text=AsyncMock()),callback_query=None)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    async def click(self,data):
        self.update.callback_query=FakeQuery(data);await bot.inline_action(self.update,self.ctx)
    async def test_confirmed_batch_and_replay(self):
        await bot.started(self.update,self.ctx);self.assertIsNone(workflows.plan_day(self.db,'2099-07-01'))
        token=self.ctx.user_data['extra_flow']['_confirmation_token'];await self.click('extra:save:'+token)
        self.assertEqual(workflows.plan_day(self.db,'2099-07-01')['status'],'off');before='\n'.join(self.db.iterdump())
        await self.click('extra:save:'+token);self.assertEqual(before,'\n'.join(self.db.iterdump()))
    async def test_cancel_preserves_database(self):
        await bot.started(self.update,self.ctx);before='\n'.join(self.db.iterdump());await self.click('extra:cancel');self.assertEqual(before,'\n'.join(self.db.iterdump()))
    async def test_foreign_owner_and_group_denied(self):
        self.update.effective_user.id=999;await bot.started(self.update,self.ctx);self.assertNotIn('extra_flow',self.ctx.user_data)
        self.update.effective_user.id=111;self.update.effective_chat.type='group';await bot.started(self.update,self.ctx);self.assertNotIn('extra_flow',self.ctx.user_data)
    async def test_forged_or_expired_confirmation_denied(self):
        await bot.started(self.update,self.ctx);selected=self.ctx.user_data['extra_flow'];token=selected['_confirmation_token']
        await self.click('extra:save:wrong');self.assertIsNone(workflows.plan_day(self.db,'2099-07-01'))
        selected['_confirmation_at']-=601;await self.click('extra:save:'+token);self.assertIsNone(workflows.plan_day(self.db,'2099-07-01'))
    async def test_changed_values_cannot_use_old_button(self):
        await bot.started(self.update,self.ctx);selected=self.ctx.user_data['extra_flow'];token=selected['_confirmation_token'];selected['days']=['2099-07-03']
        await self.click('extra:save:'+token);self.assertIsNone(workflows.plan_day(self.db,'2099-07-03'))
    async def test_conflict_after_preview_does_not_partially_save(self):
        await bot.started(self.update,self.ctx);token=self.ctx.user_data['extra_flow']['_confirmation_token'];workflows.set_plan(self.db,'2099-07-02','shift','09:00','18:00')
        before='\n'.join(self.db.iterdump());await self.click('extra:save:'+token);self.assertEqual(before,'\n'.join(self.db.iterdump()));self.assertNotIn('extra_flow',self.ctx.user_data)

class StrictHttpTests(unittest.TestCase):
    setUp=test_miniapp.MiniAppTests.setUp;tearDown=test_miniapp.MiniAppTests.tearDown
    def raw(self,body,extra=()):
        conn=HTTPConnection('127.0.0.1',self.port,timeout=5);conn.putrequest('POST','/mini/api/state')
        for k,v in [('Content-Length',str(len(body))),('Content-Type','application/json'),('X-Telegram-Init-Data',signed()),*extra]:conn.putheader(k,v)
        conn.endheaders(body);r=conn.getresponse();code=r.status;r.read();conn.close();return code
    def test_duplicate_json_keys_rejected(self):self.assertEqual(self.raw(b'{"month":"2024-07","month":"2024-08"}'),400)
    def test_nonfinite_json_rejected(self):
        for value in (b'NaN',b'Infinity',b'-Infinity',b'1e999'):self.assertEqual(self.raw(b'{"month":'+value+b'}'),400)
    def test_ambiguous_security_headers_rejected(self):
        for h in [('X-Telegram-Init-Data',signed()),('Content-Type','application/json')]:self.assertEqual(self.raw(b'{}',[h]),400)
        self.assertEqual(self.raw(b'{}',[('Origin','https://example.invalid'),('Origin','https://example.invalid')]),400)
    def test_unknown_fields_rejected(self):self.assertEqual(self.raw(b'{"month":"2024-07","write":true}'),400)
    def test_nested_json_failure_does_not_crash_server(self):
        self.assertEqual(self.raw(b'{"month":'+b'['*1500+b'0'+b']'*1500+b'}'),400);self.assertEqual(self.raw(b'{"month":"2024-07"}'),200)
    def test_signed_ambiguous_user_rejected(self):
        parts={'auth_date':str(int(time.time())),'user':'{"id":999,"id":77}'};secret=hmac.new(b'WebAppData',b'fictional-token',hashlib.sha256).digest()
        parts['hash']=hmac.new(secret,'\n'.join(k+'='+v for k,v in sorted(parts.items())).encode(),hashlib.sha256).hexdigest()
        self.assertFalse(miniapp_server.validate_init_data(urlencode(parts),'fictional-token','77'))
