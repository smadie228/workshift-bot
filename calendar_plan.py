"""Calendar plan drafts: read-only deep link, then owner-confirmed chat write.

A link is untrusted input, never permission to write. The caller authorizes the
owner and presents a value-bound confirmation. No time, money or office rows change.
"""
import hashlib,json,re
from datetime import datetime
from core import TZ
import workflows

PAYLOAD=re.compile(r'p_(\d{8})_([son])_(\d{4})_(\d{4})_([a-f0-9]{32})')

def validate(day,status,start=None,end=None,*,allow_past=False):
    target=workflows._validate_day(day)
    if target<datetime.now(TZ).date() and allow_past is not True:
        raise ValueError('Прошедший план не меняется. Для фактического времени используй исправление смены.')
    if status not in ('shift','off','unset'):raise ValueError('Выбери рабочий, выходной или не задан.')
    if status in ('off','unset'):
        if start is not None or end is not None:raise ValueError('У выходного нет времени смены.')
    else:
        for value in (start,end):
            if not isinstance(value,str) or not re.fullmatch(r'\d{2}:\d{2}',value):raise ValueError('Время должно быть ЧЧ:ММ.')
            try:datetime.strptime(value,'%H:%M')
            except ValueError as exc:raise ValueError('Проверь время смены.') from exc
        if start==end:raise ValueError('Начало и конец смены не должны совпадать.')
    return day,status,start,end

def revision(db,day):
    workflows._validate_day(day)
    raw=db.execute('SELECT * FROM plan_days WHERE day=?',(day,)).fetchone()
    # Global plan generation also protects against edit-back-to-same-value (ABA),
    # imported fallback changes and series cancellation. Unrelated plan edits may
    # conservatively require a fresh preview rather than risk a lost update.
    generation=db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE kind IN ('plan_change','plan_series_created','plan_series_cancelled')").fetchone()[0]
    material={'day':day,'manual':dict(raw) if raw else None,'cleared':workflows.is_plan_cleared(db,day),'effective':workflows.plan_day(db,day),'generation':generation}
    return hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:32]

def label(plan):
    if not plan:return 'Не задан'
    if plan['status']=='off':return 'Выходной'
    return 'Рабочий · '+str(plan.get('start') or '—')+'–'+str(plan.get('end') or '—')

def parse(payload):
    if not isinstance(payload,str) or len(payload)>64:raise ValueError('Некорректная ссылка календаря.')
    match=PAYLOAD.fullmatch(payload)
    if not match:raise ValueError('Некорректная ссылка календаря.')
    stamp,kind,start,end,expected=match.groups()
    day=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:]
    if kind in ('o','n'):
        if start!='0000' or end!='0000':raise ValueError('У выходного не должно быть времени смены.')
        start=end=None
    else:start,end=start[:2]+':'+start[2:],end[:2]+':'+end[2:]
    status={'o':'off','n':'unset','s':'shift'}[kind]
    validate(day,status,start,end,allow_past=True)
    return day,status,start,end,expected

def flow(db,day,status,start=None,end=None,expected=None):
    validate(day,status,start,end,allow_past=True)
    current=revision(db,day)
    if expected is not None and expected!=current:
        raise ValueError('План уже изменился. Заново открой дату в календаре; старый выбор не сохранён.')
    return {'kind':'plan','day':day,'status':status,'start':start,'end':end,
            'step':'confirm','expected_revision':current,'before_label':label(workflows.plan_day(db,day))}

def apply(db,selection,*,manage_transaction=True):
    validate(selection['day'],selection['status'],selection.get('start'),selection.get('end'),allow_past=True)
    if not manage_transaction and not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    if manage_transaction:db.execute('BEGIN IMMEDIATE')
    try:
        if selection.get('expected_revision')!=revision(db,selection['day']):
            raise ValueError('План изменился после просмотра. Открой дату заново и проверь новое подтверждение.')
        if selection['status']=='unset':
            result=workflows.clear_plan(db,selection['day'],manage_transaction=False,allow_past=True)
        else:
            result=workflows.set_plan(db,selection['day'],selection['status'],selection.get('start'),selection.get('end'),manage_transaction=False,allow_past=True)
        if manage_transaction:db.execute('COMMIT')
        return result
    except Exception:
        if manage_transaction and db.in_transaction:db.execute('ROLLBACK')
        raise
