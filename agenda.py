"""Read-only daily agenda, independent of the month selected in the dashboard."""
from collections import defaultdict
from datetime import datetime,timedelta
from core import TZ,shifts,local_day
from workflows import plan_day
from ledger import _plan_interval,DAY_NAMES


def daily_agenda(db,reference=None):
    at=reference or datetime.now(TZ)
    if not isinstance(at,datetime) or at.tzinfo is None:
        raise ValueError('reference must be timezone-aware')
    at=at.astimezone(TZ);today=at.date();records=defaultdict(list)
    for row in shifts(db):records[local_day(row['start_utc'])].append(row)
    off_days={r[0] for r in db.execute("SELECT day FROM actual_days_off")}
    cache={}
    def entry(day):
        key=day.isoformat()
        if key in cache:return cache[key]
        plan=plan_day(db,key);actual=records.get(key,[]);interval=_plan_interval(key,plan)
        state=('open' if any(r['end_utc'] is None for r in actual) else 'worked' if actual
               else 'actual_off' if key in off_days else 'off' if plan and plan['status']=='off' else 'planned' if plan else 'unspecified')
        item={'day':key,'weekday':DAY_NAMES[day.weekday()],'plan':plan,'status':state,
              'start':plan.get('start') if plan else None,'end':plan.get('end') if plan else None,
              'next_day':bool(interval and interval[1].date()!=day),
              'start_iso':interval[0].isoformat() if interval else None,
              'end_iso':interval[1].isoformat() if interval else None,
              'has_actual':bool(actual) or key in off_days}
        cache[key]=item;return item
    days=[entry(today+timedelta(days=i)) for i in range(7)]
    running=None;nearest=None
    for offset in range(-1,90):
        item=entry(today+timedelta(days=offset))
        if item['has_actual'] or not item['start_iso']:continue
        start=datetime.fromisoformat(item['start_iso']);end=datetime.fromisoformat(item['end_iso'])
        if start<=at<end and running is None:running=item
        if start>at and nearest is None:nearest=item
        if nearest is not None and offset>=0:break
    return {'today':today.isoformat(),'generated_at':at.isoformat(),'days':days,
            'current_plan':running,'next_shift':nearest,'search_days':90,
            'counts':{'work':sum(bool(r['plan'] and r['plan']['status']=='shift') for r in days),
                      'off':sum(bool(r['plan'] and r['plan']['status']=='off') for r in days),
                      'unspecified':sum(r['plan'] is None for r in days)}}
