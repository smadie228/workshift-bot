"""Strict direct-calendar commands. Share the outer replay-safe transaction.

No facts, pay, office data or imports are rewritten. Historical edits are allowed
only for a single explicit date; bulk edits remain today/future in one month.
"""
import re
import calendar_plan
import batch_plan
from ledger import parse_month

ACTIONS = ('set_plan', 'set_plan_batch')


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError('bad_body')
    action = payload.get('action')
    if action not in ACTIONS:
        raise ValueError('unknown_action')
    common = {'action', 'nonce', 'status', 'start', 'end', 'expected_revision'}
    allowed = common | ({'day'} if action == 'set_plan' else {'month', 'days'})
    if set(payload) - allowed:
        raise ValueError('unknown_fields')
    status, start, end = payload.get('status'), payload.get('start'), payload.get('end')
    if not isinstance(status, str) or status not in ('shift', 'off', 'unset'):
        raise ValueError('Выбери рабочий, выходной или не задан.')
    expected = payload.get('expected_revision')
    length = 32 if action == 'set_plan' else 24
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{'+str(length)+'}', expected):
        raise ValueError('Обнови календарь: отсутствует версия плана.')
    value = {'action': action, 'status': status, 'start': start, 'end': end,
             'expected_revision': expected}
    if action == 'set_plan':
        day = payload.get('day')
        calendar_plan.validate(day, status, start, end, allow_past=True)
        value['day'] = day
    else:
        month = payload.get('month')
        parse_month(month)
        days = payload.get('days')
        # Validate calendar structure now; temporal rules are checked on apply.
        # A committed batch must still be replayable after midnight.
        if not isinstance(days, list) or not 1 <= len(days) <= 31 or any(not isinstance(d, str) for d in days):
            raise ValueError('Выбери от 1 до 31 даты одного месяца.')
        if len(set(days)) != len(days):
            raise ValueError('Одна дата не должна повторяться.')
        for day in days:
            if day[:7] != month:
                raise ValueError('Все даты должны быть в одном месяце.')
            calendar_plan.validate(day, status, start, end, allow_past=True)
        value.update(month=month, days=sorted(days))
    return value


def apply(db, value):
    if not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    value = normalize(value)
    if value['action'] == 'set_plan':
        selected = calendar_plan.flow(db, value['day'], value['status'], value['start'],
                                      value['end'], expected=value['expected_revision'])
        calendar_plan.apply(db, selected, manage_transaction=False)
        return {'success': True, 'event': 'set_plan', 'day': value['day'], 'count': 1}
    selected = batch_plan.flow(db, value['month'], value['days'], value['status'],
                               value['start'], value['end'], expected=value['expected_revision'])
    result = batch_plan.apply(db, selected, manage_transaction=False)
    return {'success': True, 'event': 'set_plan_batch', 'month': value['month'], **result}
