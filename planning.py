"""Finite recurring schedules: preview, atomic apply, conflict-safe cancellation.

A series creates plans only. Existing plans and actual records are never replaced.
No timers, messages, payroll facts or external accounts are created here.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from core import TZ, now, iso, log, local_day, shifts
from workflows import plan_day, _validate_day, is_plan_cleared


def ensure(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS plan_series(
      id TEXT PRIMARY KEY, spec_json TEXT NOT NULL, created_utc TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','cancelled')),
      result_json TEXT NOT NULL, cancelled_utc TEXT
    );
    CREATE TABLE IF NOT EXISTS plan_series_days(
      series_id TEXT NOT NULL REFERENCES plan_series(id), day TEXT NOT NULL,
      applied_json TEXT NOT NULL, PRIMARY KEY(series_id,day)
    );
    """)


def _json(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _at(reference=None):
    value=reference or now()
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise ValueError('Нужно время с часовым поясом.')
    return value.astimezone(TZ)


def normalize(spec,reference=None):
    if not isinstance(spec,dict):raise ValueError('Неверный шаблон.')
    if set(spec)-{'start','end','pattern','weekdays','start_time','end_time'}:
        raise ValueError('Неизвестные параметры шаблона.')
    start=_validate_day(spec.get('start'));end=_validate_day(spec.get('end'))
    if start<_at(reference).date():raise ValueError('Начало серии не может быть в прошлом.')
    if end<start or (end-start).days>729:raise ValueError('Период: от 1 до 730 дней.')
    pattern=spec.get('pattern')
    if pattern not in ('2x2','3x3','5x2','weekly'):raise ValueError('Выбери шаблон графика.')
    days=spec.get('weekdays',[])
    if pattern=='weekly':
        if not isinstance(days,list) or not days or any(type(x) is not int or not 0<=x<=6 for x in days):
            raise ValueError('Выбери рабочие дни недели.')
        days=sorted(set(days))
    else:days=[]
    clocks=[]
    for key in ('start_time','end_time'):
        raw=spec.get(key,'')
        if not isinstance(raw,str) or not re.fullmatch(r'\d{2}:\d{2}',raw):raise ValueError('Время: ЧЧ:ММ.')
        try:datetime.strptime(raw,'%H:%M')
        except ValueError as exc:raise ValueError('Неверное время.') from exc
        clocks.append(raw)
    if clocks[0]==clocks[1]:raise ValueError('Начало и конец не должны совпадать.')
    return {'start':start.isoformat(),'end':end.isoformat(),'pattern':pattern,'weekdays':days,
            'start_time':clocks[0],'end_time':clocks[1]}


def _entries(spec):
    first=date.fromisoformat(spec['start']);last=date.fromisoformat(spec['end'])
    cycles={'2x2':(2,2),'3x3':(3,3),'5x2':(5,2)}
    for offset in range((last-first).days+1):
        day=first+timedelta(days=offset)
        if spec['pattern']=='weekly':working=day.weekday() in spec['weekdays']
        else:
            on,off=cycles[spec['pattern']];working=offset%(on+off)<on
        yield {'day':day.isoformat(),'status':'shift' if working else 'off',
               'start_time':spec['start_time'] if working else None,
               'end_time':spec['end_time'] if working else None}


def _future(entry,at):
    if entry['status']=='shift':
        return datetime.strptime(entry['day']+' '+entry['start_time'],'%Y-%m-%d %H:%M').replace(tzinfo=TZ)>at
    return date.fromisoformat(entry['day'])>at.date()


def preview(db,spec,reference=None):
    at=_at(reference);spec=normalize(spec,at)
    actual={local_day(r['start_utc']) for r in shifts(db)}
    created=[];skipped=[]
    for entry in _entries(spec):
        previous=plan_day(db,entry['day'])
        reason='есть фактическая смена' if entry['day'] in actual else 'план снят вручную' if is_plan_cleared(db,entry['day']) else 'есть план' if previous else 'день или смена уже начались' if not _future(entry,at) else None
        if reason:skipped.append({'day':entry['day'],'reason':reason,'existing':previous})
        else:created.append(entry)
    return {'spec':spec,'days':created,'skipped':skipped,
            'shift_count':sum(r['status']=='shift' for r in created),
            'off_count':sum(r['status']=='off' for r in created),
            'digest':_hash({'spec':spec,'created':created,'skipped':skipped})}


def apply(db,spec,nonce,expected_digest,reference=None,*,manage_transaction=True):
    if not isinstance(nonce,str) or not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('Неверный код подтверждения.')
    at=_at(reference);normalized=normalize(spec,at)
    if not manage_transaction and not db.in_transaction:raise RuntimeError('An outer transaction is required')
    if manage_transaction:db.execute('BEGIN IMMEDIATE')
    try:
        existing=db.execute('SELECT * FROM plan_series WHERE id=?',(nonce,)).fetchone()
        if existing:
            if existing['spec_json']!=_json(normalized):raise ValueError('Это подтверждение относится к другому плану.')
            result=json.loads(existing['result_json']);result['status']=existing['status']
            if manage_transaction:db.execute('COMMIT')
            return result
        candidate=preview(db,normalized,at)
        if candidate['digest']!=expected_digest:raise ValueError('План изменился после просмотра. Обнови предпросмотр.')
        if not candidate['days']:raise ValueError('Нет свободных будущих дат: существующие записи сохранены.')
        stamp=iso(at)
        result={'id':nonce,'created':len(candidate['days']),'shifts':candidate['shift_count'],
                'off':candidate['off_count'],'skipped':len(candidate['skipped']),'status':'active'}
        db.execute('INSERT INTO plan_series VALUES(?,?,?,?,?,NULL)',(nonce,_json(normalized),stamp,'active',_json(result)))
        for entry in candidate['days']:
            db.execute('INSERT INTO plan_days VALUES(?,?,?,?,?,?,?)',
                       (entry['day'],entry['status'],entry['start_time'],entry['end_time'],'Серия '+nonce[:8],stamp,stamp))
            saved=dict(db.execute('SELECT * FROM plan_days WHERE day=?',(entry['day'],)).fetchone())
            db.execute('INSERT INTO plan_series_days VALUES(?,?,?)',(nonce,entry['day'],_json(saved)))
        log(db,'plan_series_created',None,{'id':nonce,'spec':normalized,**result})
        if manage_transaction:db.execute('COMMIT')
        return result
    except Exception:
        if manage_transaction and db.in_transaction:db.execute('ROLLBACK')
        raise


def cancel_preview(db,series_id,reference=None):
    at=_at(reference)
    series=db.execute('SELECT * FROM plan_series WHERE id=?',(series_id,)).fetchone()
    if not series:raise ValueError('Серия не найдена.')
    if series['status']=='cancelled':return {'id':series_id,'days':[],'kept':[],'digest':_hash([]),'status':'cancelled'}
    actual={local_day(r['start_utc']) for r in shifts(db)}
    removed=[];kept=[]
    for row in db.execute('SELECT * FROM plan_series_days WHERE series_id=? ORDER BY day',(series_id,)):
        current=db.execute('SELECT * FROM plan_days WHERE day=?',(row['day'],)).fetchone()
        record=dict(current) if current else None
        changed=record!=json.loads(row['applied_json'])
        # Even an edit back to the same values belongs to the user, not to this series.
        edited=db.execute("SELECT 1 FROM events WHERE kind='plan_change' AND json_extract(details,'$.day')=? AND id>(SELECT id FROM events WHERE kind='plan_series_created' AND json_extract(details,'$.id')=? ORDER BY id DESC LIMIT 1) LIMIT 1",(row['day'],series_id)).fetchone()
        if changed or edited or row['day'] in actual or not record or not _future(record,at):
            kept.append({'day':row['day'],'current':record})
        else:removed.append(row['day'])
    return {'id':series_id,'days':removed,'kept':kept,'status':'active','digest':_hash({'days':removed,'kept':kept})}


def cancel(db,series_id,expected_digest,reference=None,*,manage_transaction=True):
    if not manage_transaction and not db.in_transaction:raise RuntimeError('An outer transaction is required')
    if manage_transaction:db.execute('BEGIN IMMEDIATE')
    try:
        candidate=cancel_preview(db,series_id,reference)
        if candidate['status']=='cancelled':
            if manage_transaction:db.execute('COMMIT')
            return {'removed':0,'already_cancelled':True}
        if candidate['digest']!=expected_digest:raise ValueError('Даты изменились. Сначала обнови предпросмотр отмены.')
        for day in candidate['days']:db.execute('DELETE FROM plan_days WHERE day=?',(day,))
        db.execute("UPDATE plan_series SET status='cancelled',cancelled_utc=? WHERE id=?",(iso(now()),series_id))
        result={'removed':len(candidate['days']),'preserved':len(candidate['kept'])}
        log(db,'plan_series_cancelled',None,{'id':series_id,'days':candidate['days'],**result})
        if manage_transaction:db.execute('COMMIT')
        return result
    except Exception:
        if manage_transaction and db.in_transaction:db.execute('ROLLBACK')
        raise


def series_list(db,limit=10):
    return [dict(r) for r in db.execute("SELECT * FROM plan_series ORDER BY created_utc DESC,id DESC LIMIT ?",(limit,))]