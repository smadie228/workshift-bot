"""Version-bound actual-time edits. Called only inside the audited API transaction.

Schedule, source documents, office hours and payment rows are never modified.
Unchanged clock fields preserve original seconds. Break sessions remain evidence;
manual break totals are explicitly recorded as an override in the audit event.
"""
import hashlib
import json
import re
from datetime import timedelta
import core
from workflows import _validate_day, active_break
from finance import shift_earnings

ACTIONS = ('correct_shift', 'add_shift', 'close_shift')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def shift_revision(db, sid):
    row = db.execute('SELECT * FROM shifts WHERE id=?', (sid,)).fetchone()
    if row is None:
        return None
    breaks = [dict(r) for r in db.execute('SELECT * FROM break_sessions WHERE shift_id=? ORDER BY id', (sid,))]
    generation = db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE shift_id=?', (sid,)).fetchone()[0]
    return _digest({'shift': dict(row), 'breaks': breaks, 'generation': generation})


def day_revision(db, day):
    _validate_day(day)
    rows = [dict(r) for r in core.shifts(db, day[:7]) if core.local_day(r['start_utc']) == day]
    # Include the factual generation to invalidate edit-back and overlapping writes.
    generation = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE shift_id IS NOT NULL").fetchone()[0]
    from actual_day import get
    off = get(db, day)
    off_generation = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE kind LIKE 'actual_day_off%'").fetchone()[0]
    return _digest({'day': day, 'rows': rows, 'generation': generation, 'off': off, 'off_generation': off_generation})


def attendance_revision(db):
    rows = [dict(r) for r in db.execute('SELECT * FROM shifts WHERE end_utc IS NULL')]
    generation = db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE shift_id IS NOT NULL').fetchone()[0]
    return _digest({'active': rows, 'generation': generation})


