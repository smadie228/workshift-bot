"""Owner-only, read-only activity feed. No raw event JSON or private metadata.

Events are paged by monotonic id, not mutable timestamps. Each request scans at
most SCAN_LIMIT rows and returns at most PAGE_SIZE items. Empty filtered pages
retain a continuation cursor; absence on one page never means no older history.
"""
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
import core
from workflows import _validate_day

PAGE_SIZE = 30
SCAN_LIMIT = 200
# Only business events are exposed. Tokens, nonces, paths, imports and technical
# receipts are deliberately not reflected into the browser.
LABELS = {
    'in': ('time', 'Приход отмечен'), 'out': ('time', 'Уход отмечен'),
    'manual': ('time', 'Смена добавлена'), 'fix': ('time', 'Время смены исправлено'),
    'late_close': ('time', 'Уход уточнён'), 'open_start_corrected': ('time', 'Приход исправлен'),
    'shift_deleted': ('time', 'Смена удалена'), 'shift_delete_restored': ('time', 'Смена восстановлена'),
    'breaks_edited': ('time', 'Перерывы исправлены'),
    'breaks_edit_undone': ('time', 'Правка перерывов отменена'),
    'break_start': ('time', 'Перерыв начат'), 'break_end': ('time', 'Перерыв завершён'),
    'automatic_break_skipped': ('time', 'Стандартный перерыв не вычтен: короткая смена'),
    'actual_day_off': ('time', 'Отмечен фактический выходной'),
    'actual_day_off_restored': ('time', 'Фактический выходной отменён'),
    'actual_day_off_cleared': ('time', 'Выходной заменён работой'),
    'plan_change': ('plan', 'План дня изменён'),
    'plan_series_created': ('plan', 'Повторяющийся график создан'),
    'plan_series_cancelled': ('plan', 'Будущая часть графика отменена'),
    'payment_received': ('money', 'Поступление добавлено'),
    'payment_updated': ('money', 'Поступление исправлено'),
    'adjustment_added': ('money', 'Корректировка начисления добавлена'),
    'adjustment_updated': ('money', 'Корректировка начисления исправлена'),
    'money_voided': ('money', 'Финансовая запись отменена'),
    'money_restored': ('money', 'Финансовая запись восстановлена'),
    'future_rate': ('money', 'Будущая ставка изменена'),
    'future_rate_removed': ('money', 'Будущая ставка отменена'),
    'setting': ('money', 'Настройка изменена'),
    'operation_reversed': ('time', 'Операция отменена'),
    'office': ('time', 'Офисная длительность внесена'),
    'office_transcription_shift_adjusted': ('time', 'Применён расчёт по офисным цифрам'),
}


