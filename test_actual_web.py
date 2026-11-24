"""Direct attendance and actual-time edits. Temporary data and fictional auth only."""
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
import core
import workflows
import actual_shift
import miniapp_actions
import miniapp_server
import test_miniapp
from test_miniapp import signed


class ActualWebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'fixture.sqlite3'
        self.db=core.connect(self.path)
        self.sid=core.corrected_shift(self.db,'2024-07-10','16:14','00:42',30)
        self.db.execute('''CREATE TABLE IF NOT EXISTS miniapp_operations(owner_id TEXT NOT NULL,nonce TEXT NOT NULL,
            request_hash TEXT NOT NULL,result_json TEXT NOT NULL,created_utc TEXT NOT NULL,PRIMARY KEY(owner_id,nonce))''')
    def tearDown(self):self.db.close();self.temp.cleanup()
    def request(self,**changes):
        value={'action':'correct_shift','nonce':'a'*32,'shift_id':self.sid,'day':'2024-07-10','start':'16:00','end':'01:00',
               'break_minutes':30,'expected_revision':actual_shift.shift_revision(self.db,self.sid),'reason':'Уточнение'}
        value.update(changes);return value
    def dump(self):return '\n'.join(self.db.iterdump())
    def execute(self,payload):return miniapp_actions.execute_action(self.db,77,payload)
    def test_closed_edit_updates_hours_and_returns_pay(self):
        result=self.execute(self.request());self.assertEqual(result['hours'],8.5);self.assertEqual(result['earnings'],2550)
        row=self.db.execute('SELECT * FROM shifts WHERE id=?',(self.sid,)).fetchone()
        self.assertEqual(core.read_time(row['end_utc']).strftime('%H:%M'),'01:00')
    def test_plan_office_and_payments_unchanged(self):
        self.db.execute("INSERT INTO plan_days VALUES('2024-07-10','off',NULL,NULL,'',?,?)",(core.iso(core.now()),core.iso(core.now())))
        before={table:[tuple(r) for r in self.db.execute('SELECT * FROM '+table)] for table in ['plan_days','plan_clears','office_days','received_payments','pay_adjustments','rate_history','history_source_rows']}
        self.execute(self.request())
        self.assertEqual(before,{t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in before})
    def test_replay_is_identical_and_no_second_audit(self):
        payload=self.request();first=self.execute(payload);before=self.dump()
        self.assertEqual(self.execute(payload),first);self.assertEqual(before,self.dump())
    def test_nonce_cannot_refer_to_another_edit(self):
        payload=self.request();self.execute(payload);payload['end']='02:00'
        with self.assertRaisesRegex(ValueError,'nonce_conflict'):self.execute(payload)
    def test_stale_form_rejected_even_if_clock_unchanged(self):
        payload=self.request();core.corrected_shift(self.db,'2024-07-10','16:14','00:42',30,self.sid);before=self.dump()
        with self.assertRaisesRegex(ValueError,'изменилась'):self.execute(payload)
        self.assertEqual(self.dump(),before)
    def test_failure_of_receipt_audit_rolls_back_all(self):
        before=self.dump()
        with patch('miniapp_actions.log',side_effect=ValueError('receipt failed')),self.assertRaises(ValueError):self.execute(self.request())
        self.assertEqual(before,self.dump())
    def test_failure_of_business_audit_rolls_back_all(self):
        before=self.dump()
        with patch('actual_shift.core.log',side_effect=ValueError('audit failed')),self.assertRaises(ValueError):self.execute(self.request())
        self.assertEqual(before,self.dump())
    def test_preserve_seconds_when_only_break_changes(self):
        self.db.execute("UPDATE shifts SET start_utc='2024-07-10T13:14:27+00:00',end_utc='2024-07-10T21:42:46+00:00' WHERE id=?",(self.sid,))
        before=dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(self.sid,)).fetchone())
        self.execute(self.request(start='16:14',end='00:42',break_minutes=15))
        after=self.db.execute('SELECT * FROM shifts WHERE id=?',(self.sid,)).fetchone()
        self.assertEqual(before['start_utc'],after['start_utc']);self.assertEqual(before['end_utc'],after['end_utc'])
    def test_original_object_and_reason_kept_in_audit(self):
        original=dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(self.sid,)).fetchone());self.execute(self.request())
        detail=json.loads(self.db.execute("SELECT details FROM events WHERE kind='fix' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(detail['old'],original);self.assertEqual(detail['reason'],'Уточнение');self.assertEqual(detail['source'],'miniapp')
    def test_bad_types_and_unknown_fields_rejected(self):
        for values in ({'break_minutes':True},{'break_minutes':1.2},{'break_minutes':-1},{'break_minutes':601},{'shift_id':'1'},
                       {'start':'9:00'},{'end':'25:00'},{'start':'16:00','end':'16:00'},{'day':'2024-02-30'},
                       {'expected_revision':'bad'},{'reason':[]},{'reason':'x'*161},{'reason':'x\ny'},{'rate':900}):
            with self.subTest(values=values),self.assertRaises(ValueError):self.execute(self.request(**values))
    def test_break_must_fit_shift(self):
        with self.assertRaisesRegex(ValueError,'Перерыв'):self.execute(self.request(start='16:00',end='17:00',break_minutes=60))
    def test_overlapping_other_shift_rejected(self):
        core.corrected_shift(self.db,'2024-07-11','09:00','18:00',30)
        with self.assertRaisesRegex(ValueError,'пересекаются'):self.execute(self.request(end='10:00'))
    def test_wrong_day_does_not_move_record(self):
        with self.assertRaisesRegex(ValueError,'перенесена'):self.execute(self.request(day='2024-07-09'))
    def test_missing_record_not_created_by_edit(self):
        with self.assertRaises(ValueError):self.execute(self.request(shift_id=999))
    def test_future_completed_shift_rejected(self):
        payload=self.request(action='add_shift',shift_id=None,day='2099-07-10',expected_revision=actual_shift.day_revision(self.db,'2099-07-10'))
        with self.assertRaisesRegex(ValueError,'будущего'):self.execute(payload)
    def test_add_missing_shift_and_replay(self):
        payload=self.request(action='add_shift',shift_id=None,day='2024-07-12',expected_revision=actual_shift.day_revision(self.db,'2024-07-12'))
        first=self.execute(payload);self.assertEqual(self.execute(payload),first)
        self.assertEqual(self.db.execute('SELECT count(*) FROM shifts').fetchone()[0],2)
    def test_add_to_occupied_day_rejected(self):
        with self.assertRaisesRegex(ValueError,'уже есть'):self.execute(self.request(action='add_shift',shift_id=None,expected_revision=actual_shift.day_revision(self.db,'2024-07-10')))
    def test_stale_empty_day_rejected(self):
        payload=self.request(action='add_shift',shift_id=None,day='2024-07-12',expected_revision=actual_shift.day_revision(self.db,'2024-07-12'))
        core.corrected_shift(self.db,'2024-07-12','09:00','10:00',0)
        with self.assertRaisesRegex(ValueError,'изменились'):self.execute(payload)
    def open_fixture(self):
        at=datetime(2024,7,20,6,tzinfo=timezone.utc);_,row=core.toggle(self.db,123,at);return row['id']
    def test_close_forgotten_shift_in_web(self):
        sid=self.open_fixture();payload=self.request(action='close_shift',shift_id=sid,day='2024-07-20',start='09:00',end='18:00',expected_revision=actual_shift.shift_revision(self.db,sid))
        result=self.execute(payload);self.assertEqual(result['hours'],8.5);self.assertIsNone(core.open_shift(self.db))
    def test_closed_shift_cannot_use_close_action(self):
        with self.assertRaisesRegex(ValueError,'уже закрыта'):self.execute(self.request(action='close_shift'))
    def test_open_shift_cannot_use_normal_edit(self):
        sid=self.open_fixture()
        with self.assertRaisesRegex(ValueError,'открытой'):self.execute(self.request(shift_id=sid,day='2024-07-20',expected_revision=actual_shift.shift_revision(self.db,sid)))
    def test_active_break_blocks_close(self):
        sid=self.open_fixture();workflows.start_break(self.db,datetime(2024,7,20,7,tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError,'перерыв'):self.execute(self.request(action='close_shift',shift_id=sid,day='2024-07-20',start='09:00',end='18:00',expected_revision=actual_shift.shift_revision(self.db,sid)))
    def test_tracked_break_evidence_preserved(self):
        sid=self.open_fixture();workflows.start_break(self.db,datetime(2024,7,20,7,tzinfo=timezone.utc));workflows.stop_break(self.db,datetime(2024,7,20,7,15,tzinfo=timezone.utc));core.toggle(self.db,124,datetime(2024,7,20,15,tzinfo=timezone.utc))
        sessions=[tuple(r) for r in self.db.execute('SELECT * FROM break_sessions')]
        payload=self.request(shift_id=sid,day='2024-07-20',start='09:00',end='18:00',expected_revision=actual_shift.shift_revision(self.db,sid))
        self.execute(payload);self.assertEqual(sessions,[tuple(r) for r in self.db.execute('SELECT * FROM break_sessions')])
        with self.assertRaisesRegex(ValueError,'перерыв'):self.execute(self.request(nonce='b'*32,shift_id=sid,day='2024-07-20',start='11:00',end='18:00',expected_revision=actual_shift.shift_revision(self.db,sid)))
    def test_read_projection_includes_versions_without_writing(self):
        before=self.dump();result=miniapp_server.summarize(self.db,'2024-07')
        self.assertEqual(result['shifts'][0]['revision'],actual_shift.shift_revision(self.db,self.sid));self.assertEqual(len(result['attendance_revision']),64)
        self.assertEqual(before,self.dump())
    def test_stale_arrival_cannot_follow_chat_cycle(self):
        revision=actual_shift.attendance_revision(self.db);sid=self.open_fixture();core.toggle(self.db,124,datetime(2024,7,20,15,tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError,'Состояние'):self.execute({'action':'in','nonce':'a'*32,'state_revision':revision})
        self.assertIsNone(core.open_shift(self.db))
    def test_concurrent_edits_only_one_wins(self):
        original=self.request();barrier=Barrier(2)
        def work(nonce):
            db=core.connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:return miniapp_actions.execute_action(db,77,{**original,'nonce':nonce*32})
                except ValueError:return {'rejected':True}
            finally:db.close()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(work,['a','b']))
        self.assertEqual(sum(bool(r.get('success')) for r in results),1)
    def test_chat_form_carries_current_revision(self):
        import edit_wizard
        flow=edit_wizard.existing_shift_flow(self.db,self.sid)
        self.assertEqual(flow['expected_revision'],actual_shift.shift_revision(self.db,self.sid))
    def test_stale_chat_form_cannot_overwrite_web_edit(self):
        revision=actual_shift.shift_revision(self.db,self.sid);self.execute(self.request());before=self.dump()
        with self.assertRaisesRegex(ValueError,'изменилась'):
            core.corrected_shift(self.db,'2024-07-10','16:14','00:42',30,self.sid,expected_revision=revision)
        self.assertEqual(self.dump(),before)
    def test_fresh_chat_form_still_saves(self):
        revision=actual_shift.shift_revision(self.db,self.sid)
        core.corrected_shift(self.db,'2024-07-10','16:14','01:00',30,self.sid,expected_revision=revision)
        self.assertEqual(core.read_time(self.db.execute('SELECT end_utc FROM shifts WHERE id=?',(self.sid,)).fetchone()[0]).strftime('%H:%M'),'01:00')
    def test_stale_chat_close_cannot_ignore_started_break(self):
        sid=self.open_fixture();revision=actual_shift.shift_revision(self.db,sid)
        workflows.start_break(self.db,datetime(2024,7,20,7,tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError,'изменилась'):
            core.close_open_shift(self.db,sid,'18:00',30,expected_revision=revision)
    def test_helpers_require_outer_transaction(self):
        with self.assertRaises(RuntimeError):actual_shift.apply(self.db,self.request())


class ActualHttpTests(unittest.TestCase):
    setUp=test_miniapp.MiniAppTests.setUp
    tearDown=test_miniapp.MiniAppTests.tearDown
    request=test_miniapp.MiniAppTests.request
    def test_real_http_edit_enabled_and_replay(self):
        db=core.connect(self.dbpath);sid=core.corrected_shift(db,'2024-07-10','09:00','18:00',30);rev=actual_shift.shift_revision(db,sid);db.close()
        payload={'action':'correct_shift','nonce':'a'*32,'shift_id':sid,'day':'2024-07-10','start':'10:00','end':'18:00','break_minutes':15,'expected_revision':rev}
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'1'}):
            first=self.request('/mini/api/action',payload,signed());self.assertEqual(first[0],200);self.assertEqual(first[1]['hours'],7.75)
            self.assertEqual(self.request('/mini/api/action',payload,signed())[1],first[1])
    def test_owner_and_current_auth_required_for_edits(self):
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'1'}):
            for auth in (None,signed(user_id=999),signed(at=1)):
                self.assertEqual(self.request('/mini/api/action',{'action':'in','nonce':'a'*32},auth)[0],403)
        db=core.connect(self.dbpath);self.assertIsNone(core.open_shift(db));db.close()
    def test_origin_required_for_production_writes(self):
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'1','MINIAPP_PUBLIC_ORIGIN':'https://example.com'}):
            self.assertEqual(self.request('/mini/api/action',{'action':'in','nonce':'a'*32},signed())[0],403)
    def test_writes_flag_still_gates_actual_edits(self):
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'0'}):
            self.assertEqual(self.request('/mini/api/action',{'action':'correct_shift','nonce':'a'*32},signed())[0],403)
    def test_capabilities_reflect_flag(self):
        with patch.dict(os.environ,{'MINIAPP_ENABLE_WRITES':'1'}):
            code,value,_=self.request('/mini/api/state',{'month':'2024-07'},signed())
        self.assertEqual(code,200);self.assertTrue(value['capabilities']['writes']);self.assertTrue(value['capabilities']['actual_edits'])