def normalize(payload):
    allowed = {'action', 'nonce', 'shift_id', 'day', 'start', 'end', 'break_minutes', 'expected_revision', 'reason'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('unknown_fields')
    action = payload.get('action')
    if action not in ACTIONS:
        raise ValueError('unknown_action')
    day = payload.get('day')
    _validate_day(day)
    sid = payload.get('shift_id')
    if action == 'add_shift':
        if sid is not None:
            raise ValueError('unexpected_shift')
    elif type(sid) is not int or not 0 < sid <= 2**53-1:
        raise ValueError('expected_shift_required')
    for key in ('start', 'end'):
        value = payload.get(key)
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', value):
            raise ValueError('Время должно быть в формате ЧЧ:ММ.')
        core.parse_local_datetime(day, value)
    if payload['start'] == payload['end']:
        raise ValueError('Начало и окончание смены должны отличаться.')
    minutes = payload.get('break_minutes')
    if type(minutes) is not int or not 0 <= minutes <= 600:
        raise ValueError('Перерыв — целое число от 0 до 600 минут.')
    expected = payload.get('expected_revision')
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Открой смену заново: отсутствует версия записи.')
    reason = payload.get('reason', '').strip() if isinstance(payload.get('reason', ''), str) else None
    if reason is None or len(reason) > 160 or any(ord(c) < 32 for c in reason):
        raise ValueError('Комментарий должен быть короче 161 символа без переводов строк.')
    return {'action': action, 'shift_id': sid, 'day': day, 'start': payload['start'],
            'end': payload['end'], 'break_minutes': minutes,
            'expected_revision': expected, 'reason': reason}


def apply(db, request):
    if not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    value = normalize(request)
    action, sid, day = value['action'], value['shift_id'], value['day']
    old = None
    if action != 'add_shift':
        row = db.execute('SELECT * FROM shifts WHERE id=?', (sid,)).fetchone()
        if row is None or core.local_day(row['start_utc']) != day:
            raise ValueError('Смена удалена или перенесена. Обнови календарь.')
        old = dict(row)
        if shift_revision(db, sid) != value['expected_revision']:
            raise ValueError('Смена изменилась после открытия. Обнови день и проверь значения заново.')
        if action == 'correct_shift' and row['end_utc'] is None:
            raise ValueError('Для открытой смены используй «Уточнить уход».')
        if action == 'close_shift' and row['end_utc'] is not None:
            raise ValueError('Смена уже закрыта. Обнови день.')
        if active_break(db, sid):
            raise ValueError('Сначала заверши текущий перерыв.')
    else:
        if day_revision(db, day) != value['expected_revision']:
            raise ValueError('Записи изменились. Обнови день перед добавлением.')
        if any(core.local_day(r['start_utc']) == day for r in core.shifts(db, day[:7])):
            raise ValueError('В этот день уже есть смена. Исправь существующую запись.')
    start = core.parse_local_datetime(day, value['start'])
    end = core.parse_local_datetime(day, value['end'])
    if value['end'] < value['start']:
        end += timedelta(days=1)
    if old:
        old_start = core.read_time(old['start_utc'])
        if value['start'] == old_start.strftime('%H:%M'):
            start = old_start
        if action == 'close_shift' and value['start'] != old_start.strftime('%H:%M'):
            raise ValueError('При уточнении ухода приход не меняется. После закрытия доступно полное исправление.')
        if old['end_utc']:
            old_end = core.read_time(old['end_utc'])
            if value['end'] == old_end.strftime('%H:%M') and end.astimezone(core.TZ).date() == old_end.date():
                end = old_end
    duration = (end-start).total_seconds()/60
    if not 0 < duration <= 1440:
        raise ValueError('Смена должна длиться больше нуля и не дольше 24 часов.')
    if end > core.now():
        raise ValueError('Нельзя записать завершённую смену с уходом из будущего.')
    if value['break_minutes'] >= duration:
        raise ValueError('Перерыв должен быть меньше длительности смены.')
    conflict = db.execute('''SELECT id FROM shifts WHERE start_utc < ?
        AND (end_utc IS NULL OR end_utc > ?) AND (? IS NULL OR id != ?) LIMIT 1''',
        (core.iso(end), core.iso(start), sid, sid)).fetchone()
    if conflict:
        raise ValueError('Эти часы пересекаются с другой сменой. Проверь время.')
    sessions = [] if sid is None else [dict(r) for r in db.execute('SELECT * FROM break_sessions WHERE shift_id=? ORDER BY id', (sid,))]
    for br in sessions:
        if not br['ended_utc'] or core.read_time(br['started_utc']) < start or core.read_time(br['ended_utc']) > end:
            raise ValueError('В выбранный интервал не помещается отмеченный перерыв. Проверь границы смены.')
    stamp = core.iso(core.now())
    if old is None:
        from actual_day import clear_for_work
        clear_for_work(db, day)
        cur = db.execute('''INSERT INTO shifts(start_utc,end_utc,break_minutes,origin,created_utc,updated_utc)
            VALUES(?,?,?,'manual',?,?)''', (core.iso(start), core.iso(end), value['break_minutes'], stamp, stamp))
        sid, kind = cur.lastrowid, 'manual'
    else:
        db.execute('UPDATE shifts SET start_utc=?,end_utc=?,break_minutes=?,updated_utc=? WHERE id=?',
                   (core.iso(start), core.iso(end), value['break_minutes'], stamp, sid))
        kind = 'late_close' if action == 'close_shift' else 'fix'
    details = {'old': old, 'day': day, 'start': value['start'], 'end': value['end'],
               'break': value['break_minutes'], 'source': 'miniapp',
               'reason': value['reason'] or 'Исправление владельцем в кабинете',
               'end_utc': core.iso(end), 'break_minutes': value['break_minutes'],
               'original_start': old['start_utc'] if old else None,
               'break_sessions_preserved': len(sessions),
               'manual_break_override': bool(sessions and old and old['break_minutes'] != value['break_minutes'])}
    core.log(db, kind, sid, details)
    after = db.execute('SELECT * FROM shifts WHERE id=?', (sid,)).fetchone()
    return {'success': True, 'event': action, 'id': sid, 'day': day,
            'hours': core.paid_hours(after), 'earnings': shift_earnings(db, after)}


def month_versions(db, month):
    """Read equivalent version tokens in five queries, never cache across requests.

    The caller supplies one SQLite read transaction. Mutation checks still use
    shift_revision/day_revision under the write lock, preserving their semantics.
    """
    from calendar import monthrange
    from collections import defaultdict
    first = _validate_day(month + '-01')
    rows = [dict(row) for row in core.shifts(db, month)]
    by_day = defaultdict(list)
    for row in rows:
        by_day[core.local_day(row['start_utc'])].append(row)
    generations = {row[0]: row[1] for row in db.execute(
        'SELECT shift_id,MAX(id) FROM events WHERE shift_id IS NOT NULL GROUP BY shift_id')}
    generation = max(generations.values(), default=0)
    off_generation = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE kind LIKE 'actual_day_off%'").fetchone()[0]
    off_days = {row['day']: dict(row) for row in db.execute(
        'SELECT * FROM actual_days_off WHERE day LIKE ?', (month + '-%',))}
    sessions = defaultdict(list)
    for row in db.execute('SELECT * FROM break_sessions ORDER BY id'):
        sessions[row['shift_id']].append(dict(row))
    days = {}
    for number in range(1, monthrange(first.year, first.month)[1]+1):
        day = f'{month}-{number:02d}'
        days[day] = _digest({'day': day, 'rows': by_day[day], 'generation': generation,
                            'off': off_days.get(day), 'off_generation': off_generation})
    versions = {row['id']: _digest({'shift': row, 'breaks': sessions[row['id']],
                'generation': generations.get(row['id'], 0)}) for row in rows}
    return {'days': days, 'shifts': versions,
            'tracked': {row['id']: bool(sessions[row['id']]) for row in rows}}
