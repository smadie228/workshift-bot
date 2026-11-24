"""No-chat regression cases. Temporary databases; fictional values and signatures."""
import base64
import io
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
import core
import finance
import planning
import revisions
import web_workspace as workspace
import miniapp_actions
import test_miniapp


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'fixture.sqlite3';self.db=core.connect(self.path)
        self.month='2024-07'
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def action(self,action,**fields):return {'action':action,'nonce':secrets.token_hex(16),'expected_revision':workspace.revision(self.db),**fields}
    def payment(self,**kw):
        fields=dict(record_id=None,work_month=self.month,period=1,amount='1234.56',paid_on='2024-08-01',note='Вымышленная запись');fields.update(kw)
        return self.action('save_payment',**fields)
    def adjustment(self,**kw):
        fields=dict(record_id=None,day='2024-07-10',category='bonus',amount='125.50',note='');fields.update(kw)
        return self.action('save_adjustment',**fields)
    def execute(self,payload):return miniapp_actions.execute_action(self.db,77,payload)
    def dump(self):return '\n'.join(self.db.iterdump())
    def get_payment(self):return dict(self.db.execute('SELECT * FROM received_payments').fetchone())
    def test_read_workspace_is_readonly(self):
        before=self.dump();data=workspace.list_data(self.db,self.month);self.assertEqual(data['payments'],[]);self.assertEqual(self.dump(),before)
    def test_receipt_saved_with_exact_decimal(self):
        self.execute(self.payment(amount='1234,56'));self.assertEqual(self.get_payment()['amount_rub'],'1234.56');self.assertEqual(core.totals(self.db)['earnings'],0)
    def test_receipt_replay_is_exactly_once(self):
        p=self.payment();first=self.execute(p);self.assertEqual(first,self.execute(p));self.assertEqual(self.db.execute('SELECT count(*) FROM received_payments').fetchone()[0],1)
    def test_same_nonce_different_amount_rejected(self):
        p=self.payment();self.execute(p);p['amount']='2.00'
        with self.assertRaises(ValueError):self.execute(p)
        self.assertEqual(self.get_payment()['amount_rub'],'1234.56')
    def test_edit_retains_id_and_history(self):
        rid=self.execute(self.payment())['record_id'];self.execute(self.payment(record_id=rid,amount='2345.67',period=2));self.assertEqual(self.get_payment()['id'],rid)
        event=self.db.execute("SELECT details FROM events WHERE kind='payment_updated'").fetchone();self.assertEqual(json.loads(event[0])['old']['amount_rub'],'1234.56')
    def test_edit_cannot_overwrite_new_chat_payment(self):
        rid=self.execute(self.payment())['record_id'];old=self.payment(record_id=rid,amount='5.00');finance.add_payment(self.db,self.month,1,'100','2024-07-20');before=self.dump()
        with self.assertRaises(ValueError):self.execute(old)
        self.assertEqual(self.dump(),before)
    def test_edit_back_to_same_value_invalidates_old_form(self):
        rid=self.execute(self.payment())['record_id'];old=self.payment(record_id=rid,amount='999');self.execute(self.payment(record_id=rid,amount='2'));self.execute(self.payment(record_id=rid))
        with self.assertRaises(ValueError):self.execute(old)
    def test_payment_void_and_restore_preserve_full_record(self):
        rid=self.execute(self.payment())['record_id'];old=self.get_payment();self.execute(self.action('void_money',kind='payment',record_id=rid));self.assertEqual(finance.payment_totals(self.db,self.month)[1],0)
        eid=workspace.list_data(self.db,self.month)['archives'][0]['event_id'];self.execute(self.action('restore_money',event_id=eid));self.assertEqual(old,self.get_payment());self.assertEqual(workspace.list_data(self.db,self.month)['archives'],[])
    def test_restore_twice_does_not_duplicate(self):
        rid=self.execute(self.payment())['record_id'];self.execute(self.action('void_money',kind='payment',record_id=rid));eid=workspace.list_data(self.db,self.month)['archives'][0]['event_id'];self.execute(self.action('restore_money',event_id=eid))
        with self.assertRaises(ValueError):self.execute(self.action('restore_money',event_id=eid))
    def test_cannot_edit_missing_record(self):
        with self.assertRaises(ValueError):self.execute(self.payment(record_id=9999))
    def test_amount_types_and_precision_rejected(self):
        for amount in [0,True,1.2,None,'-1','0','NaN','Infinity','1e3','0.001','1000001','1,000.00']:
            with self.subTest(amount=amount),self.assertRaises(ValueError):self.execute(self.payment(amount=amount))
    def test_period_is_exact_integer(self):
        for period in [True,'1',1.0,0,3]:
            with self.subTest(period=period),self.assertRaises(ValueError):self.execute(self.payment(period=period))
    def test_future_payment_rejected(self):
        with self.assertRaises(ValueError):self.execute(self.payment(paid_on='2099-07-01'))
    def test_future_work_month_can_receive_advance_without_work_hours(self):
        self.execute(self.payment(work_month='2099-07'));self.assertEqual(self.get_payment()['work_month'],'2099-07');self.assertEqual(core.totals(self.db)['hours'],0)
    def test_invalid_date_and_note_rejected(self):
        for change in [{'paid_on':'2024-02-30'},{'note':'a\nb'},{'note':'x'*151},{'record_id':True},{'work_month':[]},{'unexpected':1}]:
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):self.execute(self.payment(**change))
    def test_adjustment_categories_are_separate(self):
        for category in workspace.CATEGORIES:self.execute(self.adjustment(category=category))
        data=workspace.list_data(self.db,self.month);self.assertEqual(len(data['adjustments']),4);self.assertEqual(data['payments'],[])
    def test_adjustment_edit_void_restore(self):
        rid=self.execute(self.adjustment())['record_id'];self.execute(self.adjustment(record_id=rid,category='deduction',amount='10'));old=dict(self.db.execute('SELECT * FROM pay_adjustments').fetchone())
        self.execute(self.action('void_money',kind='adjustment',record_id=rid));eid=workspace.list_data(self.db,self.month)['archives'][0]['event_id'];self.execute(self.action('restore_money',event_id=eid));self.assertEqual(old,dict(self.db.execute('SELECT * FROM pay_adjustments').fetchone()))
    def test_rate_preserves_old_earnings(self):
        core.corrected_shift(self.db,'2024-07-10','09:00','18:00',30);before=core.totals(self.db)['earnings'];self.execute(self.action('set_rate',effective_from='2099-07-01',amount='450'));self.assertEqual(core.totals(self.db)['earnings'],before);self.assertEqual(finance.rate_at(self.db,'2099-07-01'),450)
    def test_rate_today_or_past_rejected(self):
        for day in ['2024-07-01',core.now().astimezone(core.TZ).date().isoformat()]:
            with self.assertRaises(ValueError):self.execute(self.action('set_rate',effective_from=day,amount='400'))
    def test_future_rate_cancellation(self):
        self.execute(self.action('set_rate',effective_from='2099-07-01',amount='450'));self.execute(self.action('remove_future_rate',effective_from='2099-07-01'));self.assertEqual(finance.rate_at(self.db,'2099-07-02'),300)
    def test_break_setting_does_not_change_active_shift(self):
        core.toggle(self.db,1);before=dict(core.open_shift(self.db));self.execute(self.action('set_default_break',minutes=15));self.assertEqual(dict(core.open_shift(self.db)),before);self.assertEqual(core.default_break(self.db),15)
    def test_break_value_types(self):
        for minutes in [True,1.5,'30',-1,601]:
            with self.assertRaises(ValueError):self.execute(self.action('set_default_break',minutes=minutes))
    def test_new_money_events_have_no_shift_id(self):
        self.execute(self.payment());self.assertEqual(self.db.execute('SELECT count(*) FROM events WHERE shift_id IS NOT NULL').fetchone()[0],0)
    def test_old_chat_undo_cannot_delete_edited_payment(self):
        rid=self.execute(self.payment())['record_id'];eid=self.db.execute("SELECT id FROM events WHERE kind='payment_received'").fetchone()[0];self.execute(self.payment(record_id=rid))
        with self.assertRaises(ValueError):revisions.preview(self.db,eid)
    def test_journal_failure_rolls_back_business_and_receipt(self):
        p=self.payment()
        with patch('miniapp_actions.log',side_effect=ValueError('test_failure')):
            with self.assertRaises(ValueError):self.execute(p)
        self.assertEqual(self.db.execute('SELECT count(*) FROM received_payments').fetchone()[0],0);self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
    def test_outer_transaction_required(self):
        with self.assertRaises(RuntimeError):workspace.apply(self.db,self.payment(),'a'*32)
        with self.assertRaises(RuntimeError):planning.cancel(self.db,'a'*32,'b'*64,manage_transaction=False)
    def spec(self):return dict(start='2099-07-01',end='2099-07-14',pattern='2x2',weekdays=[],start_time='16:00',end_time='02:00')
    def test_series_create_preview_and_cancel(self):
        spec=self.spec();before=self.dump();preview=workspace.preview(self.db,{'kind':'series','spec':spec});self.assertEqual(before,self.dump())
        result=self.execute(self.action('create_series',spec=spec,expected_revision=preview['digest']));sid=result['series_id'];self.assertEqual(result['created'],14);self.assertEqual(core.totals(self.db)['hours'],0)
        preview=workspace.preview(self.db,{'kind':'cancel_series','series_id':sid});self.execute(self.action('cancel_series',series_id=sid,expected_revision=preview['digest']));self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],0)
    def test_series_rollback_is_atomic(self):
        spec=self.spec();preview=planning.preview(self.db,spec)
        with patch('miniapp_actions.log',side_effect=ValueError('fail')):
            with self.assertRaises(ValueError):self.execute(self.action('create_series',spec=spec,expected_revision=preview['digest']))
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_series').fetchone()[0],0);self.assertEqual(self.db.execute('SELECT count(*) FROM plan_days').fetchone()[0],0)
    def test_series_replay_after_date_passed(self):
        spec=self.spec();p=self.action('create_series',spec=spec,expected_revision=planning.preview(self.db,spec)['digest']);result=self.execute(p)
        with patch('planning.now',return_value=datetime(2100,1,1,tzinfo=core.TZ)):
            self.assertEqual(self.execute(p),result)
    def test_existing_plan_is_not_overwritten_by_series(self):
        import workflows
        workflows.set_plan(self.db,'2099-07-03','off');preview=planning.preview(self.db,self.spec());self.execute(self.action('create_series',spec=self.spec(),expected_revision=preview['digest']));self.assertEqual(workflows.plan_day(self.db,'2099-07-03')['status'],'off')
    def test_changed_series_preview_is_rejected(self):
        import workflows
        preview=planning.preview(self.db,self.spec());workflows.set_plan(self.db,'2099-07-03','off');before=self.dump()
        with self.assertRaises(ValueError):self.execute(self.action('create_series',spec=self.spec(),expected_revision=preview['digest']))
        # A rejected command may ensure the receipts table, but no business rows change.
        self.assertEqual(self.db.execute('SELECT count(*) FROM plan_series').fetchone()[0],0)
    def test_concurrent_same_nonce_creates_once(self):
        payload=self.payment();barrier=Barrier(2)
        def run(_):
            db=core.connect(self.path)
            try:barrier.wait(5);return miniapp_actions.execute_action(db,77,payload)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.db.execute('SELECT count(*) FROM received_payments').fetchone()[0],1)
    def test_protected_tables_remain_identical(self):
        tables=('shifts','plan_days','plan_clears','actual_days_off','office_days','history_source_rows','rate_history')
        before={t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in tables};self.execute(self.payment());self.execute(self.adjustment())
        self.assertEqual(before,{t:[tuple(r) for r in self.db.execute('SELECT * FROM '+t)] for t in tables})

    def test_payment_history_is_readonly_sorted_and_limited(self):
        for paid_on,amount in [('2024-08-01','100.10'),('2024-08-03','300.30'),('2024-08-02','200.20')]:
            self.execute(self.payment(paid_on=paid_on,amount=amount))
        before=self.dump()
        data=workspace.payment_history(self.db,2)
        self.assertEqual([r['paid_on'] for r in data['payments']],['2024-08-03','2024-08-02'])
        self.assertTrue(data['has_more']);self.assertEqual(data['limit'],2)
        self.assertEqual(data['payments'][0]['amount_rub'],'300.30')
        self.assertEqual(self.dump(),before)
        for value in [0,121,True,'10',None]:
            with self.subTest(limit=value),self.assertRaises(ValueError):workspace.payment_history(self.db,value)


