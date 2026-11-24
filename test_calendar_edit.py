"""Calendar-plan regressions. Fictional data only; no Telegram network calls."""
import os,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import core,workflows,planning,calendar_plan,calendar_views,extra_wizard,confirmation,bot,miniapp_server
from test_callbacks import FakeQuery

class CalendarPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'fixture.sqlite3')
        self.day=(datetime.now(core.TZ).date()+timedelta(days=2)).isoformat()
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def draft(self,status='shift',start='16:00',end='02:00'):
        return calendar_plan.flow(self.db,self.day,status,None if status=='off' else start,None if status=='off' else end)
    def payload(self,status='s',start='1600',end='0200'):
        return 'p_'+self.day.replace('-','')+'_'+status+'_'+start+'_'+end+'_'+calendar_plan.revision(self.db,self.day)
    def test_draft_is_read_only(self):
        before='\n'.join(self.db.iterdump());item=self.draft();self.assertEqual(item['step'],'confirm');self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_payload_bounded_and_overnight(self):
        text=self.payload();self.assertLessEqual(len(text),64)
        d,s,start,end,rev=calendar_plan.parse(text)
        self.assertEqual((d,s,start,end),(self.day,'shift','16:00','02:00'));self.assertEqual(len(rev),32)
    def test_off_payload_has_no_clock(self):
        self.assertEqual(calendar_plan.parse(self.payload('o','0000','0000'))[1:4],('off',None,None))
        with self.assertRaises(ValueError):calendar_plan.parse(self.payload('o','0900','1800'))
    def test_invalid_link_and_clocks_rejected(self):
        for raw in ('p_../../bad',self.payload()+'x',self.payload(start='2500'),self.payload(start='0200'),self.payload().replace('_s_','_x_')):
            with self.subTest(raw=raw),self.assertRaises(ValueError):calendar_plan.parse(raw)
    def test_past_plan_can_be_prepared_without_writing(self):
        before='\n'.join(self.db.iterdump())
        draft=calendar_plan.flow(self.db,'2024-07-01','off')
        self.assertEqual(draft['day'],'2024-07-01')
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_change_work_to_off_keeps_facts_and_money(self):
        today=datetime.now(core.TZ).date().isoformat();core.toggle(self.db,91,core.now()-timedelta(minutes=20))
        before=[tuple(r) for r in self.db.execute('SELECT * FROM shifts')]
        item=calendar_plan.flow(self.db,today,'shift','16:00','02:00');calendar_plan.apply(self.db,item)
        item=calendar_plan.flow(self.db,today,'off');calendar_plan.apply(self.db,item)
        self.assertEqual(before,[tuple(r) for r in self.db.execute('SELECT * FROM shifts')])
        self.assertEqual(workflows.plan_day(self.db,today)['status'],'off')
        self.assertEqual(core.totals(self.db)['earnings'],0)
    def test_stale_preview_does_not_overwrite(self):
        selected=self.draft();workflows.set_plan(self.db,self.day,'off')
        before='\n'.join(self.db.iterdump())
        with self.assertRaises(ValueError):calendar_plan.apply(self.db,selected)
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_stale_link_fails_before_confirmation(self):
        args=calendar_plan.parse(self.payload());workflows.set_plan(self.db,self.day,'off')
        with self.assertRaises(ValueError):calendar_plan.flow(self.db,*args[:4],expected=args[4])
    def test_edit_back_to_same_value_invalidates_old_confirmation(self):
        workflows.set_plan(self.db,self.day,'off');selected=self.draft()
        workflows.set_plan(self.db,self.day,'shift','09:00','18:00');workflows.set_plan(self.db,self.day,'off')
        with self.assertRaises(ValueError):calendar_plan.apply(self.db,selected)
    def test_audit_failure_rolls_back_plan(self):
        selected=self.draft();before='\n'.join(self.db.iterdump())
        with patch('workflows.log',side_effect=ValueError('audit failed')),self.assertRaises(ValueError):calendar_plan.apply(self.db,selected)
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_day_override_survives_series_cancellation(self):
        spec={'start':self.day,'end':self.day,'pattern':'2x2','start_time':'16:00','end_time':'02:00'}
        preview=planning.preview(self.db,spec);created=planning.apply(self.db,spec,'c'*32,preview['digest'])
        calendar_plan.apply(self.db,self.draft('off'))
        preview=planning.cancel_preview(self.db,created['id']);self.assertEqual(preview['days'],[])
        planning.cancel(self.db,created['id'],preview['digest']);self.assertEqual(workflows.plan_day(self.db,self.day)['status'],'off')
    def test_state_exposes_only_confirmation_capability(self):
        before='\n'.join(self.db.iterdump())
        with patch.dict(os.environ,{'MINIAPP_BOT_USERNAME':'FictionalShiftBot','MINIAPP_ENABLE_WRITES':'0'}):
            state=miniapp_server.summarize(self.db,self.day[:7])
        self.assertFalse(state['capabilities']['writes']);self.assertEqual(state['plan_editor']['mode'],'chat_confirmation')
        self.assertEqual(state['plan_editor']['revisions'][self.day],calendar_plan.revision(self.db,self.day))
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
    def test_chat_buttons_clear_and_manual_off_not_labelled_imported(self):
        calendar_plan.apply(self.db,self.draft('off'));text,menu=calendar_views.day_view(self.db,self.day)
        buttons=[b for row in menu.inline_keyboard for b in row]
        self.assertTrue(any('Рабочий' in b.text for b in buttons));self.assertTrue(any('✓ Выходной' in b.text for b in buttons))
        self.assertNotIn('импортированному',text)
    def test_equal_clock_and_off_with_clock_rejected(self):
        for args in ((self.day,'shift','09:00','09:00'),(self.day,'off','09:00','18:00')):
            with self.assertRaises(ValueError):calendar_plan.validate(*args)

class CalendarChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'fixture.sqlite3');core.put_config(self.db,'owner_id','111')
        self.day=(datetime.now(core.TZ).date()+timedelta(days=2)).isoformat()
        payload='p_'+self.day.replace('-','')+'_o_0000_0000_'+calendar_plan.revision(self.db,self.day)
        self.ctx=SimpleNamespace(args=[payload],user_data={},application=SimpleNamespace(bot_data={'db':self.db}),bot=SimpleNamespace(send_message=AsyncMock()))
        self.update=SimpleNamespace(effective_user=SimpleNamespace(id=111),effective_chat=SimpleNamespace(id=111,type='private'),message=SimpleNamespace(reply_text=AsyncMock()),callback_query=None)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    async def click(self,data):
        self.update.callback_query=FakeQuery(data);await bot.inline_action(self.update,self.ctx)
    async def test_open_link_only_prepares_then_save_changes_plan_once(self):
        await bot.started(self.update,self.ctx);self.assertIsNone(workflows.plan_day(self.db,self.day))
        selected=self.ctx.user_data['extra_flow'];token=selected['_confirmation_token']
        self.assertIn('Было:',self.ctx.bot.send_message.call_args.kwargs['text'])
        await self.click('extra:save:'+token);self.assertEqual(workflows.plan_day(self.db,self.day)['status'],'off')
        before='\n'.join(self.db.iterdump());await self.click('extra:save:'+token);self.assertEqual(before,'\n'.join(self.db.iterdump()))
    async def test_foreign_user_cannot_prepare_or_save(self):
        self.update.effective_user.id=222;await bot.started(self.update,self.ctx)
        self.assertNotIn('extra_flow',self.ctx.user_data);self.assertEqual(self.ctx.bot.send_message.await_count,0)
    async def test_changed_plan_rejects_chat_confirmation(self):
        await bot.started(self.update,self.ctx);token=self.ctx.user_data['extra_flow']['_confirmation_token']
        workflows.set_plan(self.db,self.day,'shift','10:00','19:00')
        await self.click('extra:save:'+token);self.assertEqual(workflows.plan_day(self.db,self.day)['start'],'10:00');self.assertNotIn('extra_flow',self.ctx.user_data)
    async def test_cancel_never_saves(self):
        await bot.started(self.update,self.ctx);await self.click('extra:cancel');self.assertIsNone(workflows.plan_day(self.db,self.day))
