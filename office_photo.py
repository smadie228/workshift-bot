"""Provisional office-photo durations and explicitly confirmed estimated bounds.

Photo readings stay in office_image_days, NOT in confirmed office_days.
An arrival anchor is an assumption. Hundredths of an hour are 36 seconds.
"""
import json
from datetime import timedelta
from decimal import Decimal
import core
import actual_shift
from workflows import _validate_day, plan_day
from actual_day import clear_for_work

STATUS = 'photo_visual_provisional_decimal_hours'
ACTIONS = ('apply_office_estimate',)


def normalize(payload):
    if not isinstance(payload, dict) or set(payload)-{'action','nonce','day','expected_revision'}:
        raise ValueError('unknown_fields')
    if payload.get('action') not in ACTIONS: raise ValueError('unknown_action')
    day=payload.get('day'); _validate_day(day)
    revision=payload.get('expected_revision')
    import re
    if not isinstance(revision,str) or not re.fullmatch('[a-f0-9]{64}',revision):
        raise ValueError('Обнови расчёт по фото.')
    return {'action':'apply_office_estimate','day':day,'expected_revision':revision}


def proposal(db, day):
    raw=db.execute('SELECT * FROM office_image_days WHERE day=? AND office_hours IS NOT NULL AND parse_status=?',(day,STATUS)).fetchone()
    if raw is None:return None
    reference=dict(raw);hours=Decimal(str(raw['office_hours']))
    if not hours.is_finite() or not Decimal('0')<hours<Decimal('24'):return None
    records=[dict(row) for row in core.shifts(db,day[:7]) if core.local_day(row['start_utc'])==day]
    blocked='Есть подтверждённый офисный табель. Не заменяй его предварительным чтением фотографии.' if db.execute('SELECT 1 FROM office_days WHERE day=?',(day,)).fetchone() else None
    if len(records)>1:blocked='За день несколько смен: нужна ручная сверка.'
    if any(r['end_utc'] is None for r in records):blocked='Сначала закрой или исправь открытую смену.'
    if db.execute('SELECT 1 FROM actual_days_off WHERE day=?',(day,)).fetchone():blocked='День явно отмечен фактическим выходным.'
    row=records[0] if len(records)==1 else None
    plan=plan_day(db,day)
    anchor=core.read_time(row['start_utc']) if row else core.parse_local_datetime(day,plan['start'] if plan and plan.get('start') else '16:00').astimezone(core.TZ)
    minutes=row['break_minutes'] if row else core.default_break(db)
    seconds=int(hours*3600)
    end=anchor+timedelta(seconds=seconds,minutes=minutes)
    if seconds+minutes*60>86400:blocked='Расчётная смена превышает сутки.'
    if end>core.now():blocked='Расчётный уход ещё не наступил.'
    sid=row['id'] if row else None
    if sid and db.execute('SELECT 1 FROM break_sessions WHERE shift_id=?',(sid,)).fetchone():blocked='Есть отмеченные перерывы: используй ручное исправление.'
    overlap=db.execute('SELECT id FROM shifts WHERE (? IS NULL OR id!=?) AND start_utc<? AND (end_utc IS NULL OR end_utc>?) LIMIT 1',(sid,sid,core.iso(end),core.iso(anchor))).fetchone()
    if overlap:blocked='Расчётный интервал пересекает другую смену.'
    material={'reference':reference,'actual_revision':actual_shift.day_revision(db,day),'anchor':core.iso(anchor),'break_minutes':minutes,'plan':plan}
    return {'day':day,'hours_decimal':str(hours),'paid_seconds':seconds,'start':anchor.isoformat(),'end':end.isoformat(),
            'break_minutes':minutes,'shift_id':sid,'anchor_source':'existing_record' if row else 'plan' if plan and plan.get('start') else 'assumed_16_00',
            'estimated':True,'photo_confirmed':False,'blocked':blocked,'revision':actual_shift._digest(material)}


def month_proposals(db,month):
    rows=db.execute('SELECT day FROM office_image_days WHERE day LIKE ? AND office_hours IS NOT NULL AND parse_status=? ORDER BY day',(month+'-%',STATUS)).fetchall()
    return {r['day']:proposal(db,r['day']) for r in rows}


def apply(db,payload):
    if not db.in_transaction:raise RuntimeError('An outer write transaction is required')
    value=normalize(payload);item=proposal(db,value['day'])
    if item is None or item['revision']!=value['expected_revision']:raise ValueError('Данные или расчёт по фото изменились. Обнови день.')
    if item['blocked']:raise ValueError(item['blocked'])
    sid=item['shift_id'];old=dict(db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone()) if sid else None
    stamp=core.iso(core.now())
    marker={'estimate_basis':'office_photo','photo_confirmed':False,'arrival_is_assumed':True,'office_hours':item['hours_decimal'],
            'anchor_source':item['anchor_source'],'previous_note':old['note'] if old else ''}
    from datetime import datetime
    start=core.iso(datetime.fromisoformat(item['start']));end=core.iso(datetime.fromisoformat(item['end']))
    if sid:
        db.execute('UPDATE shifts SET start_utc=?,end_utc=?,break_minutes=?,note=?,updated_utc=? WHERE id=?',
                   (start,end,item['break_minutes'],json.dumps(marker,ensure_ascii=False),stamp,sid))
    else:
        clear_for_work(db,value['day'])
        sid=db.execute('INSERT INTO shifts(start_utc,end_utc,break_minutes,origin,created_utc,updated_utc,note) VALUES(?,?,?,?,?,?,?)',
                       (start,end,item['break_minutes'],'office_estimate',stamp,stamp,json.dumps(marker,ensure_ascii=False))).lastrowid
    core.log(db,'fix' if old else 'manual',sid,{'old':old,'day':value['day'],'source':'office_photo_estimate','estimate':marker,
               'start':item['start'],'end':item['end'],'break':item['break_minutes']})
    return {'success':True,'event':'apply_office_estimate','id':sid,'day':value['day'],'estimated':True,'hours':float(item['hours_decimal'])}
