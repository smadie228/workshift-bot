"""Direct schedule and reversible actual-off tests: fictional temporary data only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta
from unittest.mock import patch
import json
import os
import unittest
import core, ledger, workflows, calendar_plan, batch_plan, miniapp_actions, actual_shift, actual_day, web_plan


class FriendlyPlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.path=Path(self.tmp.name)/'fixture.sqlite3';self.db=core.connect(self.path)
        self.day='2024-07-10';self.month='2099-07';self.count=0
        self.db.execute('''CREATE TABLE IF NOT EXISTS miniapp_operations(owner_id TEXT NOT NULL,nonce TEXT NOT NULL,
            request_hash TEXT NOT NULL,result_json TEXT NOT NULL,created_utc TEXT NOT NULL,PRIMARY KEY(owner_id,nonce))''')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def nonce(self):self.count+=1;return f'{self.count:032x}'
    def dump(self):return '\n'.join(self.db.iterdump())
    def off(self,day=None,replace=False,restore=False):
        day=day or self.day
        return {'action':'restore_day_off' if restore else 'mark_day_off','nonce':self.nonce(),'day':day,
                'replace_shifts':replace,'expected_revision':actual_shift.day_revision(self.db,day)}
    def execute(self,value):return miniapp_actions.execute_action(self.db,77,value)
    def fact(self):return core.corrected_shift(self.db,self.day,'16:00','02:00',30)
    def plan(self,day=None,status='off'):
        day=day or self.day
        return {'action':'set_plan','nonce':self.nonce(),'day':day,'status':status,
          'start':'16:00' if status=='shift' else None,'end':'02:00' if status=='shift' else None,
          'expected_revision':calendar_plan.revision(self.db,day)}
    def group(self,status='off',days=None):
        return {'action':'set_plan_batch','nonce':self.nonce(),'month':self.month,
          'days':days or [self.month+'-10',self.month+'-31'],'status':status,
          'start':'16:00' if status=='shift' else None,'end':'02:00' if status=='shift' else None,
          'expected_revision':batch_plan.revision(self.db,self.month)}
    def test_empty_actual_off_does_not_change_plan(self):
        self.execute(self.plan(status='shift'));self.execute(self.off())
        r=next(r for r in ledger.month_ledger(self.db,'2024-07')['days'] if r['day']==self.day)
        self.assertEqual(r['status'],'actual_off');self.assertEqual(r['hours'],0);self.assertEqual(r['plan']['status'],'shift')
        self.assertEqual(core.totals(self.db)['shifts'],0)
    def test_work_needs_separate_explicit_confirmation(self):
        self.fact();request=self.off();before=self.dump()
        with self.assertRaises(ValueError):self.execute(request)
        self.assertEqual(self.dump(),before)
    def test_actual_off_archives_and_restores_exact_records(self):
        sid=self.fact();before=dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone());pay=core.totals(self.db)['earnings']
        self.execute(self.off(replace=True));self.assertEqual(core.totals(self.db)['hours'],0)
        saved=json.loads(actual_day.get(self.db,self.day)['archive_json']);self.assertEqual(saved['shifts'],[before])
        self.execute(self.off(restore=True));self.assertEqual(core.totals(self.db)['earnings'],pay)
        self.assertEqual(dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone()),before)
    def test_actual_off_preserves_break_evidence(self):
        sid=self.fact();start=core.iso(core.parse_local_datetime(self.day,'20:00'));end=core.iso(core.parse_local_datetime(self.day,'20:15'))
        self.db.execute('INSERT INTO tracked_break_shifts VALUES(?,?)',(sid,start))
        self.db.execute('INSERT INTO break_sessions(shift_id,started_utc,ended_utc,minutes,created_utc) VALUES(?,?,?,?,?)',(sid,start,end,15,start))
        original=[tuple(r) for r in self.db.execute('SELECT * FROM break_sessions')]
        self.execute(self.off(replace=True));self.assertFalse(self.db.execute('SELECT * FROM break_sessions').fetchall())
        self.execute(self.off(restore=True));self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM break_sessions')],original)
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())
    def test_open_shift_not_replaced(self):
        core.toggle(self.db,100,core.parse_local_datetime(self.day,'16:00'));before=self.dump()
        with self.assertRaises(ValueError):self.execute(self.off(replace=True))
        self.assertEqual(self.dump(),before)
    def test_future_actual_off_rejected(self):
        with self.assertRaises(ValueError):self.execute(self.off('2099-07-10'))
    def test_duplicate_off_and_restore_return_same_receipt(self):
        self.fact();request=self.off(replace=True);result=self.execute(request);dump=self.dump()
        self.assertEqual(self.execute(request),result);self.assertEqual(self.dump(),dump)
        request=self.off(restore=True);result=self.execute(request);dump=self.dump()
        self.assertEqual(self.execute(request),result);self.assertEqual(self.dump(),dump)
    def test_nonce_conflict_rejected(self):
        request=self.off();self.execute(request);request['replace_shifts']=True
        with self.assertRaisesRegex(ValueError,'nonce_conflict'):self.execute(request)
    def test_actual_off_stale_form_rejected(self):
        request=self.off();self.fact();before=self.dump()
        with self.assertRaises(ValueError):self.execute(request)
        self.assertEqual(self.dump(),before)
    def test_archive_restore_invalidates_old_shift_revision(self):
        sid=self.fact();old=actual_shift.shift_revision(self.db,sid)
        self.execute(self.off(replace=True));self.execute(self.off(restore=True))
        self.assertNotEqual(actual_shift.shift_revision(self.db,sid),old)
    def test_actual_off_write_rolls_back_on_failed_audit(self):
        self.fact();request=self.off(replace=True);before=self.dump()
        with patch('core.log',side_effect=ValueError('audit_failure')):
            with self.assertRaises(ValueError):self.execute(request)
        self.assertEqual(self.dump(),before)
    def test_office_and_money_tables_unchanged(self):
        self.fact();self.execute(self.plan())
        self.db.execute('INSERT INTO office_days VALUES(?,?,?,?)',(self.day,8,'fictional',core.iso(core.now())))
        protected=['office_days','plan_days','rate_history','received_payments','pay_adjustments','history_source_rows']
        before={t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in protected}
        self.execute(self.off(replace=True))
        self.assertEqual({t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in protected},before)
    def test_new_arrival_supersedes_off_with_history(self):
        today=core.now().astimezone(core.TZ).date().isoformat();self.execute(self.off(today))
        core.toggle(self.db,55);self.assertIsNone(actual_day.get(self.db,today));self.assertIsNotNone(core.open_shift(self.db))
        self.assertTrue(self.db.execute("SELECT 1 FROM events WHERE kind='actual_day_off_cleared'").fetchone())
    def test_manual_add_supersedes_off(self):
        self.execute(self.off());self.fact();self.assertIsNone(actual_day.get(self.db,self.day))
    def test_restore_conflicting_other_day_rejected(self):
        self.fact();self.execute(self.off(replace=True))
        core.corrected_shift(self.db,'2024-07-11','00:30','05:00',15)
        before=self.dump()
        with self.assertRaises(ValueError):self.execute(self.off(restore=True))
        self.assertEqual(self.dump(),before)
    def test_empty_off_restore_is_not_a_work_record(self):
        self.execute(self.off());self.execute(self.off(restore=True))
        self.assertIsNone(actual_day.get(self.db,self.day));self.assertEqual(core.totals(self.db)['shifts'],0)
    def test_actual_off_strict_fields(self):
        for key,value in [('replace_shifts',1),('expected_revision','bad'),('day','2024-02-30'),('extra',True)]:
            request=self.off();request[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.execute(request)
    def test_work_does_not_report_missing_plan_on_off(self):
        yesterday=(core.now().astimezone(core.TZ).date()-timedelta(days=1)).isoformat()
        self.execute(self.plan(yesterday,'shift'));self.execute(self.off(yesterday))
        self.assertFalse(any(r['day']==yesterday for r in workflows.issues(self.db)))
    def test_office_zero_matches_actual_off(self):
        self.execute(self.off());self.db.execute('INSERT INTO office_days VALUES(?,?,?,?)',(self.day,0,'fictional',core.iso(core.now())))
        r=next(r for r in ledger.month_ledger(self.db,'2024-07')['days'] if r['day']==self.day)
        self.assertEqual(r['comparison'],'matched')
    def test_direct_plan_all_statuses_preserve_pay(self):
        self.fact();pay=core.totals(self.db);facts=[tuple(r) for r in self.db.execute('SELECT * FROM shifts')]
        for status in ['shift','off','unset']:
            result=self.execute(self.plan(status=status));self.assertEqual(result['event'],'set_plan')
            self.assertEqual(core.totals(self.db),pay);self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM shifts')],facts)
    def test_direct_plan_replay_preserves_newer_edit(self):
        request=self.plan(status='shift');result=self.execute(request);self.execute(self.plan(status='off'))
        self.assertEqual(self.execute(request),result);self.assertEqual(workflows.plan_day(self.db,self.day)['status'],'off')
    def test_direct_plan_stale_rejected(self):
        request=self.plan(status='shift');self.execute(self.plan(status='off'));before=self.dump()
        with self.assertRaises(ValueError):self.execute(request)
        self.assertEqual(self.dump(),before)
    def test_direct_plan_nested_helpers_require_transaction(self):
        for fn,selected in [(calendar_plan.apply,calendar_plan.flow(self.db,self.day,'off')),(batch_plan.apply,batch_plan.flow(self.db,self.month,[self.month+'-10'],'off'))]:
            with self.assertRaises(RuntimeError):fn(self.db,selected,manage_transaction=False)
    def test_group_one_transaction_and_day31(self):
        value=self.group('shift');self.execute(value)
        self.assertEqual(len(self.db.execute('SELECT * FROM plan_days').fetchall()),2)
        self.assertEqual(workflows.plan_day(self.db,'2099-07-31')['end'],'02:00')
    def test_group_audit_failure_rolls_every_day_back(self):
        request=self.group();before=self.dump()
        with patch('batch_plan.log',side_effect=ValueError('audit_failure')):
            with self.assertRaises(ValueError):self.execute(request)
        self.assertEqual(self.dump(),before)
    def test_group_stale_rejects_all(self):
        request=self.group();self.execute(self.plan('2099-07-10','shift'));before=self.dump()
        with self.assertRaises(ValueError):self.execute(request)
        self.assertEqual(self.dump(),before)
    def test_group_past_blocked_but_single_allowed(self):
        request=self.group();request.update(month='2024-07',days=[self.day],expected_revision=batch_plan.revision(self.db,'2024-07'))
        with self.assertRaises(ValueError):self.execute(request)
        self.assertTrue(self.execute(self.plan())['success'])
    def test_group_replay_works_after_date_passes(self):
        request=self.group();result=self.execute(request)
        class Later(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2100,1,1,tzinfo=core.TZ)
        with patch('calendar_plan.datetime',Later):self.assertEqual(self.execute(request),result)
    def test_group_bad_days_or_types_rejected(self):
        for days in [[],['2099-07-31','2099-07-31'],['2099-07-32'],['2099-08-01'],[True]]:
            with self.subTest(days=days),self.assertRaises(ValueError):self.execute(self.group(days=days) if days else dict(self.group(),days=[]))
    def test_unsupported_action_types_rejected(self):
        for action in [[],{},None,1]:
            with self.subTest(action=action),self.assertRaises(ValueError):self.execute({'action':action,'nonce':self.nonce()})


    def test_imported_reference_is_archived_and_restored(self):
        sid=self.fact()
        self.db.execute('INSERT INTO historical_imported_shifts VALUES(?,?,?,?,?)',(self.day,'fictional.xlsx','Shifts',2,sid))
        original=[tuple(r) for r in self.db.execute('SELECT * FROM historical_imported_shifts')]
        self.execute(self.off(replace=True))
        self.assertFalse(self.db.execute('SELECT * FROM historical_imported_shifts').fetchall())
        self.assertEqual(len(json.loads(actual_day.get(self.db,self.day)['archive_json'])['historical_imported_shifts']),1)
        self.execute(self.off(restore=True))
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM historical_imported_shifts')],original)
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())
    def test_import_does_not_resurrect_explicit_actual_off(self):
        import import_history
        self.execute(self.off())
        bundle={'schema':1,'assets':[],'summaries':[],'screenshot_times':[],'office_days':[],
          'rows':[{'source':import_history.OCTOBER,'sheet':'Shifts','excel_row':2,'day':self.day,'weekday':'Wed','status':'Работал',
          'start':'09:00','end':'18:00','break_minutes':30,'hours':8.5,'earnings':2550,'checked':None}]}
        result=import_history.apply_import(self.db,bundle)
        self.assertEqual(result['historical_shifts'],0);self.assertEqual(result['conflicts'],1)
        self.assertIsNotNone(actual_day.get(self.db,self.day));self.assertEqual(core.totals(self.db)['shifts'],0)
    def test_restore_rejects_changed_import_link(self):
        sid=self.fact();self.db.execute('INSERT INTO historical_imported_shifts VALUES(?,?,?,?,?)',(self.day,'fictional.xlsx','Shifts',2,sid))
        self.execute(self.off(replace=True))
        other=core.corrected_shift(self.db,'2024-07-12','09:00','18:00',30)
        self.db.execute('INSERT INTO historical_imported_shifts VALUES(?,?,?,?,?)',(self.day,'other.xlsx','Shifts',3,other))
        before=self.dump()
        with self.assertRaises(ValueError):self.execute(self.off(restore=True))
        self.assertEqual(self.dump(),before)


class FriendlyHttpTests(unittest.TestCase):
    from test_miniapp import MiniAppTests
    setUp=MiniAppTests.setUp;tearDown=MiniAppTests.tearDown;request=MiniAppTests.request
    def test_new_capabilities_default_readonly(self):
        from test_miniapp import signed
        code,state,_=self.request('/mini/api/state',{'month':'2024-07'},signed())
        self.assertEqual(code,200);self.assertFalse(state['plan_editor']['direct_writes']);self.assertFalse(state['capabilities']['actual_days_off'])
    def test_anonymous_off_write_denied(self):
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'1'}):
            self.assertEqual(self.request('/mini/api/action',{'action':'mark_day_off','nonce':'a'*32})[0],403)
    def test_enabled_direct_plan_through_http(self):
        from test_miniapp import signed
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'1'}):
            _,state,_=self.request('/mini/api/state',{'month':'2024-07'},signed())
            value={'action':'set_plan','nonce':'a'*32,'day':'2024-07-10','status':'off','expected_revision':state['plan_editor']['revisions']['2024-07-10']}
            self.assertEqual(self.request('/mini/api/action',value,signed())[0],200)
            _,state,_=self.request('/mini/api/state',{'month':'2024-07'},signed())
            self.assertEqual(state['plan'][0]['status'],'off')