class WorkspaceHttpTests(unittest.TestCase):
    setUp=test_miniapp.MiniAppTests.setUp;tearDown=test_miniapp.MiniAppTests.tearDown;request=test_miniapp.MiniAppTests.request
    def test_workspace_and_preview_require_owner(self):
        for route,body in [('workspace',{'month':'2024-07'}),('preview',{'kind':'series','spec':{}}),('report',{'month':'2024-07','format':'pdf'})]:
            self.assertEqual(self.request('/mini/api/'+route,body)[0],403)
    def test_workspace_read_and_unknown_fields(self):
        self.assertEqual(self.request('/mini/api/workspace',{'month':'2024-07'},test_miniapp.signed())[0],200)
        self.assertEqual(self.request('/mini/api/workspace',{'month':'2024-07','token':'x'},test_miniapp.signed())[0],400)
    def test_payment_http_disabled_by_default(self):
        self.assertEqual(self.request('/mini/api/action',{'action':'save_payment','nonce':'a'*32},test_miniapp.signed())[0],403)

    def test_payment_history_is_owner_only_and_readonly(self):
        self.assertEqual(self.request('/mini/api/workspace',{'view':'payment_history','limit':60})[0],403)
        code,data,_=self.request('/mini/api/workspace',{'view':'payment_history','limit':60},test_miniapp.signed())
        self.assertEqual(code,200);self.assertEqual(data['payments'],[]);self.assertFalse(data['has_more'])
        self.assertEqual(self.request('/mini/api/workspace',{'view':'payment_history','limit':0},test_miniapp.signed())[0],400)
        self.assertEqual(self.request('/mini/api/workspace',{'view':'payment_history','limit':60,'month':'2024-07'},test_miniapp.signed())[0],400)
    def test_readonly_report_is_private_and_valid(self):
        from pypdf import PdfReader
        for kind in ['xlsx','pdf','annual_pdf']:
            code,data,headers=self.request('/mini/api/report',{'month':'2024-07','format':kind},test_miniapp.signed());self.assertEqual(code,200);raw=base64.b64decode(data['base64']);self.assertEqual(len(raw),data['bytes']);self.assertIn('no-store',headers['Cache-Control'])
            if kind=='xlsx':self.assertIn('xl/workbook.xml',zipfile.ZipFile(io.BytesIO(raw)).namelist())
            else:self.assertGreaterEqual(len(PdfReader(io.BytesIO(raw)).pages),1)
    def test_report_path_and_unknown_kind_denied(self):
        for body in [{'month':'../../private','format':'xlsx'},{'month':'2024-07','format':'sqlite3'},{'month':'2024-07','format':'xlsx','path':'/etc/passwd'}]:
            self.assertEqual(self.request('/mini/api/report',body,test_miniapp.signed())[0],400)




if __name__=='__main__':unittest.main()