def _object(raw):
    if not isinstance(raw, str) or len(raw) > 131072:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, RecursionError):
        return {}


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return _validate_day(value).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _instant(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(core.TZ) if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _date_at(value):
    dt = _instant(value)
    return dt.date().isoformat() if dt else None


def _clock(value):
    dt = _instant(value)
    if dt:
        return dt.strftime('%d.%m.%Y %H:%M:%S' if dt.second else '%d.%m.%Y %H:%M')
    if isinstance(value, str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        return value
    return None


def _minutes(value):
    return str(value)+' мин' if type(value) is int and 0 <= value <= 1440 else None


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > 1000000:
            return None
        return format(amount, '.2f').replace('.', ',')+' ₽'
    except (InvalidOperation, ValueError):
        return None


def _snapshot(value):
    if not isinstance(value, dict):
        return None
    if 'amount_rub' in value:
        return _money(value['amount_rub'])
    if 'start_utc' in value:
        a, b = _clock(value.get('start_utc')), _clock(value.get('end_utc'))
        return ' — '.join(x for x in [a, b or 'смена открыта', _minutes(value.get('break_minutes'))] if x)
    status = value.get('status')
    if status == 'off':
        return 'Выходной'
    if status == 'shift':
        return ' — '.join(filter(None, [_clock(value.get('start', value.get('start_time'))), _clock(value.get('end', value.get('end_time')))])) or 'Рабочий день'
    return None


def _project(row, data, anchor):
    kind = row['kind']
    old = data.get('old') if isinstance(data.get('old'), dict) else {}
    after = data.get('after') if isinstance(data.get('after'), dict) else {}
    days = set(filter(None, [_day(data.get('day')), _day(data.get('paid_on')),
                            _date_at(old.get('start_utc')), _date_at(after.get('start_utc')), _date_at(anchor)]))
    if kind in ('in', 'open_start_corrected'):
        actual_day = _date_at(data.get('at'))
        if actual_day: days.add(actual_day)
    day = _day(data.get('day')) or _date_at(after.get('start_utc')) or _date_at(anchor) or next(iter(sorted(days)), None)
    fields = []
    def add(label, value):
        if value:
            fields.append({'label': label, 'value': value})
    if kind == 'plan_change':
        add('Было', _snapshot(data.get('before')) or 'Не задан')
        add('Стало', _snapshot(data.get('after')) or 'Не задан')
    else:
        add('Было', _snapshot(old))
        add('Стало', _snapshot(after))
        if not after:
            add('Приход', _clock(data.get('start')))
            add('Уход', _clock(data.get('end', data.get('end_utc'))))
            add('Время', _clock(data.get('at')))
            add('Сумма', _money(data.get('amount', data.get('rate'))))
        add('Перерыв', _minutes(data.get('break', data.get('minutes'))))
    if kind == 'setting' and data.get('kind') == 'break':
        fields = [{'label': label, 'value': val} for label, val in [('Было', _minutes(data.get('before'))), ('Стало', _minutes(data.get('after')))] if val]
    if kind == 'future_rate' or kind == 'future_rate_removed':
        add('Действует с', _day(data.get('effective_from')))
    if kind.startswith('plan_series_'):
        for key, label in [('created', 'Добавлено дат'), ('removed', 'Снято дат'), ('preserved', 'Сохранено дат')]:
            val = data.get(key)
            if type(val) is int and 0 <= val <= 730: add(label, str(val))
    estimated = kind == 'office_transcription_shift_adjusted' or data.get('source') == 'office_photo_estimate'
    return {'id': row['id'], 'at': (_instant(row['event_utc']).isoformat() if _instant(row['event_utc']) else None),
            'category': LABELS[kind][0], 'title': LABELS[kind][1], 'day': day, 'days': sorted(days),
            'fields': fields[:6], 'estimated': estimated,
            'manually_timed': data.get('mode') == 'chosen',
            'source': 'Кабинет' if data.get('source') == 'miniapp' else 'Офисный расчёт' if estimated else 'Журнал учёта'}


def page(db, payload):
    if not isinstance(payload, dict) or set(payload)-{'view', 'day', 'before_id', 'category'} or payload.get('view') != 'activity':
        raise ValueError('unknown_fields')
    day = payload.get('day')
    if day is not None and _day(day) != day:
        raise ValueError('Некорректная дата истории.')
    category = payload.get('category', 'all')
    if not isinstance(category, str) or category not in ('all', 'time', 'plan', 'money'):
        raise ValueError('Некорректный раздел истории.')
    before = payload.get('before_id', 2**53-1)
    if type(before) is not int or not 0 < before <= 2**53-1:
        raise ValueError('Некорректная страница истории.')
    kinds = [k for k, value in LABELS.items() if category == 'all' or value[0] == category]
    query = ('SELECT e.id,e.event_utc,e.kind,e.shift_id,'
             "CASE WHEN length(e.details)<=131072 THEN e.details ELSE '{}' END AS details,"
             's.start_utc AS anchor FROM events e LEFT JOIN shifts s ON s.id=e.shift_id '
             'WHERE e.id<? AND e.kind IN ('+','.join('?' for _ in kinds)+') ORDER BY e.id DESC LIMIT ?')
    rows = db.execute(query, (before, *kinds, SCAN_LIMIT+1)).fetchall()
    items, anchors, examined = [], {}, 0
    for row in rows[:SCAN_LIMIT]:
        examined += 1
        data = _object(row['details']); anchor = row['anchor']; sid = row['shift_id']
        if anchor is None and sid is not None:
            if sid not in anchors:
                saved = db.execute("SELECT details FROM events WHERE shift_id=? AND kind='shift_deleted' ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
                record = _object(saved[0]).get('archive', {}) if saved else {}
                shifts = record.get('shifts', []) if isinstance(record, dict) else []
                anchors[sid] = shifts[0].get('start_utc') if shifts and isinstance(shifts[0], dict) else None
            anchor = anchors[sid]
        item = _project(row, data, anchor)
        matches = day is None or day in item['days']
        if day and row['kind'].startswith('plan_series_'):
            series_id = data.get('id')
            if isinstance(series_id, str):
                matches = bool(db.execute('SELECT 1 FROM plan_series_days WHERE series_id=? AND day=?', (series_id, day)).fetchone())
                if matches: item['day'] = day
        if matches: items.append(item)
        if len(items) == PAGE_SIZE: break
    more = examined < len(rows)
    return {'items': items, 'day': day, 'category': category, 'has_more': more,
            'next_before_id': rows[examined-1]['id'] if more and examined else None,
            'scanned': examined, 'read_only': True}
