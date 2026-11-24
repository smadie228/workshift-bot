"""Break editor tests. Every case owns its temporary SQLite database."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import core, workflows, actual_shift, miniapp_actions, miniapp_server, web_activity, web_breaks
from test_miniapp import signed
from http.client import HTTPConnection
from threading import Thread


class BreakEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite3'
        self.db=core.connect(self.path);self.n=0
        self.now=datetime(2024,7,11,4,0,tzinfo=core.TZ)
        self.clock=patch('core.now',return_value=self.now);self.clock.start()
        self.sid=core.corrected_shift(self.db,'2024-07-10','16:00','02:00',30)
    def tearDown(self):
        self.clock.stop();self.db.close();self.temp.cleanup()
    def data(self):return web_breaks.details(self.db,{'view':'breaks','shift_id':self.sid})
    def shift(self):return dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(self.sid,)).fetchone())
    def change(self,action='save_break',**kwargs):
        value={'action':action,'shift_id':self.sid,'expected_revision':actual_shift.shift_revision(self.db,self.sid)}
        if action=='save_break':value.update(break_id=None,start='2024-07-10T19:00',end='2024-07-10T19:15')
        value.update(kwargs);return value
    def send(self,payload):
        self.n+=1;return miniapp_actions.execute_action(self.db,77,{'nonce':f'{self.n:032x}',**payload})
    def preview(self,payload):return web_breaks.preview(self.db,{'kind':'break_change','change':payload})
    def undo(self):return self.send(self.change('undo_break_change',event_id=self.data()['undo_event_id']))
    def snapshot(self):
        return {t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in ('shifts','break_sessions','tracked_break_shifts','events','office_days','rate_history','plan_days','received_payments')}
    def test_details_are_read_only_and_do_not_expose_notes(self):
        self.db.execute("UPDATE shifts SET note='private marker' WHERE id=?",(self.sid,));before=self.snapshot();d=self.data()
        self.assertEqual(d['mode'],'total_only');self.assertEqual(d['gross_seconds'],36000);self.assertEqual(d['paid_seconds'],34200)
        self.assertNotIn('private marker',json.dumps(d));self.assertEqual(before,self.snapshot())
    def test_preview_is_read_only(self):
        before=self.snapshot();d=self.preview(self.change());self.assertTrue(d['read_only']);self.assertEqual(d['before_minutes'],30);self.assertEqual(d['after_minutes'],15);self.assertEqual(self.snapshot(),before)
    def test_add_replaces_estimate_not_adds(self):
        old=self.shift();self.send(self.change());s=self.shift();self.assertEqual(s['break_minutes'],15)
        self.assertEqual(s['start_utc'],old['start_utc']);self.assertEqual(s['end_utc'],old['end_utc']);self.assertEqual(self.data()['mode'],'tracked')
    def test_second_interval_sums(self):
        self.send(self.change());self.send(self.change(start='2024-07-10T20:00',end='2024-07-10T20:12'));self.assertEqual(self.shift()['break_minutes'],27)
    def test_edit_specific_interval(self):
        self.send(self.change());bid=self.data()['sessions'][0]['id'];self.send(self.change(break_id=bid,end='2024-07-10T19:20'));self.assertEqual(self.shift()['break_minutes'],20);self.assertEqual(len(self.data()['sessions']),1)
    def test_delete_last_leaves_zero_not_default(self):
        self.send(self.change());bid=self.data()['sessions'][0]['id'];self.send(self.change('remove_break',break_id=bid));self.assertEqual(self.shift()['break_minutes'],0);self.assertEqual(self.data()['mode'],'tracked')
    def test_delete_can_be_undone(self):
        self.send(self.change());before=self.data()['sessions'];self.send(self.change('remove_break',break_id=before[0]['id']));self.undo();self.assertEqual(self.data()['sessions'],before);self.assertEqual(self.shift()['break_minutes'],15)
    def test_undo_first_add_restores_untracked_total(self):
        self.send(self.change());self.undo();d=self.data();self.assertEqual(d['mode'],'total_only');self.assertEqual(d['deducted_minutes'],30);self.assertEqual(d['sessions'],[])
    def test_undo_is_not_offered_after_other_edit(self):
        self.send(self.change());event=self.data()['undo_event_id'];core.log(self.db,'fix',self.sid,{'day':'2024-07-10'})
        self.assertIsNone(self.data()['undo_event_id'])
        with self.assertRaises(ValueError):self.send(self.change('undo_break_change',event_id=event))
    def test_undo_conflicting_interval_id_is_rejected(self):
        self.send(self.change());bid=self.data()['sessions'][0]['id'];self.send(self.change('remove_break',break_id=bid))
        event=self.data()['undo_event_id'];other=core.corrected_shift(self.db,'2024-07-09','16:00','02:00',30)
        self.db.execute('INSERT INTO break_sessions(id,shift_id,started_utc,ended_utc,minutes,created_utc) VALUES(?,?,?,?,?,?)',(bid,other,core.iso(core.parse_local_datetime('2024-07-09','19:00')),core.iso(core.parse_local_datetime('2024-07-09','19:15')),15,core.iso(core.now())))
        before=self.snapshot()
        with self.assertRaises(ValueError):self.send(self.change('undo_break_change',event_id=event))
        self.assertEqual(before,self.snapshot())
    def test_undo_second_time_rejected(self):
        self.send(self.change());event=self.data()['undo_event_id'];self.undo()
        with self.assertRaises(ValueError):self.send(self.change('undo_break_change',event_id=event))
    def test_manual_total_preview_warns_and_undo_restores(self):
        self.send(self.change());self.db.execute('UPDATE shifts SET break_minutes=37 WHERE id=?',(self.sid,));self.assertEqual(self.data()['mode'],'manual_override')
        self.assertTrue(self.preview(self.change(start='2024-07-10T20:00',end='2024-07-10T20:10'))['replaces_total'])
        self.send(self.change(start='2024-07-10T20:00',end='2024-07-10T20:10'));self.assertEqual(self.shift()['break_minutes'],25);self.undo();self.assertEqual(self.shift()['break_minutes'],37)
    def test_overlapping_intervals_rejected(self):
        self.send(self.change());before=self.snapshot()
        with self.assertRaises(ValueError):self.send(self.change(start='2024-07-10T19:10',end='2024-07-10T19:20'))
        self.assertEqual(before,self.snapshot())
    def test_adjacent_intervals_allowed(self):
        self.send(self.change());self.send(self.change(start='2024-07-10T19:15',end='2024-07-10T19:30'));self.assertEqual(self.shift()['break_minutes'],30)
    def test_before_shift_rejected(self):
        with self.assertRaises(ValueError):self.send(self.change(start='2024-07-10T15:00'))
    def test_after_shift_rejected(self):
        with self.assertRaises(ValueError):self.send(self.change(end='2024-07-11T02:01'))
    def test_future_rejected(self):
        self.db.execute('UPDATE shifts SET end_utc=NULL WHERE id=?',(self.sid,))
        with self.assertRaises(ValueError):self.send(self.change(start='2024-07-11T03:00',end='2024-07-11T04:01'))
    def test_reversed_or_equal_rejected(self):
        for end in ['2024-07-10T18:59','2024-07-10T19:00']:
            with self.subTest(end=end),self.assertRaises(ValueError):self.preview(self.change(end=end))
    def test_night_interval_has_explicit_next_date(self):
        self.send(self.change(start='2024-07-10T23:50',end='2024-07-11T00:10'));self.assertEqual(self.shift()['break_minutes'],20)
    def test_seconds_preserved_and_rounded_per_interval(self):
        stamp=core.iso(core.now());a=core.iso(core.parse_local_datetime('2024-07-10','19:00')+timedelta(seconds=15));b=core.iso(core.parse_local_datetime('2024-07-10','19:15')+timedelta(seconds=30))
        bid=self.db.execute('INSERT INTO break_sessions(shift_id,started_utc,ended_utc,minutes,created_utc) VALUES(?,?,?,?,?)',(self.sid,a,b,16,stamp)).lastrowid
        self.send(self.change(break_id=bid));row=self.db.execute('SELECT * FROM break_sessions WHERE id=?',(bid,)).fetchone();self.assertEqual(row['started_utc'],a);self.assertEqual(row['ended_utc'],b);self.assertEqual(row['minutes'],16)
    def test_close_running_at_actual_time(self):
        self.db.execute('UPDATE shifts SET end_utc=NULL WHERE id=?',(self.sid,));workflows.start_break(self.db,at=core.parse_local_datetime('2024-07-10','19:00'));bid=self.data()['sessions'][0]['id']
        self.send(self.change(break_id=bid,end='2024-07-10T19:25'));self.assertFalse(self.data()['running']);self.assertEqual(self.shift()['break_minutes'],25);self.assertIsNone(self.shift()['end_utc'])
    def test_remove_running_and_undo(self):
        self.db.execute('UPDATE shifts SET end_utc=NULL WHERE id=?',(self.sid,));workflows.start_break(self.db,at=core.parse_local_datetime('2024-07-10','19:00'));bid=self.data()['sessions'][0]['id']
        self.send(self.change('remove_break',break_id=bid));self.assertFalse(self.data()['running']);self.undo();self.assertTrue(self.data()['running'])
    def test_other_break_while_running_cannot_overlap(self):
        self.db.execute('UPDATE shifts SET end_utc=NULL WHERE id=?',(self.sid,));workflows.start_break(self.db,at=core.parse_local_datetime('2024-07-10','19:00'))
        with self.assertRaises(ValueError):self.send(self.change(start='2024-07-10T19:20',end='2024-07-10T19:30'))
    def test_stale_form_rejected(self):
        old=self.change();self.send(self.change())
        with self.assertRaisesRegex(ValueError,'изменились'):self.send(old)
    def test_replay_no_duplicate_after_later_change(self):
        p={'nonce':'a'*32,**self.change()};first=miniapp_actions.execute_action(self.db,77,p);self.send(self.change(start='2024-07-10T20:00',end='2024-07-10T20:10'))
        before=self.snapshot();self.assertEqual(miniapp_actions.execute_action(self.db,77,p),first);self.assertEqual(before,self.snapshot())
    def test_nonce_payload_conflict(self):
        p={'nonce':'a'*32,**self.change()};miniapp_actions.execute_action(self.db,77,p)
        with self.assertRaisesRegex(ValueError,'nonce_conflict'):miniapp_actions.execute_action(self.db,77,{**p,'end':'2024-07-10T19:16'})
    def test_atomic_rollback_on_log_failure(self):
        # Creation of the receipt table is harmless; business rows must roll back.
        before=self.snapshot()
        with patch('web_breaks.core.log',side_effect=RuntimeError('test fault')):
            with self.assertRaises(RuntimeError):self.send(self.change())
        self.assertEqual(before,self.snapshot())
    def test_other_shift_break_id_rejected(self):
        self.send(self.change());bid=self.data()['sessions'][0]['id'];other=core.corrected_shift(self.db,'2024-07-09','16:00','02:00',30)
        with self.assertRaises(ValueError):self.send(self.change('remove_break',shift_id=other,break_id=bid,expected_revision=actual_shift.shift_revision(self.db,other)))
    def test_protected_tables_and_shift_note_unchanged(self):
        self.db.execute('UPDATE shifts SET note=? WHERE id=?',(json.dumps({'estimate_basis':'office_photo'}),self.sid));before=self.snapshot();self.send(self.change());after=self.snapshot()
        for name in ('office_days','plan_days','rate_history','received_payments'):self.assertEqual(before[name],after[name])
        self.assertEqual(self.shift()['note'],before['shifts'][0][-1]);self.assertTrue(self.data()['office_estimated'])
    def test_audit_feed_includes_before_after(self):
        self.send(self.change());d=web_activity.page(self.db,{'view':'activity','day':'2024-07-10'});item=next(x for x in d['items'] if x['title']=='Перерывы исправлены');self.assertTrue(item['fields']);self.assertNotIn('before_state',json.dumps(d))
    def test_unknown_fields_and_invalid_types(self):
        for extra in [{'shift_id':True},{'break_id':True},{'expected_revision':'bad'},{'start':[]},{'end':'2024-07-10T19:15Z'},{'admin':True}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.preview(self.change(**extra))
    def test_apply_requires_outer_transaction(self):
        with self.assertRaises(RuntimeError):web_breaks.apply(self.db,self.change())
    def test_deleted_shift_fails(self):
        self.db.execute('DELETE FROM shifts WHERE id=?',(self.sid,))
        with self.assertRaises(ValueError):self.data()
    def test_more_than_ten_hours_rejected(self):
        sid=core.corrected_shift(self.db,'2024-07-08','08:00','23:00',30)
        with self.assertRaises(ValueError):self.send(self.change(shift_id=sid,start='2024-07-08T09:00',end='2024-07-08T19:01',expected_revision=actual_shift.shift_revision(self.db,sid)))
    def test_api_read_only_under_query_only(self):
        ro=miniapp_server.read_database(self.path)
        try:self.assertEqual(web_breaks.details(ro,{'view':'breaks','shift_id':self.sid})['shift_id'],self.sid);self.assertTrue(web_breaks.preview(ro,{'kind':'break_change','change':self.change()})['read_only'])
        finally:ro.close()


class BreakHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite3';db=core.connect(self.path);self.sid=core.corrected_shift(db,'2024-07-10','16:00','02:00',30);db.close()
        self.env=patch.dict('os.environ',{'MINIAPP_ENABLE_WRITES':'1','MINIAPP_PUBLIC_ORIGIN':'https://owner.invalid'});self.env.start()
        self.server=miniapp_server.MiniServer(('127.0.0.1',0),miniapp_server.MiniAppHandler,'fictional-token','77',db_path=self.path);self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.env.stop();self.temp.cleanup()
    def request(self,route,payload,token=True,origin='https://owner.invalid'):
        conn=HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=10);headers={'Content-Type':'application/json'}
        if token:headers['X-Telegram-Init-Data']=signed()
        if origin is not None:headers['Origin']=origin
        conn.request('POST','/mini/api/'+route,json.dumps(payload),headers);res=conn.getresponse();data=json.loads(res.read());status=res.status;conn.close();return status,data
    def test_anonymous_read_denied(self):self.assertEqual(self.request('workspace',{'view':'breaks','shift_id':self.sid},False)[0],403)
    def test_wrong_origin_denied(self):self.assertEqual(self.request('workspace',{'view':'breaks','shift_id':self.sid},origin='https://other.invalid')[0],403)
    def test_owner_details(self):
        code,data=self.request('workspace',{'view':'breaks','shift_id':self.sid});self.assertEqual(code,200);self.assertEqual(data['deducted_minutes'],30)
    def test_preview_and_action(self):
        code,d=self.request('workspace',{'view':'breaks','shift_id':self.sid});p={'action':'save_break','shift_id':self.sid,'break_id':None,'start':'2024-07-10T19:00','end':'2024-07-10T19:15','expected_revision':d['revision']}
        self.assertEqual(self.request('preview',{'kind':'break_change','change':p})[0],200);self.assertEqual(self.request('action',{**p,'nonce':'e'*32})[0],200)
    def test_missing_origin_write_denied(self):self.assertEqual(self.request('action',{'action':'save_break','nonce':'e'*32},origin=None)[0],403)
    def test_writes_flag_enforced(self):
        with patch.dict('os.environ',{'MINIAPP_ENABLE_WRITES':'0'}):self.assertEqual(self.request('action',{'action':'save_break','nonce':'e'*32})[0],403)
    def test_bad_view_fields(self):self.assertEqual(self.request('workspace',{'view':'breaks','shift_id':self.sid,'sql':'x'})[0],400)


if __name__=='__main__':unittest.main()
