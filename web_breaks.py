"""Audited break-interval edits, using the existing outer replay transaction.

No new schema, rate rules, inferred intervals or automatic changes. Preview is
read-only. Any edit explicitly replaces the shift's break total with the sum of
its recorded intervals (each rounded up exactly as workflows.stop_break does).
"""
import json
import math
import re
from datetime import datetime
import core
import actual_shift
from finance import shift_earnings
from workflows import _validate_day

ACTIONS = ('save_break', 'remove_break', 'undo_break_change')
MAX_INTERVALS = 96


def _id(value):
    if type(value) is not int or not 0 < value <= 2**53-1:
        raise ValueError('Некорректный номер записи.')
    return value


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', value):
        raise ValueError('Укажи дату и время начала и окончания перерыва.')
    _validate_day(value[:10])
    return core.parse_local_datetime(value[:10], value[11:])


def normalize(payload):
    if not isinstance(payload, dict) or payload.get('action') not in ACTIONS:
        raise ValueError('unknown_action')
    action = payload['action']
    fields = {'action', 'nonce', 'shift_id', 'expected_revision'}
    fields |= {'break_id', 'start', 'end'} if action == 'save_break' else {'break_id'} if action == 'remove_break' else {'event_id'}
    if set(payload)-fields:
        raise ValueError('unknown_fields')
    revision = payload.get('expected_revision')
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision):
        raise ValueError('Обнови перерывы: отсутствует версия смены.')
    value = {'action': action, 'shift_id': _id(payload.get('shift_id')), 'expected_revision': revision}
    if action == 'undo_break_change':
        value['event_id'] = _id(payload.get('event_id'))
    else:
        bid = payload.get('break_id')
        value['break_id'] = None if bid is None and action == 'save_break' else _id(bid)
        if action == 'save_break':
            _clock(payload.get('start')); _clock(payload.get('end'))
            value.update(start=payload['start'], end=payload['end'])
    return value


def _snapshot(db, sid):
    row = db.execute('SELECT * FROM shifts WHERE id=?', (sid,)).fetchone()
    if row is None:
        raise ValueError('Смена удалена. Обнови календарь.')
    sessions = [dict(r) for r in db.execute('SELECT * FROM break_sessions WHERE shift_id=? ORDER BY id LIMIT ?', (sid, MAX_INTERVALS+1))]
    if len(sessions) > MAX_INTERVALS:
        raise ValueError('В смене слишком много интервалов для этого редактора.')
    tracked = db.execute('SELECT * FROM tracked_break_shifts WHERE shift_id=?', (sid,)).fetchone()
    return {'shift': dict(row), 'sessions': sessions, 'tracked': dict(tracked) if tracked else None}


def _latest_undo(db, snap):
    row = db.execute("SELECT id,kind,details FROM events WHERE shift_id=? AND kind!='miniapp_operation' ORDER BY id DESC LIMIT 1", (snap['shift']['id'],)).fetchone()
    if row is None or row['kind'] != 'breaks_edited':
        return None
    data = json.loads(row['details'])
    if data.get('after_state') != snap:
        return None
    return row['id'], data


def _summary(snap):
    s = snap['shift']
    intervals = sum(r['minutes'] or 0 for r in snap['sessions'] if r['ended_utc'])
    mode = 'total_only' if not snap['tracked'] else 'tracked' if intervals == s['break_minutes'] else 'manual_override'
    return {'deducted_minutes': s['break_minutes'], 'interval_minutes': intervals, 'mode': mode,
            'running': any(r['ended_utc'] is None for r in snap['sessions'])}


