"""Fictional-only read-only feed tests: paging, validation, history and privacy."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import core
import web_activity as activity
import miniapp_server
import test_miniapp as helpers


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'fixture.sqlite3';self.db=core.connect(self.path)
    def tearDown(self):
        self.db.close();self.folder.cleanup()
    def add(self,kind='plan_change',data=None,sid=None,at='2024-07-10T15:00:00+00:00'):
        return self.db.execute('INSERT INTO events(event_utc,kind,shift_id,details) VALUES(?,?,?,?)',(at,kind,sid,json.dumps(data or {'day':'2024-07-10','after':{'status':'off'}}))).lastrowid
    def page(self,**kwargs):return activity.page(self.db,{'view':'activity',**kwargs})
    def test_read_only_unchanged_database(self):
        self.add();before=list(self.db.iterdump());ro=miniapp_server.read_database(self.path)
        try:self.assertEqual(len(activity.page(ro,{'view':'activity'})['items']),1)
        finally:ro.close()
        self.assertEqual(before,list(self.db.iterdump()))
    def test_strict_request_fields(self):
        for body in [None,[],{}, {'view':'other'},{'view':'activity','month':'2024-07'},{'view':'activity','limit':999}]:
            with self.subTest(body=body),self.assertRaises(ValueError):activity.page(self.db,body)
    def test_cursor_types_and_bounds(self):
        for value in [True,False,0,-1,1.5,'5',[],2**53]:
            with self.subTest(value=value),self.assertRaises(ValueError):self.page(before_id=value)
    def test_day_validation(self):
        for day in ['2024-02-30','bad',1,True,[],{}]:
            with self.subTest(day=day),self.assertRaises(ValueError):self.page(day=day)
    def test_category_validation(self):
        for category in ['other',True,[],{},None]:
            with self.subTest(category=category),self.assertRaises(ValueError):self.page(category=category)
    def test_stable_pagination_and_no_repeats(self):
        ids=[self.add() for _ in range(77)];seen=[];cursor=None
        while True:
            result=self.page(**({'before_id':cursor} if cursor else {}));seen.extend(x['id'] for x in result['items']);self.assertLessEqual(len(result['items']),30)
            if not result['has_more']:break
            cursor=result['next_before_id']
        self.assertEqual(seen,list(reversed(ids)))
    def test_new_event_does_not_shift_old_pages(self):
        for _ in range(35):self.add()
        first=self.page();self.add();second=self.page(before_id=first['next_before_id'])
        self.assertEqual([r['id'] for r in second['items']],[5,4,3,2,1])
    def test_sort_by_id_even_with_older_timestamps(self):
        a=self.add(at='2024-07-12T15:00:00+00:00');b=self.add(at='2024-07-01T15:00:00+00:00')
        self.assertEqual([r['id'] for r in self.page()['items']],[b,a])
    def test_bounded_scan_empty_page_keeps_cursor(self):
        target=self.add(data={'day':'2024-01-01'})
        for _ in range(activity.SCAN_LIMIT+1):self.add()
        first=self.page(day='2024-01-01');self.assertEqual(first['items'],[]);self.assertTrue(first['has_more']);self.assertEqual(first['scanned'],activity.SCAN_LIMIT)
        second=self.page(day='2024-01-01',before_id=first['next_before_id']);self.assertEqual(second['items'][0]['id'],target)
    def test_source_payload_not_exposed(self):
        self.add('fix',{'day':'2024-07-10','reason':'TOP_SECRET_REASON','token':'TOP_SECRET_TOKEN','nonce':'TOP_SECRET_NONCE','source':'/private/TOP_SECRET_PATH','old':{'start_utc':'2024-07-10T06:00:00+00:00','end_utc':'2024-07-10T15:00:00+00:00','break_minutes':30,'note':'TOP_SECRET_NOTE'},'start':'09:20','end':'18:10'})
        encoded=json.dumps(self.page());self.assertNotIn('TOP_SECRET',encoded);self.assertIn('09:20',encoded)
    def test_technical_receipts_not_shown(self):
        self.add('miniapp_operation',{'action':'in','nonce':'never-expose'});self.add('backup',{'path':'secret'});self.assertEqual(self.page()['items'],[])
    def test_plan_before_after(self):
        self.add(data={'day':'2024-07-10','before':{'status':'off'},'after':{'status':'shift','start':'16:00','end':'02:00'}})
        row=self.page()['items'][0];self.assertEqual(row['fields'],[{'label':'Было','value':'Выходной'},{'label':'Стало','value':'16:00 — 02:00'}])
    def test_night_time_uses_moscow_day(self):
        self.add('in',{'at':'2024-07-10T21:15:00+00:00','mode':'chosen'})
        row=self.page(day='2024-07-11')['items'][0];self.assertEqual(row['day'],'2024-07-11');self.assertTrue(row['manually_timed'])
    def test_moved_start_matches_both_days(self):
        self.add('open_start_corrected',{'old':{'start_utc':'2024-07-10T20:00:00+00:00'},'at':'2024-07-10T22:00:00+00:00'})
        self.assertEqual(len(self.page(day='2024-07-10')['items']),1);self.assertEqual(len(self.page(day='2024-07-11')['items']),1)
    def test_deleted_shift_anchor_preserved(self):
        sid=core.corrected_shift(self.db,'2024-07-10','16:00','02:00',30)
        self.add('break_end',{'minutes':20},sid)
        row=dict(self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone())
        self.add('shift_deleted',{'day':'2024-07-10','archive':{'shifts':[row]}},sid);self.db.execute('DELETE FROM shifts WHERE id=?',(sid,))
        result=self.page(day='2024-07-10');self.assertTrue(any(x['title']=='Перерыв завершён' for x in result['items']))
    def test_malformed_json_and_long_payload_fail_closed(self):
        for raw in ['{broken','[]','null','{"secret":"'+('x'*140000)+'"}']:
            self.db.execute('INSERT INTO events(event_utc,kind,details) VALUES(?,?,?)',('invalid','fix',raw))
        rows=self.page()['items'];self.assertEqual(len(rows),4);self.assertTrue(all(x['fields']==[] and x['at'] is None for x in rows))
    def test_finance_category_separate_from_work(self):
        self.add();self.add('payment_updated',{'paid_on':'2024-07-10','old':{'amount_rub':'10.00'},'after':{'amount_rub':'15.50'}})
        rows=self.page(category='money')['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['fields'][1]['value'],'15,50 ₽')
    def test_office_estimate_label(self):
        self.add('office_transcription_shift_adjusted',{'day':'2024-07-10'})
        self.assertTrue(self.page()['items'][0]['estimated'])
    def test_series_day_membership_not_just_range(self):
        sid='a'*32
        self.db.execute('INSERT INTO plan_series VALUES(?,?,?,?,?,NULL)',(sid,'{}','2024-07-10','active','{}'))
        self.db.execute('INSERT INTO plan_series_days VALUES(?,?,?)',(sid,'2024-07-11','{}'))
        self.add('plan_series_created',{'id':sid,'created':1})
        self.assertEqual(len(self.page(day='2024-07-11')['items']),1);self.assertEqual(self.page(day='2024-07-12')['items'],[])


class ActivityHTTPTests(unittest.TestCase):
    setUp=helpers.MiniAppTests.setUp;tearDown=helpers.MiniAppTests.tearDown;request=helpers.MiniAppTests.request
    def test_anonymous_history_denied(self):
        self.assertEqual(self.request('/mini/api/workspace',{'view':'activity'})[0],403)
    def test_owner_read_with_writes_disabled(self):
        code,data,hdr=self.request('/mini/api/workspace',{'view':'activity'},helpers.signed())
        self.assertEqual(code,200);self.assertTrue(data['read_only']);self.assertEqual(data['items'],[]);self.assertIn('no-store',hdr['Cache-Control'])
    def test_owner_cannot_query_arbitrary_columns(self):
        self.assertEqual(self.request('/mini/api/workspace',{'view':'activity','sql':'SELECT * FROM config'},helpers.signed())[0],400)
    def test_wrong_owner_history_denied(self):
        self.assertEqual(self.request('/mini/api/workspace',{'view':'activity'},helpers.signed(user_id=78))[0],403)

if __name__=='__main__':unittest.main()
