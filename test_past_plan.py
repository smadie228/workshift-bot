"""Retrospective single-day schedules, exact correction links: isolated fictional data."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import core,workflows,calendar_plan,batch_plan,calendar_views,extra_wizard,confirmation
import finance,ledger,quick_routes,edit_wizard,bot,miniapp_server
from test_callbacks import FakeQuery

class PastPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'fixture.sqlite3')
        self.day='2024-07-10';self.sid=core.corrected_shift(self.db,self.day,'09:00','18:00',30)
        core.set_office(self.db,self.day,8.5,'Fictional verified source')
        finance.add_payment(self.db,'2024-07',1,100,'2024-07-20','Fictional')
        workflows.add_adjustment(self.db,self.day,'bonus',25,'Fictional')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def dump(self):return '\n'.join(self.db.iterdump())
    def protected(self):
        excluded={'events','sqlite_sequence','plan_days','plan_clears'}
        names=[r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in excluded]
        return {n:sorted(repr(tuple(r)) for r in self.db.execute('SELECT * FROM "'+n+'"')) for n in names}
    def draft(self,status='shift'):
        return calendar_plan.flow(self.db,self.day,status,'16:00' if status=='shift' else None,'02:00' if status=='shift' else None)
    def test_preview_and_link_are_read_only(self):
        before=self.dump();flow=self.draft();payload='p_20240710_s_1600_0200_'+flow['expected_revision']
        self.assertEqual(calendar_plan.parse(payload)[:4],(self.day,'shift','16:00','02:00'))
        self.assertEqual(before,self.dump())
    def test_all_plan_states_leave_payroll_and_facts_exactly_unchanged(self):
        original=self.protected();totals=ledger.month_ledger(self.db,'2024-07')['summary']
        for status in ('shift','off','unset','shift'):
            calendar_plan.apply(self.db,self.draft(status))
            self.assertEqual(original,self.protected());self.assertEqual(totals,ledger.month_ledger(self.db,'2024-07')['summary'])
    def test_past_plan_does_not_create_work_or_projected_pay(self):
        day='2024-07-12';calendar_plan.apply(self.db,calendar_plan.flow(self.db,day,'shift','09:00','18:00'))
        result=ledger.month_ledger(self.db,'2024-07')
        self.assertEqual(result['summary']['shifts'],1);self.assertEqual(result['forecast']['future_pay'],0)
    def test_default_low_level_and_batch_paths_remain_future_only(self):
        for fn in (lambda:workflows.set_plan(self.db,self.day,'off'),lambda:workflows.clear_plan(self.db,self.day),lambda:batch_plan.flow(self.db,'2024-07',[self.day],'off'),lambda:calendar_plan.validate(self.day,'off')):
            with self.assertRaises(ValueError):fn()
    def test_invalid_dates_and_clocks_still_rejected(self):
        for day,status,start,end in [('2023-02-29','off',None,None),('1999-12-31','off',None,None),('2101-01-01','off',None,None),(self.day,'shift','25:00','02:00'),(self.day,'shift','09:00','09:00'),(self.day,'off','09:00','18:00')]:
            with self.assertRaises(ValueError):calendar_plan.flow(self.db,day,status,start,end)
    def test_leap_day_is_supported(self):
        day='2024-02-29';calendar_plan.apply(self.db,calendar_plan.flow(self.db,day,'off'));self.assertEqual(workflows.plan_day(self.db,day)['status'],'off')
    def test_unset_keeps_import_source_and_suppresses_its_plan(self):
        self.db.execute('INSERT INTO history_source_rows(source,sheet,excel_row,day,status,start_time,end_time,imported_utc) VALUES(?,?,?,?,?,?,?,?)',('fictional.xlsx','Plan',1,self.day,'План','16:00','02:00',core.iso(core.now())))
        original=self.protected();calendar_plan.apply(self.db,self.draft('unset'))
        self.assertIsNone(workflows.plan_day(self.db,self.day));self.assertTrue(workflows.is_plan_cleared(self.db,self.day));self.assertEqual(original,self.protected())
    def test_historical_audit_records_before_and_after(self):
        calendar_plan.apply(self.db,self.draft('off'));calendar_plan.apply(self.db,self.draft('shift'))
        event=json.loads(self.db.execute("SELECT details FROM events WHERE kind='plan_change' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertTrue(event['historical']);self.assertEqual(event['before']['status'],'off');self.assertEqual(event['after']['status'],'shift');self.assertEqual(event['previous_manual']['status'],'off')
    def test_unset_audit_records_historical_flag(self):
        calendar_plan.apply(self.db,self.draft('unset'))
        event=json.loads(self.db.execute("SELECT details FROM events WHERE kind='plan_change' ORDER BY id DESC LIMIT 1").fetchone()[0]);self.assertTrue(event['historical']);self.assertEqual(event['operation'],'unset')
    def test_stale_confirmation_never_overwrites_newer_plan(self):
        stale=self.draft();calendar_plan.apply(self.db,self.draft('off'));before=self.dump()
        with self.assertRaises(ValueError):calendar_plan.apply(self.db,stale)
        self.assertEqual(before,self.dump())
    def test_audit_failure_rolls_back_plan(self):
        selected=self.draft();before=self.dump()
        with patch('workflows.log',side_effect=ValueError('fictional audit failure')),self.assertRaises(ValueError):calendar_plan.apply(self.db,selected)
        self.assertEqual(before,self.dump())
    def test_chat_card_exposes_separate_actions_for_past(self):
        text,keys=calendar_views.day_view(self.db,self.day);actions=[b.callback_data for row in keys.inline_keyboard for b in row]
        self.assertIn('edit:fix:'+str(self.sid),actions)
        for status in ('work','off','custom','unset'):self.assertIn('extra:plan:'+status+':'+self.day,actions)
    def test_confirmation_clearly_marks_historical_plan(self):
        text=extra_wizard.flow_prompt(self.draft());self.assertIn('Прошедшая дата',text);self.assertIn('Было:',text);self.assertIn('Будет:',text);self.assertIn('заработок не изменятся',text)
    def test_api_advertises_single_day_not_bulk_past_capability(self):
        before=self.dump();result=miniapp_server.summarize(self.db,'2024-07')
        self.assertTrue(result['plan_editor']['supports_past_plan']);self.assertTrue(result['plan_editor']['supports_exact_correction']);self.assertEqual(before,self.dump())
    def test_exact_route_parser_rejects_ambiguous_or_invalid_values(self):
        self.assertEqual(quick_routes.parse_destination('q_fix_20240710_1'),('fix',(self.day,1)))
        for value in ('q_fix_20240710_0','q_fix_20240710_-1','q_fix_20240710_01','q_fix_20240710_9007199254740992','q_fix_20240230_1','q_fix_20240710_1_extra'):
            with self.assertRaises(ValueError):quick_routes.parse_destination(value)

class PastPlanChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.tmp.name)/'fixture.sqlite3');core.put_config(self.db,'owner_id','111')
        self.day='2024-07-10';self.sid=core.corrected_shift(self.db,self.day,'09:00','18:00',30)
        self.ctx=SimpleNamespace(args=[],user_data={},application=SimpleNamespace(bot_data={'db':self.db}),bot=SimpleNamespace(send_message=AsyncMock()))
        self.update=SimpleNamespace(effective_user=SimpleNamespace(id=111),effective_chat=SimpleNamespace(id=111,type='private'),message=SimpleNamespace(reply_text=AsyncMock()),callback_query=None)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def dump(self):return '\n'.join(self.db.iterdump())
    async def start(self,payload):self.update.callback_query=None;self.ctx.args=[payload];await bot.started(self.update,self.ctx)
    async def click(self,data):self.update.callback_query=FakeQuery(data);await bot.inline_action(self.update,self.ctx)
    async def test_past_plan_requires_owner_confirmation_and_replay_is_noop(self):
        before=self.dump();await self.start('p_20240710_o_0000_0000_'+calendar_plan.revision(self.db,self.day));self.assertEqual(before,self.dump())
        token=self.ctx.user_data['extra_flow']['_confirmation_token'];await self.click('extra:save:'+token)
        self.assertEqual(workflows.plan_day(self.db,self.day)['status'],'off');saved=self.dump();await self.click('extra:save:'+token);self.assertEqual(saved,self.dump())
    async def test_old_chat_custom_plan_wizard_can_start_on_past_day(self):
        before=self.dump();await self.click('extra:plan:custom:'+self.day)
        self.assertEqual(self.ctx.user_data['extra_flow']['step'],'start');self.assertEqual(before,self.dump())
    async def test_cancel_past_plan_does_not_write(self):
        before=self.dump();await self.click('extra:plan:off:'+self.day);await self.click('extra:cancel');self.assertEqual(before,self.dump())
    async def test_exact_correction_link_opens_only_the_requested_shift(self):
        before=self.dump();await self.start(f'q_fix_20240710_{self.sid}')
        self.assertEqual(self.ctx.user_data['correction']['shift_id'],self.sid);self.assertEqual(self.ctx.user_data['correction']['step'],'start');self.assertEqual(before,self.dump())
    async def test_mismatched_date_or_missing_shift_never_opens_editor(self):
        before=self.dump()
        for payload in (f'q_fix_20240711_{self.sid}','q_fix_20240710_999999'):
            await self.start(payload);self.assertNotIn('correction',self.ctx.user_data)
        self.assertEqual(before,self.dump())
    async def test_foreign_user_and_group_cannot_start_either_edit(self):
        before=self.dump()
        for user,chat in ((999,'private'),(111,'group')):
            self.update.effective_user.id=user;self.update.effective_chat.type=chat
            for payload in (f'q_fix_20240710_{self.sid}','p_20240710_o_0000_0000_'+calendar_plan.revision(self.db,self.day)):
                await self.start(payload);self.assertNotIn('correction',self.ctx.user_data);self.assertNotIn('extra_flow',self.ctx.user_data)
        self.assertEqual(before,self.dump())
    async def test_exact_edit_cancellation_keeps_plan_and_facts(self):
        calendar_plan.apply(self.db,calendar_plan.flow(self.db,self.day,'off'));before=self.dump()
        await self.start(f'q_fix_20240710_{self.sid}');await self.click('edit:cancel');self.assertEqual(before,self.dump())
