"""Fictional regression cases for the batched, read-only version projection."""
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest
import core
import actual_shift
import miniapp_actions
import miniapp_server
import workflows


class BatchedVersionTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.path=Path(self.folder.name)/'fictional.sqlite3'
        self.db=core.connect(self.path)
    def tearDown(self):
        self.db.close();self.folder.cleanup()
    def same(self,month):
        first=datetime.strptime(month,'%Y-%m');before='\n'.join(self.db.iterdump())
        grouped=actual_shift.month_versions(self.db,month)
        self.assertEqual(grouped['days'],{f'{month}-{n:02d}':actual_shift.day_revision(self.db,f'{month}-{n:02d}') for n in range(1,monthrange(first.year,first.month)[1]+1)})
        for row in core.shifts(self.db,month):
            self.assertEqual(grouped['shifts'][row['id']],actual_shift.shift_revision(self.db,row['id']))
            self.assertEqual(grouped['tracked'][row['id']],bool(self.db.execute('SELECT 1 FROM break_sessions WHERE shift_id=?',(row['id'],)).fetchone()))
        self.assertEqual(before,'\n'.join(self.db.iterdump()))
        return grouped
    def test_empty_months_leap_and_year_boundary(self):
        for month in ['2024-02','2024-07','2024-12','2025-01']:
            with self.subTest(month=month):self.same(month)
    def test_closed_open_and_multiple_records(self):
        sid=core.corrected_shift(self.db,'2024-07-10','09:00','13:00',15)
        stamp='2024-07-10T12:00:00+00:00'
        self.db.execute("INSERT INTO shifts(start_utc,end_utc,break_minutes,created_utc,updated_utc) VALUES(?,?,0,?,?)",(stamp,'2024-07-10T15:00:00+00:00',stamp,stamp))
        core.toggle(self.db,99,datetime(2024,7,12,9,tzinfo=core.TZ))
        self.assertEqual(len(self.same('2024-07')['shifts']),3)
    def test_moscow_month_not_utc_month(self):
        core.corrected_shift(self.db,'2024-08-01','00:15','08:30',15)
        self.assertEqual(len(self.same('2024-07')['shifts']),0)
        self.assertEqual(len(self.same('2024-08')['shifts']),1)
    def test_break_evidence_and_generation(self):
        at=datetime(2024,7,10,9,tzinfo=core.TZ)
        core.toggle(self.db,1,at)
        with patch('workflows.now',return_value=at+timedelta(hours=1)):workflows.start_break(self.db)
        self.same('2024-07')
        with patch('workflows.now',return_value=at+timedelta(hours=1,minutes=15)):workflows.stop_break(self.db)
        core.toggle(self.db,2,at+timedelta(hours=8));self.same('2024-07')
    def test_off_archive_and_restore(self):
        core.corrected_shift(self.db,'2024-07-10','09:00','18:00',30)
        self.same('2024-07')
        value={'action':'mark_day_off','nonce':'a'*32,'day':'2024-07-10','expected_revision':actual_shift.day_revision(self.db,'2024-07-10'),'replace_shifts':True}
        miniapp_actions.execute_action(self.db,77,value);self.same('2024-07')
        value.update(action='restore_day_off',nonce='b'*32,expected_revision=actual_shift.day_revision(self.db,'2024-07-10'),replace_shifts=False)
        miniapp_actions.execute_action(self.db,77,value);self.same('2024-07')
    def test_unrelated_generation_still_invalidates(self):
        before=self.same('2024-07')
        core.corrected_shift(self.db,'2024-06-10','09:00','18:00',30)
        after=self.same('2024-07');self.assertNotEqual(before['days'],after['days'])
    def test_query_count_is_constant(self):
        for day in range(1,20):core.corrected_shift(self.db,f'2024-07-{day:02d}','09:00','18:00',30)
        counts=[];self.db.set_trace_callback(counts.append)
        grouped=actual_shift.month_versions(self.db,'2024-07');new_count=len(counts)
        counts.clear()
        for day in grouped['days']:actual_shift.day_revision(self.db,day)
        for sid in grouped['shifts']:actual_shift.shift_revision(self.db,sid)
        old_count=len(counts);self.db.set_trace_callback(None)
        self.assertLessEqual(new_count,6);self.assertGreater(old_count,new_count*10)
        print('VERSION_QUERY_COUNTS',json.dumps({'legacy':old_count,'batched':new_count,'fixture_shifts':19}))
    def test_query_only_snapshot_and_write_tokens_compatible(self):
        sid=core.corrected_shift(self.db,'2024-07-10','09:00','18:00',30)
        ro=miniapp_server.read_database(self.path)
        try:
            data=actual_shift.month_versions(ro,'2024-07')
            self.assertEqual(ro.execute('PRAGMA query_only').fetchone()[0],1)
        finally:ro.close()
        request={'action':'correct_shift','nonce':'e'*32,'shift_id':sid,'day':'2024-07-10','start':'09:00','end':'18:30','break_minutes':30,'expected_revision':data['shifts'][sid]}
        self.assertTrue(miniapp_actions.execute_action(self.db,77,request)['success'])
        self.same('2024-07')
    def test_read_transaction_is_consistent_across_concurrent_writer(self):
        sid=core.corrected_shift(self.db,'2024-07-10','09:00','18:00',30)
        ro=miniapp_server.read_database(self.path)
        try:
            first=actual_shift.month_versions(ro,'2024-07')
            core.corrected_shift(self.db,'2024-07-10','09:00','18:30',30,sid)
            self.assertEqual(actual_shift.month_versions(ro,'2024-07'),first)
        finally:ro.close()
        self.assertNotEqual(actual_shift.month_versions(self.db,'2024-07'),first)

if __name__=='__main__':unittest.main()
