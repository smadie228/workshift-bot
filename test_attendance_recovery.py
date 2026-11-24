"""Fictional-data recovery regressions; never connect to the live database."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import core
import workflows
import miniapp_actions
import miniapp_server
import actual_shift
import attendance_recovery as ar
import office_photo

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.db=core.connect(Path(self.folder.name)/'test.sqlite3')
        self.at=datetime(2024,7,20,18,0,tzinfo=core.TZ);self.seq=0
        self.patch=patch('core.now',return_value=self.at);self.patch.start()
    def tearDown(self):self.patch.stop();self.db.close();self.folder.cleanup()
    def send(self,payload):
        self.seq+=1;payload=dict(payload);payload.setdefault('nonce',f'{self.seq:032x}')
        return miniapp_actions.execute_action(self.db,77,payload)
    def clock(self,action='clock_in_at',at='2024-07-20T14:20',sid=None):
        return {'action':action,'shift_id':sid,'mode':'chosen','at':at,'expected_revision':actual_shift.attendance_revision(self.db) if action=='clock_in_at' else actual_shift.shift_revision(self.db,sid)}
    def shift(self):return core.corrected_shift(self.db,'2024-07-10','16:00','02:00',30)
    def delete(self,sid):return self.send({'action':'delete_shift','shift_id':sid,'expected_revision':actual_shift.shift_revision(self.db,sid)})
    def restore(self,eid):return self.send({'action':'restore_shift','event_id':eid,'expected_revision':ar.restore_revision(self.db,eid)})
    def dump(self):return '\n'.join(self.db.iterdump())
    def test_delayed_arrival_records_actual_time_not_submission(self):
        result=self.send(self.clock());row=core.open_shift(self.db)
        self.assertEqual(core.read_time(row['start_utc']).strftime('%H:%M'),'14:20')
        details=json.loads(self.db.execute("SELECT details FROM events WHERE kind='in'").fetchone()[0])
        self.assertEqual(details['delay_seconds'],220*60);self.assertEqual(result['event'],'clock_in_at')
    def test_delayed_departure(self):
        sid=self.send(self.clock())['id'];self.send(self.clock('clock_out_at','2024-07-20T17:30',sid))
        row=self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone()
        self.assertEqual(core.read_time(row['end_utc']).strftime('%H:%M'),'17:30');self.assertAlmostEqual(core.paid_hours(row),2+40/60)
    def test_now_uses_server_clock(self):
        p=self.clock();p.update(mode='now',at=None);self.send(p)
        self.assertEqual(core.open_shift(self.db)['start_utc'],core.iso(self.at))
    def test_night_departure_keeps_start_day(self):
        self.at=datetime(2024,7,21,4,tzinfo=core.TZ);self.patch.stop();self.patch=patch('core.now',return_value=self.at);self.patch.start()
        sid=self.send(self.clock(at='2024-07-20T16:00'))['id'];self.send(self.clock('clock_out_at','2024-07-21T02:00',sid))
        row=self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone();self.assertEqual(core.local_day(row['start_utc']),'2024-07-20');self.assertEqual(core.paid_hours(row),9.5)
    def test_open_arrival_can_be_corrected_without_closing(self):
        sid=self.send(self.clock(at='2024-07-20T17:50'))['id'];self.send(self.clock('move_open_start','2024-07-20T14:20',sid))
        self.assertEqual(core.read_time(core.open_shift(self.db)['start_utc']).strftime('%H:%M'),'14:20');self.assertIsNone(core.open_shift(self.db)['end_utc'])
    def test_future_and_old_arrivals_rejected(self):
        for stamp in ['2024-07-20T18:01','2024-07-18T09:00']:
            with self.subTest(stamp=stamp),self.assertRaises(ValueError):self.send(self.clock(at=stamp))
        self.assertIsNone(core.open_shift(self.db))
    def test_overlapping_arrival_rejected(self):
        core.corrected_shift(self.db,'2024-07-20','09:00','16:00',30)
        with self.assertRaises(ValueError):self.send(self.clock(at='2024-07-20T15:00'))
    def test_stale_form_cannot_close_new_state(self):
        sid=self.send(self.clock())['id'];old=self.clock('clock_out_at','2024-07-20T17:30',sid)
        self.send(self.clock('move_open_start','2024-07-20T14:00',sid))
        with self.assertRaises(ValueError):self.send(old)
        self.assertIsNotNone(core.open_shift(self.db))
    def test_replay_keeps_same_time_and_single_record(self):
        p=self.clock();p['nonce']='a'*32;first=self.send(p);self.assertEqual(self.send(p),first)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0],1)
    def test_failure_rolls_back_business_and_audit(self):
        with patch('miniapp_actions.log',side_effect=ValueError('fail')):
            with self.assertRaises(ValueError):self.send(self.clock())
        self.assertIsNone(core.open_shift(self.db));self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
    def test_delete_open_and_restore(self):
        sid=self.send(self.clock())['id'];before=dict(core.open_shift(self.db));event=self.delete(sid)
        self.assertIsNone(core.open_shift(self.db));self.assertFalse(self.db.execute('SELECT * FROM actual_days_off').fetchall())
        self.restore(event['event_id']);self.assertEqual(dict(core.open_shift(self.db)),before)
    def test_delete_closed_preserves_plan_money_office(self):
        sid=self.shift();workflows.set_plan(self.db,'2024-07-10','shift','16:00','02:00',allow_past=True)
        plan=[tuple(r) for r in self.db.execute('SELECT * FROM plan_days')];event=self.delete(sid)
        self.assertEqual(core.totals(self.db)['hours'],0);self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM plan_days')],plan)
        self.restore(event['event_id']);self.assertEqual(core.totals(self.db)['hours'],9.5)
    def test_delete_imported_links_restore_exactly(self):
        sid=self.shift();self.db.execute('INSERT INTO historical_imported_shifts VALUES(?,?,?,?,?)',('2024-07-10','fictional.xlsx','Sheet',2,sid))
        event=self.delete(sid);self.assertEqual(ar.deleted_import_days(self.db),{'2024-07-10'})
        self.restore(event['event_id']);self.assertFalse(ar.deleted_import_days(self.db));self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())
    def test_delete_active_break_preserves_archive(self):
        sid=self.send(self.clock())['id']
        with patch('workflows.now',return_value=self.at-timedelta(minutes=10)):workflows.start_break(self.db)
        original=[dict(r) for r in self.db.execute('SELECT * FROM break_sessions')];event=self.delete(sid)
        self.assertFalse(self.db.execute('SELECT * FROM break_sessions').fetchall());self.restore(event['event_id'])
        self.assertEqual([dict(r) for r in self.db.execute('SELECT * FROM break_sessions')],original)
    def test_restore_overlap_rejected_without_partial_writes(self):
        sid=self.shift();eid=self.delete(sid)['event_id'];core.corrected_shift(self.db,'2024-07-10','17:00','01:00',30)
        before=self.dump()
        with self.assertRaises(ValueError):self.restore(eid)
        self.assertEqual(self.dump(),before)
    def test_stale_delete_rejected(self):
        sid=self.send(self.clock())['id'];p={'action':'delete_shift','shift_id':sid,'expected_revision':actual_shift.shift_revision(self.db,sid)}
        self.send(self.clock('move_open_start','2024-07-20T14:00',sid))
        with self.assertRaises(ValueError):self.send(p)
    def test_threshold_exact_five_minutes(self):
        workflows.set_plan(self.db,'2024-07-20','shift','14:20','23:00',allow_past=True)
        for second,ask in [(299,False),(300,False),(301,True)]:
            value=ar.hints(self.db,datetime(2024,7,20,14,20,tzinfo=core.TZ)+timedelta(seconds=second))
            self.assertEqual(value['in']['ask_time'],ask)
    def test_no_plan_is_unknown_not_inferred(self):
        value=ar.hints(self.db,self.at)['in'];self.assertTrue(value['ask_time']);self.assertIsNone(value['planned_at'])
    def test_departure_hint_uses_night_plan(self):
        workflows.set_plan(self.db,'2024-07-20','shift','14:20','02:00',allow_past=True);self.send(self.clock())
        value=ar.hints(self.db,datetime(2024,7,21,2,6,tzinfo=core.TZ))['out']
        self.assertTrue(value['ask_time']);self.assertEqual(value['planned_at'][:16],'2024-07-21T02:00')
    def test_invalid_fields_types_and_date(self):
        p=self.clock()
        for changes in [{'extra':True},{'at':'2024-02-30T12:00'},{'at':'2024-07-20T14:20Z'},{'shift_id':True},{'mode':'now','at':'2024-07-20T14:20'},{'expected_revision':'bad'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.send({**p,**changes})
    def test_deleted_list_is_readonly(self):
        sid=self.shift();self.delete(sid);before=self.dump();items=ar.deleted_list(self.db,'2024-07')
        self.assertEqual(len(items),1);self.assertEqual(self.dump(),before);self.assertEqual(len(items[0]['revision']),64)
    def test_read_state_keeps_all_tables_unchanged(self):
        before=self.dump();state=miniapp_server.summarize(self.db,'2024-07');self.assertIn('attendance_hints',state);self.assertEqual(self.dump(),before)

class OfficePhotoTests(unittest.TestCase):
    setUp=RecoveryTests.setUp
    tearDown=RecoveryTests.tearDown
    send=RecoveryTests.send
    shift=RecoveryTests.shift
    dump=RecoveryTests.dump
    # Only photo-specific methods below; inherited cases are valid independent fixtures.
    def photo(self,hours=9.23):
        self.db.execute('INSERT INTO office_image_days VALUES(?,?,?,?)',('2024-07-10','fictional-photo',hours,office_photo.STATUS))
    def test_decimal_hours_use_seconds_not_minutes(self):
        self.photo();item=office_photo.proposal(self.db,'2024-07-10')
        self.assertEqual(item['paid_seconds'],33228);self.assertFalse(item['photo_confirmed']);self.assertEqual(item['end'][11:19],'01:43:48')
    def test_estimate_applies_only_after_explicit_action(self):
        self.photo();self.shift();before=self.dump();item=office_photo.proposal(self.db,'2024-07-10');self.assertEqual(self.dump(),before)
        self.send({'action':'apply_office_estimate','day':'2024-07-10','expected_revision':item['revision']})
        self.assertAlmostEqual(core.totals(self.db)['hours'],9.23)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM office_days').fetchone()[0],0)
    def test_unknown_photo_number_is_not_zero(self):
        self.db.execute('INSERT INTO office_image_days VALUES(?,?,NULL,?)',('2024-07-10','fictional-photo','numeric_unverified'))
        self.assertIsNone(office_photo.proposal(self.db,'2024-07-10'))
    def test_confirmed_office_prevents_provisional_application(self):
        self.photo();core.set_office(self.db,'2024-07-10',8.5,'fictional-original')
        item=office_photo.proposal(self.db,'2024-07-10');self.assertIsNotNone(item['blocked'])
        with self.assertRaises(ValueError):self.send({'action':'apply_office_estimate','day':'2024-07-10','expected_revision':item['revision']})

    def test_stale_photo_estimate_rejected(self):
        self.photo();item=office_photo.proposal(self.db,'2024-07-10');self.db.execute('UPDATE office_image_days SET office_hours=8')
        with self.assertRaises(ValueError):self.send({'action':'apply_office_estimate','day':'2024-07-10','expected_revision':item['revision']})

if __name__=='__main__':unittest.main()
