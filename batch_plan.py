"""Bounded monthly bulk plans: read-only preview, value-bound chat confirmation.

Links are untrusted proposals, not authorization. No facts or money are modified.
Every selected date is validated again within one immediate write transaction.
"""
import calendar
import hashlib
import json
import re
from html import escape
from ledger import parse_month
import calendar_plan
import workflows
from core import log

PAYLOAD=re.compile(r'b_([0-9]{6})_([0-9a-f]{8})_([son])_([0-9]{4})_([0-9]{4})_([0-9a-f]{24})')


def combine_revision(revisions):
    encoded=json.dumps(revisions,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def revision(db,month):
    first=parse_month(month)
    return combine_revision({f'{month}-{day:02d}':calendar_plan.revision(db,f'{month}-{day:02d}')
        for day in range(1,calendar.monthrange(first.year,first.month)[1]+1)})


def validate(month,days,status,start=None,end=None):
    parse_month(month)
    if not isinstance(days,list) or not 1<=len(days)<=31 or any(not isinstance(d,str) for d in days):
        raise ValueError('Выбери от 1 до 31 даты одного месяца.')
    if len(set(days))!=len(days):raise ValueError('Одна дата не должна повторяться.')
    for day in days:
        if day[:7]!=month:raise ValueError('Все даты должны быть в одном месяце.')
        calendar_plan.validate(day,status,start,end)
    return sorted(days)


def parse(payload):
    if not isinstance(payload,str) or len(payload)>64:raise ValueError('Неверная ссылка выбора дней.')
    match=PAYLOAD.fullmatch(payload)
    if not match:raise ValueError('Неверная ссылка выбора дней.')
    stamp,mask,kind,start,end,expected=match.groups()
    month=stamp[:4]+'-'+stamp[4:];first=parse_month(month)
    length=calendar.monthrange(first.year,first.month)[1];bits=int(mask,16)
    if bits==0 or bits>=(1<<length):raise ValueError('В выборе есть несуществующие даты.')
    days=[f'{month}-{i+1:02d}' for i in range(length) if bits&(1<<i)]
    status={'s':'shift','o':'off','n':'unset'}[kind]
    if status=='shift':start,end=start[:2]+':'+start[2:],end[:2]+':'+end[2:]
    else:
        if start!='0000' or end!='0000':raise ValueError('Для выходного и снятого плана время не задаётся.')
        start=end=None
    validate(month,days,status,start,end)
    return month,days,status,start,end,expected


def flow(db,month,days,status,start=None,end=None,expected=None):
    days=validate(month,days,status,start,end)
    current=revision(db,month)
    if expected is not None and expected!=current:
        raise ValueError('График изменился. Обнови календарь и выбери дни заново. Ничего не сохранено.')
    return {'kind':'batch_plan','step':'confirm','month':month,'days':days,
        'status':status,'start':start,'end':end,'expected_revision':current,
        'before':{day:calendar_plan.label(workflows.plan_day(db,day)) for day in days}}


def prompt(selection):
    status=selection['status']
    after='Не задан (план снят)' if status=='unset' else 'Выходной' if status=='off' else 'Рабочий · '+selection['start']+'–'+selection['end']
    lines=[f"<b>Проверь график · {len(selection['days'])} дат</b>",
           '<b>Будет для всех:</b> '+escape(after),'','<b>Сейчас по датам:</b>']
    for day in selection['days']:
        lines.append(day[8:]+'.'+day[5:7]+' · '+escape(selection['before'][day]))
    if status=='shift' and selection['end']<selection['start']:lines+=['','Уход на следующий день.']
    lines+=['','Изменятся только перечисленные даты. Фактические смены, деньги и офисный табель останутся без изменений.',
            'Все даты сохраняются вместе. При конфликте не изменится ни одна.']
    if status=='unset':lines+=['Снятый план не станет выходным. Исходные строки импорта сохраняются, но не возвращают снятый план.']
    return '\n'.join(lines)


def apply(db,selection,*,manage_transaction=True):
    days=validate(selection['month'],selection['days'],selection['status'],selection.get('start'),selection.get('end'))
    if not manage_transaction and not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    if manage_transaction:db.execute('BEGIN IMMEDIATE')
    try:
        # Revalidate after acquiring the lock, including the midnight boundary.
        validate(selection['month'],days,selection['status'],selection.get('start'),selection.get('end'))
        if selection.get('expected_revision')!=revision(db,selection['month']):
            raise ValueError('График изменился после просмотра. Ничего не сохранено. Выбери дни заново.')
        for day in days:
            if selection['status']=='unset':workflows.clear_plan(db,day,manage_transaction=False)
            else:workflows.set_plan(db,day,selection['status'],selection.get('start'),selection.get('end'),manage_transaction=False)
        log(db,'plan_batch_change',None,{'days':days,'status':selection['status'],
            'start':selection.get('start'),'end':selection.get('end'),'before':selection['before']})
        if manage_transaction:db.execute('COMMIT')
        return {'count':len(days)}
    except Exception:
        if manage_transaction and db.in_transaction:db.execute('ROLLBACK')
        raise
