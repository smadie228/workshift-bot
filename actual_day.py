"""Explicit actual days off, separate from plan, with reversible archived facts.

Only owner-confirmed API actions call apply inside the receipt transaction.
Closed shifts can be excluded only with replace_shifts=True. Their complete rows
and break evidence are archived before removal; restore is conflict checked.
"""
import json
import re
import core
from workflows import _validate_day

ACTIONS = ('mark_day_off', 'restore_day_off')


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS actual_days_off(
        day TEXT PRIMARY KEY, archive_json TEXT NOT NULL,
        created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL)''')


def get(db, day):
    row = db.execute('SELECT * FROM actual_days_off WHERE day=?', (day,)).fetchone()
    return dict(row) if row else None


def clear_for_work(db, day):
    """A deliberate new actual record supersedes an off marker, with evidence."""
    if not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    old = get(db, day)
    if old:
        core.log(db, 'actual_day_off_cleared', None, {'day': day, 'previous': old,
                 'reason': 'Добавлена фактическая работа; прежнее состояние сохранено в журнале'})
        db.execute('DELETE FROM actual_days_off WHERE day=?', (day,))


def normalize(payload):
    allowed = {'action', 'nonce', 'day', 'expected_revision', 'replace_shifts'}
    if not isinstance(payload, dict) or set(payload)-allowed:
        raise ValueError('unknown_fields')
    action = payload.get('action')
    if action not in ACTIONS:
        raise ValueError('unknown_action')
    day = payload.get('day');_validate_day(day)
    revision = payload.get('expected_revision')
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision):
        raise ValueError('Обнови день: отсутствует версия записи.')
    replace = payload.get('replace_shifts', False)
    if type(replace) is not bool or (action == 'restore_day_off' and replace):
        raise ValueError('Неверное подтверждение замены смены.')
    return {'action': action, 'day': day, 'expected_revision': revision, 'replace_shifts': replace}


def _insert(db, table, rows):
    for row in rows:
        columns=list(row)
        db.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')', [row[k] for k in columns])


def apply(db, request):
    if not db.in_transaction:
        raise RuntimeError('An outer write transaction is required')
    from actual_shift import day_revision
    value=normalize(request);day=value['day']
    if day > core.now().astimezone(core.TZ).date().isoformat():
        raise ValueError('Будущий выходной указывается в плане, а не в факте.')
    if day_revision(db,day) != value['expected_revision']:
        raise ValueError('Фактические записи изменились. Обнови день и проверь выбор.')
    off=get(db,day)
    if value['action']=='mark_day_off':
        if off:
            raise ValueError('Этот день уже отмечен как фактически выходной.')
        rows=[dict(r) for r in core.shifts(db,day[:7]) if core.local_day(r['start_utc'])==day]
        if any(r['end_utc'] is None for r in rows):
            raise ValueError('Сначала уточни уход открытой смены. Открытая смена не заменяется выходным.')
        if rows and not value['replace_shifts']:
            raise ValueError('В этот день есть работа. Нужна отдельная проверка исключаемых часов и начислений.')
        sessions=[];tracking=[];links=[]
        for row in rows:
            links += [dict(r) for r in db.execute('SELECT * FROM historical_imported_shifts WHERE shift_id=?',(row['id'],))]
            sessions += [dict(r) for r in db.execute('SELECT * FROM break_sessions WHERE shift_id=? ORDER BY id',(row['id'],))]
            tracking += [dict(r) for r in db.execute('SELECT * FROM tracked_break_shifts WHERE shift_id=?',(row['id'],))]
        if any(r['ended_utc'] is None for r in sessions):
            raise ValueError('Есть незавершённый перерыв. Сначала исправь его.')
        archive={'shifts':rows,'break_sessions':sessions,'tracked_break_shifts':tracking,'historical_imported_shifts':links}
        stamp=core.iso(core.now())
        db.execute('INSERT INTO actual_days_off VALUES(?,?,?,?)',(day,json.dumps(archive,ensure_ascii=False),stamp,stamp))
        for row in rows:
            db.execute('DELETE FROM break_sessions WHERE shift_id=?',(row['id'],))
            db.execute('DELETE FROM tracked_break_shifts WHERE shift_id=?',(row['id'],))
            db.execute('DELETE FROM historical_imported_shifts WHERE shift_id=?',(row['id'],))
            db.execute('DELETE FROM shifts WHERE id=?',(row['id'],))
        core.log(db,'actual_day_off',None,{'day':day,'archived':archive,'source':'miniapp'})
        return {'success':True,'event':'mark_day_off','day':day,'archived_shifts':len(rows)}
    if off is None:
        raise ValueError('Отметка выходного уже снята. Обнови день.')
    archive=json.loads(off['archive_json'])
    if any(core.local_day(r['start_utc'])==day for r in core.shifts(db,day[:7])):
        raise ValueError('В этот день уже появились другие фактические записи.')
    for row in archive['shifts']:
        overlap=db.execute('SELECT id FROM shifts WHERE start_utc<? AND (end_utc IS NULL OR end_utc>?) LIMIT 1', (row['end_utc'],row['start_utc'])).fetchone()
        if overlap or db.execute('SELECT 1 FROM shifts WHERE id=?',(row['id'],)).fetchone():
            raise ValueError('Прежняя смена пересекается с новой записью. Автоматическое восстановление не выполнено.')
    for link in archive.get('historical_imported_shifts',[]):
        if db.execute('SELECT 1 FROM historical_imported_shifts WHERE day=? OR shift_id=?',(link['day'],link['shift_id'])).fetchone():
            raise ValueError('Связь с исходным документом уже изменена. Автоматическое восстановление не выполнено.')
    _insert(db,'shifts',archive['shifts'])
    for row in archive['shifts']:
        core.log(db, 'actual_shift_restored', row['id'], {'day':day,'source':'actual_days_off'})
    _insert(db,'historical_imported_shifts',archive.get('historical_imported_shifts',[]))
    _insert(db,'tracked_break_shifts',archive['tracked_break_shifts'])
    _insert(db,'break_sessions',archive['break_sessions'])
    core.log(db,'actual_day_off_restored',None,{'day':day,'previous':off,'restored_shifts':len(archive['shifts'])})
    db.execute('DELETE FROM actual_days_off WHERE day=?',(day,))
    return {'success':True,'event':'restore_day_off','day':day,'restored_shifts':len(archive['shifts'])}
