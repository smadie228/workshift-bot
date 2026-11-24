"""Explicit, compare-and-swap undo with an immutable copy of the excluded record.

No automatic reversal. Every operation needs a fresh preview and confirmation.
Original events, Telegram deduplication keys and reversal evidence are retained.
"""
import hashlib
import json
from datetime import datetime,timedelta
from core import now,iso,log,open_shift,TZ

LABELS={'in':'Приход','out':'Уход','manual':'Ручная смена','fix':'Исправление смены',
        'late_close':'Уточнение ухода','break_start':'Начало перерыва','break_end':'Конец перерыва',
        'plan_change':'Изменение плана','payment_received':'Поступление','adjustment_added':'Доплата / удержание',
        'plan_series_created':'Создание графика','plan_series_cancelled':'Отмена графика','operation_reversed':'Отмена операции','actual_day_off':'\u0424\u0430\u043a\u0442: \u0432\u044b\u0445\u043e\u0434\u043d\u043e\u0439','actual_day_off_restored':'\u0412\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u0440\u0430\u0431\u043e\u0442\u044b','actual_day_off_cleared':'\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u043a \u0440\u0430\u0431\u043e\u0442\u0435','actual_shift_restored':'\u0421\u043c\u0435\u043d\u0430 \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0430'}
SUPPORTED={'in','out','manual','fix','payment_received','adjustment_added'}


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS operation_reversals(
        event_id INTEGER PRIMARY KEY REFERENCES events(id),
        reverted_utc TEXT NOT NULL, kind TEXT NOT NULL,
        original_json TEXT NOT NULL, reason TEXT NOT NULL)''')


def _json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def preview(db,event_id):
    if type(event_id) is not int or event_id<=0:raise ValueError('Неверная операция.')
    event=db.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
    if not event:raise ValueError('Операция не найдена.')
    if db.execute('SELECT 1 FROM operation_reversals WHERE event_id=?',(event_id,)).fetchone():
        raise ValueError('Эта операция уже отменена.')
    kind=event['kind'];details=json.loads(event['details'])
    if kind not in SUPPORTED:raise ValueError('Для этой операции используй исправление времени или управление серией.')
    previous=None
    if kind in ('in','out','manual','fix'):
        table='shifts';key=event['shift_id']
        raw=db.execute('SELECT * FROM shifts WHERE id=?',(key,)).fetchone()
        if not raw:raise ValueError('Смена уже исключена из учёта.')
        row=dict(raw)
        newer=db.execute("SELECT id FROM events WHERE shift_id=? AND id>? AND kind!='miniapp_operation' LIMIT 1",(key,event_id)).fetchone()
        if newer:raise ValueError('После этой операции смена уже менялась. Используй исправление с подтверждением.')
        if kind in ('in','manual'):
            if db.execute('SELECT 1 FROM break_sessions WHERE shift_id=?',(key,)).fetchone() or db.execute('SELECT 1 FROM tracked_break_shifts WHERE shift_id=?',(key,)).fetchone():
                raise ValueError('У смены есть перерывы. Используй исправление, чтобы не потерять связанные события.')
            if kind=='in' and row['end_utc'] is not None:raise ValueError('Приход уже связан с уходом.')
            if kind=='manual' and row['origin']!='manual':raise ValueError('Происхождение смены изменилось.')
            effect='Исключить эту смену из часов и начислений. Её полная копия останется в журнале отмен.'
        elif kind=='out':
            if not row['end_utc'] or row['end_utc']!=details.get('at'):raise ValueError('Время ухода уже отличается от этой операции.')
            if open_shift(db):raise ValueError('Другая смена уже открыта. Сначала уточни её состояние.')
            if now()-datetime.fromisoformat(row['start_utc'])>timedelta(hours=24):raise ValueError('Слишком старая смена для повторного открытия. Используй исправление.')
            effect='Убрать ошибочный уход и снова открыть смену. Начисление появится после правильного ухода.'
        else:
            previous=details.get('old')
            required={'id','start_utc','end_utc','break_minutes','origin','created_utc','updated_utc','note'}
            if not isinstance(previous,dict) or set(previous)!=required or previous['id']!=key or not previous['end_utc']:
                raise ValueError('Нет полного предыдущего состояния для безопасной отмены.')
            overlap=db.execute('SELECT id FROM shifts WHERE id!=? AND start_utc<? AND (end_utc IS NULL OR end_utc>?) LIMIT 1',(key,previous['end_utc'],previous['start_utc'])).fetchone()
            if overlap:raise ValueError('Прежнее время пересекается с другой сменой. Автоматическая отмена запрещена.')
            effect='Восстановить предыдущие время и перерыв этой смены. Обе версии останутся в истории.'
        stamp=datetime.fromisoformat(row['start_utc']).astimezone(TZ).strftime('%d.%m.%Y %H:%M')
        description=LABELS[kind]+' · '+stamp
    else:
        table='received_payments' if kind=='payment_received' else 'pay_adjustments'
        key=details.get('id')
        money_kind='payment' if kind=='payment_received' else 'adjustment'
        newer=db.execute("SELECT 1 FROM events WHERE id>? AND json_extract(details,'$.id')=? AND (kind=? OR (kind IN ('money_voided','money_restored') AND json_extract(details,'$.kind')=?)) LIMIT 1",(event_id,key,money_kind+'_updated',money_kind)).fetchone()
        if newer:raise ValueError('После этой операции запись менялась. Открой её в разделе денег и проверь актуальные значения.')
        raw=db.execute('SELECT * FROM '+table+' WHERE id=?',(key,)).fetchone()
        if not raw:raise ValueError('Запись уже исключена из учёта.')
        row=dict(raw)
        if str(row['amount_rub'])!=str(details.get('amount')):raise ValueError('Сумма записи изменилась: безопасная отмена невозможна.')
        if kind=='payment_received':
            if row['work_month']!=details.get('work_month') or row['paid_on']!=details.get('paid_on'):raise ValueError('Поступление уже изменено.')
            effect='Исключить ошибочную запись о поступлении. Это не возврат денег и не операция с банковским счётом.'
        else:
            if row['day']!=details.get('day') or row['category']!=details.get('category'):raise ValueError('Корректировка уже изменена.')
            effect='Исключить ошибочную корректировку из расчёта. Исходная сумма останется в журнале отмен.'
        description=LABELS[kind]+' · '+row['amount_rub']+' ₽'
    material={'event':dict(event),'table':table,'record':row,'previous':previous}
    return {'id':event_id,'kind':kind,'description':description,'effect':effect,'material':material,
            'digest':hashlib.sha256(_json(material).encode()).hexdigest()}


def reverse(db,event_id,expected_digest,reason='Ошибка ввода, подтверждено владельцем'):
    if not isinstance(reason,str) or not 1<=len(reason)<=160:raise ValueError('Нужно короткое основание отмены.')
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('SELECT 1 FROM operation_reversals WHERE event_id=?',(event_id,)).fetchone():
            db.execute('COMMIT');return {'event_id':event_id,'already_reversed':True}
        item=preview(db,event_id)
        if item['digest']!=expected_digest:raise ValueError('Запись изменилась после просмотра. Открой подтверждение ещё раз.')
        material=item['material'];row=material['record'];key=row['id'];kind=item['kind']
        db.execute('INSERT INTO operation_reversals VALUES(?,?,?,?,?)',(event_id,iso(now()),kind,_json(material),reason))
        if kind=='out':
            db.execute('UPDATE shifts SET end_utc=NULL,updated_utc=? WHERE id=?',(iso(now()),key))
        elif kind=='fix':
            old=material['previous'];columns=[k for k in old if k!='id']
            db.execute('UPDATE shifts SET '+','.join(k+'=?' for k in columns)+' WHERE id=?',[old[k] for k in columns]+[key])
        else:db.execute('DELETE FROM '+material['table']+' WHERE id=?',(key,))
        log(db,'operation_reversed',key if material['table']=='shifts' else None,
            {'event_id':event_id,'kind':kind,'reason':reason,'archive':'operation_reversals'})
        db.execute('COMMIT');return {'event_id':event_id,'already_reversed':False}
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise


def history(db,offset=0,limit=8):
    if type(offset) is not int or offset<0 or offset>100000:raise ValueError('Неверная страница.')
    result=[]
    rows=db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()
    for r in rows:
        reversed_already=bool(db.execute('SELECT 1 FROM operation_reversals WHERE event_id=?',(r['id'],)).fetchone())
        eligible=False
        if r['kind'] in SUPPORTED and not reversed_already:
            try:preview(db,r['id']);eligible=True
            except (ValueError,TypeError,KeyError):pass
        result.append({'id':r['id'],'at':r['event_utc'],'label':LABELS.get(r['kind'],'Служебное событие'),
                       'shift_id':r['shift_id'],'reversed':reversed_already,'undo_available':eligible})
    return result