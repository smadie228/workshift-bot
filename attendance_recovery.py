"""Owner-confirmed delayed attendance and reversible removal of one actual shift.

No inferred arrival is saved automatically. UTC storage, Moscow wall-clock input.
All mutations run inside miniapp_actions' transaction and replay receipt.
"""
import json
import re
from datetime import datetime, timedelta
import core
import actual_shift
from workflows import _validate_day, plan_day, active_break
from actual_day import clear_for_work, _insert

ACTIONS = ('clock_in_at', 'clock_out_at', 'move_open_start', 'delete_shift', 'restore_shift')
CHILDREN = ('break_sessions', 'tracked_break_shifts', 'historical_imported_shifts')


def _id(value):
    if type(value) is not int or not 0 < value <= 2**53-1:
        raise ValueError('Неверный номер записи.')
    return value


def normalize(payload):
    if not isinstance(payload, dict) or payload.get('action') not in ACTIONS:
        raise ValueError('unknown_action')
    action = payload['action']
    fields = {'action', 'nonce', 'expected_revision'}
    if action == 'restore_shift': fields |= {'event_id'}
    else:
        fields |= {'shift_id'}
        if action != 'delete_shift': fields |= {'mode', 'at'}
    if set(payload)-fields:
        raise ValueError('unknown_fields')
    revision = payload.get('expected_revision')
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision):
        raise ValueError('Обнови данные перед изменением.')
    value = {'action': action, 'expected_revision': revision}
    if action == 'restore_shift':
        value['event_id'] = _id(payload.get('event_id'))
        return value
    sid = payload.get('shift_id')
    if action == 'clock_in_at':
        if sid is not None: raise ValueError('unexpected_shift')
    else: sid = _id(sid)
    value['shift_id'] = sid
    if action == 'delete_shift': return value
    mode, stamp = payload.get('mode'), payload.get('at')
    if mode not in ('now', 'chosen') or (action == 'move_open_start' and mode != 'chosen'):
        raise ValueError('Выбери текущее или указанное время.')
    if mode == 'now':
        if stamp is not None: raise ValueError('unexpected_time')
    else:
        if not isinstance(stamp, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', stamp):
            raise ValueError('Укажи дату и время по Москве.')
        _validate_day(stamp[:10]); core.parse_local_datetime(stamp[:10], stamp[11:])
    value.update(mode=mode, at=stamp)
    return value


def hints(db, reference=None):
    """Schedule is a reason to ask, never proof of the actual event time."""
    at = (reference or core.now()).astimezone(core.TZ)
    current = core.open_shift(db)
    answer = {'threshold_minutes': 5, 'clock': at.isoformat(), 'in': None, 'out': None}
    if current:
        day = core.local_day(current['start_utc']); plan = plan_day(db, day)
        planned = None
        if plan and plan['status'] == 'shift' and plan.get('start') and plan.get('end'):
            try:
                start = core.parse_local_datetime(day, plan['start'])
                planned = core.parse_local_datetime(day, plan['end'])
                if planned <= start: planned += timedelta(days=1)
            except (ValueError, TypeError): planned = None
        answer['out'] = _hint(at, planned, 'end')
        answer['active_revision'] = actual_shift.shift_revision(db, current['id'])
        return answer
    candidates = []
    records = core.shifts(db)
    worked = {core.local_day(row['start_utc']) for row in records}
    for offset in (-1, 0):
        day = (at.date()+timedelta(days=offset)).isoformat()
        plan = plan_day(db, day)
        if day in worked or not plan or plan['status'] != 'shift' or not plan.get('start'): continue
        try: start = core.parse_local_datetime(day, plan['start'])
        except (ValueError, TypeError): continue
        if abs((at-start).total_seconds()) <= 24*3600:
            candidates.append(start)
    # Prefer a current/past scheduled start, otherwise today's future start.
    past = [start for start in candidates if start <= at]
    planned = max(past) if past else min(candidates) if candidates else None
    answer['in'] = _hint(at, planned, 'start')
    return answer


def _hint(at, planned, kind):
    delta = (at-planned).total_seconds() if planned else None
    return {'planned_at': planned.astimezone(core.TZ).isoformat() if planned else None,
            'difference_minutes': round(delta/60, 2) if delta is not None else None,
            'ask_time': planned is None or abs(delta) > 300,
            'reference': 'plan' if planned else 'none', 'kind': kind}


def _restored(db, event_id):
    return bool(db.execute("SELECT 1 FROM events WHERE kind='shift_delete_restored' AND json_extract(details,'$.event_id')=?", (event_id,)).fetchone())


def deleted_import_days(db):
    result = set()
    for event in db.execute("SELECT id,details FROM events WHERE kind='shift_deleted'"):
        if _restored(db, event['id']): continue
        archive = json.loads(event['details'])['archive']
        result.update(row['day'] for row in archive.get('historical_imported_shifts', []))
    return result


def restore_revision(db, event_id):
    event = db.execute("SELECT * FROM events WHERE id=? AND kind='shift_deleted'", (event_id,)).fetchone()
    if not event or _restored(db, event_id): raise ValueError('Удаление уже отменено или архив не найден.')
    data = json.loads(event['details'])
    return actual_shift._digest({'event': dict(event), 'day': actual_shift.day_revision(db, data['day'])})


def deleted_list(db, month):
    result = []
    for event in db.execute("SELECT id,details FROM events WHERE kind='shift_deleted' AND substr(json_extract(details,'$.day'),1,7)=? ORDER BY id DESC", (month,)):
        if _restored(db, event['id']): continue
        data = json.loads(event['details']); row = data['archive']['shifts'][0]
        result.append({'event_id': event['id'], 'day': data['day'], 'shift_id': row['id'],
                       'start': core.read_time(row['start_utc']).strftime('%H:%M'),
                       'end': core.read_time(row['end_utc']).strftime('%H:%M') if row['end_utc'] else None,
                       'revision': restore_revision(db, event['id'])})
    return result


def apply(db, payload):
    if not db.in_transaction: raise RuntimeError('An outer write transaction is required')
    value = normalize(payload); action = value['action']; now = core.now()
    if action == 'restore_shift': return _restore(db, value)
    sid = value['shift_id']; row = None
    if action == 'clock_in_at':
        if value['expected_revision'] != actual_shift.attendance_revision(db):
            raise ValueError('Состояние уже изменилось. Обнови кабинет.')
        if core.open_shift(db): raise ValueError('Смена уже открыта.')
    else:
        raw = db.execute('SELECT * FROM shifts WHERE id=?', (sid,)).fetchone()
        if raw is None or actual_shift.shift_revision(db, sid) != value['expected_revision']:
            raise ValueError('Смена изменилась. Открой её заново.')
        row = dict(raw)
    if action == 'delete_shift':
        archive = {'shifts': [row]}
        for table in CHILDREN:
            archive[table] = [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE shift_id=?', (sid,))]
        core.log(db, 'shift_deleted', sid, {'day': core.local_day(row['start_utc']), 'archive': archive, 'source': 'miniapp', 'reason': 'Удаление ошибочной смены подтверждено владельцем'})
        event_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for table in CHILDREN: db.execute('DELETE FROM '+table+' WHERE shift_id=?', (sid,))
        db.execute('DELETE FROM shifts WHERE id=?', (sid,))
        return {'success': True, 'event': action, 'id': sid, 'day': core.local_day(row['start_utc']), 'event_id': event_id}
    at = now if value['mode'] == 'now' else core.parse_local_datetime(value['at'][:10], value['at'][11:])
    if at > now: raise ValueError('Время не может быть в будущем.')
    if action in ('clock_in_at', 'move_open_start') and now-at > timedelta(hours=24):
        raise ValueError('Для уже прошедшей смены добавь приход и уход через календарь. Открытый приход доступен за последние 24 часа.')
    if row and row['end_utc'] is not None: raise ValueError('Смена уже завершена. Используй исправление закрытой смены.')
    if action == 'clock_out_at':
        if active_break(db, sid): raise ValueError('Сначала закончи перерыв или исправь ошибочную смену.')
        start, end = datetime.fromisoformat(row['start_utc']), at
    else: start, end = at, None
    if end is not None:
        duration = (end-start).total_seconds()
        if not 0 < duration <= 86400: raise ValueError('Уход должен быть позже прихода, смена — не длиннее 24 часов.')
        # A newly opened automatic deduction must not consume a shorter shift.
        # Manual or tracked break values remain protected by the original check.
        import core as core_break
        auto = core_break.skip_unfitting_automatic_break
        if row['break_minutes']*60 >= duration:
            if not auto(db,row,end):
                raise ValueError('Перерыв больше длительности. Уточни уход и перерыв в карточке дня.')
    conflict = db.execute('SELECT id FROM shifts WHERE (? IS NULL OR id!=?) AND (? IS NULL OR start_utc<?) AND (end_utc IS NULL OR end_utc>?) LIMIT 1',
                         (sid, sid, core.iso(end) if end else None, core.iso(end) if end else None, core.iso(start))).fetchone()
    if conflict: raise ValueError('Выбранное время пересекается с другой сменой.')
    if row:
        for br in db.execute('SELECT * FROM break_sessions WHERE shift_id=?', (sid,)):
            if core.read_time(br['started_utc']) < start or (end and (not br['ended_utc'] or core.read_time(br['ended_utc']) > end)):
                raise ValueError('В выбранное время не помещается отмеченный перерыв.')
    stamp = core.iso(now)
    if action == 'clock_in_at':
        clear_for_work(db, core.local_day(core.iso(start)))
        minutes=core.default_break(db)
        sid = db.execute('INSERT INTO shifts(start_utc,break_minutes,origin,created_utc,updated_utc,note) VALUES(?,?,?,?,?,?)',
                         (core.iso(start), minutes, 'button' if value['mode']=='now' else 'manual', stamp, stamp,core._automatic_break_note(minutes))).lastrowid
        kind = 'in'
    elif action == 'clock_out_at':
        db.execute('UPDATE shifts SET end_utc=?,updated_utc=? WHERE id=?', (core.iso(end), stamp, sid)); kind = 'out'
    else:
        clear_for_work(db, core.local_day(core.iso(start)))
        db.execute('UPDATE shifts SET start_utc=?,updated_utc=? WHERE id=?', (core.iso(start), stamp, sid)); kind = 'open_start_corrected'
    core.log(db, kind, sid, {'old': row, 'at': core.iso(at), 'recorded_at': stamp, 'mode': value['mode'],
                            'source': 'miniapp', 'reason': 'Время подтверждено владельцем', 'delay_seconds': max(0, int((now-at).total_seconds()))})
    return {'success': True, 'event': action, 'id': sid, 'day': core.local_day(core.iso(start)), 'at': core.iso(at)}


def _restore(db, value):
    event_id = value['event_id']
    if restore_revision(db, event_id) != value['expected_revision']:
        raise ValueError('Данные изменились. Обнови архив перед восстановлением.')
    raw = db.execute('SELECT details FROM events WHERE id=?', (event_id,)).fetchone()
    data = json.loads(raw['details']); archive = data['archive']; row = archive['shifts'][0]
    if db.execute('SELECT 1 FROM actual_days_off WHERE day=?', (data['day'],)).fetchone():
        raise ValueError('День отмечен выходным. Сначала сними эту отметку.')
    if db.execute('SELECT 1 FROM shifts WHERE id=?', (row['id'],)).fetchone(): raise ValueError('Номер смены уже занят.')
    if not row['end_utc'] and core.now()-datetime.fromisoformat(row['start_utc']) > timedelta(hours=24):
        raise ValueError('Слишком старая открытая смена. Добавь правильные приход и уход вместо восстановления.')
    conflict = db.execute('SELECT id FROM shifts WHERE (? IS NULL OR start_utc<?) AND (end_utc IS NULL OR end_utc>?) LIMIT 1',
                         (row['end_utc'], row['end_utc'], row['start_utc'])).fetchone()
    if conflict: raise ValueError('Прежняя смена пересекается с новой записью.')
    for table in CHILDREN:
        for child in archive[table]:
            key = 'id' if table == 'break_sessions' else 'day' if table == 'historical_imported_shifts' else 'shift_id'
            if db.execute('SELECT 1 FROM '+table+' WHERE '+key+'=?', (child[key],)).fetchone():
                raise ValueError('Связанные записи изменились. Восстановление отменено.')
    _insert(db, 'shifts', archive['shifts'])
    for table in CHILDREN: _insert(db, table, archive[table])
    core.log(db, 'shift_delete_restored', row['id'], {'event_id': event_id, 'day': data['day'], 'source': 'miniapp'})
    return {'success': True, 'event': 'restore_shift', 'id': row['id'], 'day': data['day']}
