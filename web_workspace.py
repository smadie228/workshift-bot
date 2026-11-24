"""Owner-only workspace operations inside the existing replay-safe transaction.
No bank transfers, no implicit payroll rules, and no external storage.
Amounts are exact decimal strings. Every edit compares the displayed revision.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
import core
import finance
import planning
from workflows import _validate_day
from ledger import parse_month

ACTIONS = ('save_payment', 'save_adjustment', 'void_money', 'restore_money',
           'set_rate', 'remove_future_rate', 'set_default_break', 'create_series', 'cancel_series')
TABLES = {'payment': 'received_payments', 'adjustment': 'pay_adjustments'}
CATEGORIES = ('bonus', 'allowance', 'reimbursement', 'deduction')
MONEY_KINDS = ('payment_received', 'adjustment_added', 'payment_updated', 'adjustment_updated',
               'money_voided', 'money_restored', 'future_rate', 'future_rate_removed', 'setting', 'operation_reversed')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def revision(db):
    material = {}
    for table in ('received_payments', 'pay_adjustments', 'rate_history'):
        material[table] = [dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
    material['break'] = core.default_break(db)
    marks = ','.join('?' for _ in MONEY_KINDS)
    material['generation'] = db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE kind IN ('+marks+')', MONEY_KINDS).fetchone()[0]
    return digest(material)


def exact_amount(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,7}(?:[.,][0-9]{1,2})?', value):
        raise ValueError('Сумма — положительное число, не больше двух знаков после запятой.')
    return str(finance._amount(value))


def note(value):
    if not isinstance(value, str) or len(value) > 150 or any(ord(c) < 32 for c in value):
        raise ValueError('Комментарий — до 150 символов без переводов строк.')
    return value.strip()


def identifier(value):
    if type(value) is not int or not 0 < value <= 2**53-1:
        raise ValueError('Некорректный номер записи.')
    return value


def normalize(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('action'), str) or payload['action'] not in ACTIONS:
        raise ValueError('unknown_action')
    action = payload['action']
    fields = {
        'save_payment': {'record_id', 'work_month', 'period', 'amount', 'paid_on', 'note'},
        'save_adjustment': {'record_id', 'day', 'category', 'amount', 'note'},
        'void_money': {'kind', 'record_id'}, 'restore_money': {'event_id'},
        'set_rate': {'effective_from', 'amount'}, 'remove_future_rate': {'effective_from'},
        'set_default_break': {'minutes'}, 'create_series': {'spec'}, 'cancel_series': {'series_id'}
    }[action]
    if set(payload) - (fields | {'action', 'nonce', 'expected_revision'}):
        raise ValueError('unknown_fields')
    expected = payload.get('expected_revision')
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Обнови форму: отсутствует версия данных.')
    value = {'action': action, 'expected_revision': expected}
    if action in ('save_payment', 'save_adjustment'):
        rid = payload.get('record_id')
        value.update(record_id=None if rid is None else identifier(rid), amount=exact_amount(payload.get('amount')),
                     note=note(payload.get('note', '')))
        if action == 'save_payment':
            month = payload.get('work_month'); parse_month(month)
            period = payload.get('period')
            if type(period) is not int or period not in (1, 2):
                raise ValueError('Выбери первую или вторую часть месяца.')
            day = payload.get('paid_on'); _validate_day(day)
            value.update(work_month=month, period=period, paid_on=day)
        else:
            day = payload.get('day'); _validate_day(day)
            category = payload.get('category')
            if not isinstance(category, str) or category not in CATEGORIES:
                raise ValueError('Выбери вид корректировки.')
            value.update(day=day, category=category)
    elif action == 'void_money':
        kind = payload.get('kind')
        if not isinstance(kind, str) or kind not in TABLES:
            raise ValueError('Неизвестный вид записи.')
        value.update(kind=kind, record_id=identifier(payload.get('record_id')))
    elif action == 'restore_money':
        value['event_id'] = identifier(payload.get('event_id'))
    elif action in ('set_rate', 'remove_future_rate'):
        day = payload.get('effective_from'); _validate_day(day)
        value['effective_from'] = day
        if action == 'set_rate':
            value['amount'] = exact_amount(payload.get('amount'))
            if not Decimal('1') <= Decimal(value['amount']) <= Decimal('100000'):
                raise ValueError('Ставка — от 1 до 100 000 ₽/ч.')
    elif action == 'set_default_break':
        minutes = payload.get('minutes')
        if type(minutes) is not int or not 0 <= minutes <= 600:
            raise ValueError('Перерыв — целое число от 0 до 600 минут.')
        value['minutes'] = minutes
    elif action == 'create_series':
        spec = payload.get('spec')
        if not isinstance(spec, dict):
            raise ValueError('Неверный шаблон графика.')
        # Normalize calendar structure without making a committed replay expire at midnight.
        start = _validate_day(spec.get('start'))
        value['spec'] = planning.normalize(spec, datetime.combine(start, datetime.min.time(), tzinfo=core.TZ))
    else:
        sid = payload.get('series_id')
        if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{32}', sid):
            raise ValueError('Неверный номер серии.')
        value['series_id'] = sid
    return value


def list_data(db, month):
    parse_month(month)
    today = core.now().astimezone(core.TZ).date().isoformat()
    payments = [dict(r) for r in db.execute('SELECT * FROM received_payments WHERE work_month=? ORDER BY paid_on DESC,id DESC', (month,))]
    adjustments = [dict(r) for r in db.execute('SELECT * FROM pay_adjustments WHERE substr(day,1,7)=? ORDER BY day DESC,id DESC', (month,))]
    rates = [dict(r) for r in db.execute('SELECT * FROM rate_history ORDER BY effective_from DESC')]
    archives = []
    for event in db.execute("SELECT id,event_utc,details FROM events WHERE kind='money_voided' ORDER BY id DESC LIMIT 200"):
        data = json.loads(event['details']); old = data['old']; kind = data['kind']
        belongs = old.get('work_month') == month if kind == 'payment' else old.get('day', '')[:7] == month
        restored = db.execute("SELECT 1 FROM events WHERE kind='money_restored' AND json_extract(details,'$.event_id')=?", (event['id'],)).fetchone()
        if belongs and not restored:
            archives.append({'event_id': event['id'], 'kind': kind, 'old': old, 'at': event['event_utc']})
    series = []
    for row in planning.series_list(db, 50):
        series.append({'id': row['id'], 'status': row['status'], 'spec': json.loads(row['spec_json']),
                       'result': json.loads(row['result_json'])})
    return {'month': month, 'revision': revision(db), 'payments': payments, 'adjustments': adjustments,
            'archives': archives, 'current_rate': finance.rate_at(db, today), 'rates': rates,
            'default_break_minutes': core.default_break(db), 'series': series, 'today': today}



def payment_history(db, limit=60):
    if type(limit) is not int or not 1 <= limit <= 120:
        raise ValueError('История выплат: выбери от 1 до 120 записей.')
    rows=[dict(r) for r in db.execute(
        'SELECT id,work_month,period,amount_rub,paid_on,note FROM received_payments '
        'ORDER BY paid_on DESC,id DESC LIMIT ?', (limit+1,))]
    return {'payments':rows[:limit],'limit':limit,'has_more':len(rows)>limit}

def preview(db, payload):
    if not isinstance(payload, dict):
        raise ValueError('bad_body')
    kind = payload.get('kind')
    if kind == 'series':
        if set(payload) != {'kind', 'spec'}: raise ValueError('unknown_fields')
        return planning.preview(db, payload['spec'])
    if kind == 'cancel_series':
        if set(payload) != {'kind', 'series_id'}: raise ValueError('unknown_fields')
        sid = payload['series_id']
        if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{32}', sid): raise ValueError('Неверная серия.')
        return planning.cancel_preview(db, sid)
    raise ValueError('unknown_preview')


def apply(db, payload, nonce):
    if not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    value = normalize(payload); action = value['action']; expected = value['expected_revision']
    if action == 'create_series':
        result = planning.apply(db, value['spec'], nonce, expected, manage_transaction=False)
        return {'success': True, 'event': action, 'series_id': result['id'], 'created': result['created']}
    if action == 'cancel_series':
        result = planning.cancel(db, value['series_id'], expected, manage_transaction=False)
        return {'success': True, 'event': action, 'series_id': value['series_id'], **result}
    if expected != revision(db):
        raise ValueError('Данные изменились. Закрой форму, обнови раздел и проверь новые значения.')
    today = core.now().astimezone(core.TZ).date().isoformat(); stamp = core.iso(core.now())
    result = {'success': True, 'event': action}
    if action in ('save_payment', 'save_adjustment'):
        kind = 'payment' if action == 'save_payment' else 'adjustment'; table = TABLES[kind]
        day = value['paid_on'] if kind == 'payment' else value['day']
        if day > today: raise ValueError('Фактическое поступление или корректировка не могут быть из будущего.')
        rid = value['record_id']; old = None
        if rid is not None:
            row = db.execute('SELECT * FROM '+table+' WHERE id=?', (rid,)).fetchone()
            if row is None: raise ValueError('Запись уже исключена. Обнови раздел.')
            old = dict(row)
        data = ({'work_month': value['work_month'], 'period': value['period'], 'amount_rub': value['amount'],
                 'paid_on': value['paid_on'], 'note': value['note']} if kind == 'payment' else
                {'day': value['day'], 'category': value['category'], 'amount_rub': value['amount'], 'note': value['note']})
        if old:
            db.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in data)+' WHERE id=?', [*data.values(), rid])
        else:
            data['created_utc'] = stamp
            rid = db.execute('INSERT INTO '+table+'('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')', list(data.values())).lastrowid
        after = dict(db.execute('SELECT * FROM '+table+' WHERE id=?', (rid,)).fetchone())
        details = {'id': rid, 'old': old, 'after': after, 'source': 'miniapp', 'amount': value['amount'],
                   **({k: value[k] for k in ('work_month','period','paid_on')} if kind == 'payment' else {k: value[k] for k in ('day','category')})}
        core.log(db, (kind+'_updated') if old else ('payment_received' if kind == 'payment' else 'adjustment_added'), details=details)
        result['record_id'] = rid
    elif action == 'void_money':
        table = TABLES[value['kind']]
        row = db.execute('SELECT * FROM '+table+' WHERE id=?', (value['record_id'],)).fetchone()
        if row is None: raise ValueError('Запись уже исключена.')
        core.log(db, 'money_voided', details={'kind': value['kind'], 'id': row['id'], 'old': dict(row), 'source': 'miniapp'})
        db.execute('DELETE FROM '+table+' WHERE id=?', (row['id'],))
    elif action == 'restore_money':
        event = db.execute("SELECT details FROM events WHERE id=? AND kind='money_voided'", (value['event_id'],)).fetchone()
        if event is None: raise ValueError('Архив не найден.')
        if db.execute("SELECT 1 FROM events WHERE kind='money_restored' AND json_extract(details,'$.event_id')=?", (value['event_id'],)).fetchone():
            raise ValueError('Запись уже восстановлена.')
        data = json.loads(event['details']); table = TABLES[data['kind']]; old = data['old']
        if db.execute('SELECT 1 FROM '+table+' WHERE id=?', (old['id'],)).fetchone(): raise ValueError('Номер записи занят. Обнови раздел.')
        db.execute('INSERT INTO '+table+'('+','.join(old)+') VALUES('+','.join('?' for _ in old)+')', list(old.values()))
        core.log(db, 'money_restored', details={'event_id': value['event_id'], 'kind': data['kind'], 'id': old['id'], 'after': old, 'source': 'miniapp'})
    elif action in ('set_rate', 'remove_future_rate'):
        day = value['effective_from']
        if day <= today: raise ValueError('Ставку можно менять только с будущей даты. Прошлые начисления сохраняются.')
        old = db.execute('SELECT * FROM rate_history WHERE effective_from=?', (day,)).fetchone()
        if action == 'set_rate':
            db.execute("INSERT INTO rate_history VALUES(?,?,?,?) ON CONFLICT(effective_from) DO UPDATE SET rate_rub=excluded.rate_rub,created_utc=excluded.created_utc,note=excluded.note", (day, value['amount'], stamp, 'Изменено владельцем в кабинете'))
        else:
            if not old: raise ValueError('Будущая ставка уже отменена.')
            db.execute('DELETE FROM rate_history WHERE effective_from=?', (day,))
        core.log(db, 'future_rate' if action == 'set_rate' else 'future_rate_removed', details={'effective_from': day, 'rate': value.get('amount'), 'old': dict(old) if old else None, 'source': 'miniapp'})
    else:
        previous = core.default_break(db); core.put_config(db, 'default_break_minutes', value['minutes'])
        core.log(db, 'setting', details={'kind': 'break', 'before': previous, 'after': value['minutes'], 'source': 'miniapp'})
    return result