def details(db, payload):
    if not isinstance(payload, dict) or set(payload) != {'view', 'shift_id'} or payload.get('view') != 'breaks':
        raise ValueError('unknown_fields')
    sid = _id(payload['shift_id']); snap = _snapshot(db, sid); s = snap['shift']
    undo = _latest_undo(db, snap)
    sessions = [{'id': r['id'], 'start': core.read_time(r['started_utc']).isoformat(),
                 'end': core.read_time(r['ended_utc']).isoformat() if r['ended_utc'] else None,
                 'minutes': r['minutes']} for r in sorted(snap['sessions'], key=lambda r: r['started_utc'])]
    return {'shift_id': sid, 'day': core.local_day(s['start_utc']), 'revision': actual_shift.shift_revision(db, sid),
            'start': core.read_time(s['start_utc']).isoformat(), 'end': core.read_time(s['end_utc']).isoformat() if s['end_utc'] else None,
            'gross_seconds': int((core.read_time(s['end_utc'])-core.read_time(s['start_utc'])).total_seconds()) if s['end_utc'] else None,
            'paid_seconds': round(core.paid_hours(s)*3600) if s['end_utc'] else None,
            'earnings': shift_earnings(db, s) if s['end_utc'] else None,
            'sessions': sessions, 'undo_event_id': undo[0] if undo else None,
            'office_estimated': '"estimate_basis": "office_photo"' in s['note'], **_summary(snap)}


def _validate_intervals(shift, sessions):
    if len(sessions) > MAX_INTERVALS:
        raise ValueError('Не больше 96 перерывов в одной смене.')
    start = core.read_time(shift['start_utc']); now = core.now()
    boundary = core.read_time(shift['end_utc']) if shift['end_utc'] else now
    previous_end = None; total = 0
    for r in sorted(sessions, key=lambda r: r['started_utc']):
        a = core.read_time(r['started_utc']); b = core.read_time(r['ended_utc']) if r['ended_utc'] else None
        if a < start or a > boundary or a > now:
            raise ValueError('Начало перерыва должно быть внутри смены и не в будущем.')
        if b is not None and (b < a or b > boundary or b > now):
            raise ValueError('Окончание перерыва должно быть после начала, внутри смены и не в будущем.')
        if b is None and shift['end_utc']:
            raise ValueError('У закрытой смены не может быть незавершённого перерыва.')
        if previous_end is not None and a < previous_end:
            raise ValueError('Перерывы пересекаются. Проверь даты и время.')
        previous_end = b if b is not None else datetime.max.replace(tzinfo=core.TZ)
        if b is not None:
            total += r['minutes'] or 0
    duration = (boundary-start).total_seconds()/60
    if not 0 <= total <= 600 or total > duration:
        raise ValueError('Сумма перерывов не может превышать 600 минут или длительность смены.')
    return total


def _prepare(db, value):
    sid = value['shift_id']; snap = _snapshot(db, sid)
    if value['expected_revision'] != actual_shift.shift_revision(db, sid):
        raise ValueError('Смена или перерывы изменились. Обнови раздел перед сохранением.')
    sessions = [dict(r) for r in snap['sessions']]; old = None
    if value['action'] == 'undo_break_change':
        undo = _latest_undo(db, snap)
        if not undo or undo[0] != value['event_id']:
            raise ValueError('После этой правки были другие изменения. Автоматическая отмена недоступна.')
        target = undo[1]['before_state']
        for session in target['sessions']:
            occupied = db.execute('SELECT shift_id FROM break_sessions WHERE id=?', (session['id'],)).fetchone()
            if occupied is not None and occupied['shift_id'] != sid:
                raise ValueError('Номер прежнего перерыва уже занят. Автоматическая отмена недоступна.')
        _validate_intervals(target['shift'], target['sessions'])
        if any(not r['ended_utc'] and (core.now()-core.read_time(r['started_utc'])).total_seconds() > 86400 for r in target['sessions']):
            raise ValueError('Нельзя восстановить перерыв, открытый больше суток назад.')
        return snap, target['sessions'], target['shift']['break_minutes'], target
    bid = value['break_id']
    if bid is not None:
        old = next((r for r in sessions if r['id'] == bid), None)
        if old is None:
            raise ValueError('Этот перерыв уже удалён или относится к другой смене.')
        sessions = [r for r in sessions if r['id'] != bid]
    if value['action'] == 'save_break':
        a, b = _clock(value['start']), _clock(value['end'])
        if old:
            # Keep original seconds for an unchanged minute shown by the form.
            if core.read_time(old['started_utc']).strftime('%Y-%m-%dT%H:%M') == value['start']:
                a = core.read_time(old['started_utc'])
            if old['ended_utc'] and core.read_time(old['ended_utc']).strftime('%Y-%m-%dT%H:%M') == value['end']:
                b = core.read_time(old['ended_utc'])
        if b <= a:
            raise ValueError('Окончание должно быть позже начала. Для ночного перерыва выбери следующую дату.')
        new = dict(old) if old else {'id': None, 'shift_id': sid, 'created_utc': core.iso(core.now())}
        new.update(started_utc=core.iso(a), ended_utc=core.iso(b), minutes=math.ceil((b-a).total_seconds()/60))
        sessions.append(new)
    total = _validate_intervals(snap['shift'], sessions)
    return snap, sessions, total, None


def preview(db, payload):
    if not isinstance(payload, dict) or set(payload) != {'kind', 'change'} or payload.get('kind') != 'break_change':
        raise ValueError('unknown_fields')
    value = normalize(payload['change'])
    snap, sessions, total, undo = _prepare(db, value)
    after = {**snap['shift'], 'break_minutes': total}
    before_info = _summary(snap)
    return {'shift_id': value['shift_id'], 'day': core.local_day(after['start_utc']),
            'before_minutes': snap['shift']['break_minutes'], 'after_minutes': total,
            'before_earnings': shift_earnings(db, snap['shift']) if after['end_utc'] else None,
            'after_earnings': shift_earnings(db, after) if after['end_utc'] else None,
            'replaces_total': undo is None and before_info['mode'] != 'tracked',
            'running_after': any(not r['ended_utc'] for r in sessions),
            'intervals_after': len(sessions), 'read_only': True}


def apply(db, payload):
    if not db.in_transaction:
        raise RuntimeError('An outer transaction is required')
    value = normalize(payload); sid = value['shift_id']
    snap, sessions, total, undo = _prepare(db, value)
    # Save a complete before-state in the audit event. FK links of the shift,
    # its times, note, plan, rates, office values and receipts are not modified.
    db.execute('DELETE FROM break_sessions WHERE shift_id=?', (sid,))
    for r in sessions:
        keys = [k for k in r if k != 'id' or r[k] is not None]
        db.execute('INSERT INTO break_sessions('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')', [r[k] for k in keys])
    if undo:
        db.execute('DELETE FROM tracked_break_shifts WHERE shift_id=?', (sid,))
        if undo['tracked']:
            db.execute('INSERT INTO tracked_break_shifts(shift_id,created_utc) VALUES(?,?)', (sid, undo['tracked']['created_utc']))
    else:
        db.execute('INSERT OR IGNORE INTO tracked_break_shifts(shift_id,created_utc) VALUES(?,?)', (sid, core.iso(core.now())))
    db.execute('UPDATE shifts SET break_minutes=?,updated_utc=? WHERE id=?', (total, core.iso(core.now()), sid))
    after = _snapshot(db, sid)
    core.log(db, 'breaks_edit_undone' if undo else 'breaks_edited', sid,
             {'day': core.local_day(snap['shift']['start_utc']), 'old': snap['shift'], 'after': after['shift'],
              'before_state': snap, 'after_state': after, 'break': total, 'source': 'miniapp',
              'operation': value['action'], 'event_id': value.get('event_id'), 'mode': 'chosen'})
    return {'success': True, 'event': value['action'], 'id': sid, 'day': core.local_day(after['shift']['start_utc']),
            'break_minutes': total}
