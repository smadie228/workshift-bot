"""One read-only monthly projection for Telegram, the dashboard and reports.

Facts, estimates, office confirmations and money received are never interchanged.
No migration, update or external request is performed by this module.
"""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from core import TZ, shifts, local_day, read_time, paid_hours, default_break
from finance import rate_at, shift_earnings, payment_totals
from workflows import plan_day, adjustment_totals

MONTHS=("Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь")
DAY_NAMES=("Пн","Вт","Ср","Чт","Пт","Сб","Вс")
STATUS={"worked":"Отработано","open":"В работе","planned":"План","off":"Выходной","missing":"Нет отметки","empty":"Нет записей","actual_off":"Факт: выходной"}
COMPARE={"matched":"Совпадает","difference":"Расхождение","office_missing":"Нет табеля","personal_missing":"Нет личной отметки","in_progress":"Смена открыта"}


def parse_month(value):
    if not isinstance(value,str) or len(value)!=7:
        raise ValueError("Выбери месяц в формате ГГГГ-ММ")
    try:
        d=datetime.strptime(value,"%Y-%m").date()
    except ValueError as exc:
        raise ValueError("Неверный месяц") from exc
    if not 2000<=d.year<=2100 or d.strftime("%Y-%m")!=value:
        raise ValueError("Месяц должен быть между 2000 и 2100 годами")
    return d


def month_title(value):
    d=parse_month(value)
    return f"{MONTHS[d.month-1]} {d.year}"


def available_months(db):
    result={datetime.now(TZ).strftime("%Y-%m")}
    for row in shifts(db):
        result.add(local_day(row["start_utc"])[:7])
    for query in (
        "SELECT substr(day,1,7) AS m FROM plan_days",
        "SELECT substr(day,1,7) AS m FROM office_days",
        "SELECT substr(day,1,7) AS m FROM pay_adjustments",
        "SELECT substr(day,1,7) AS m FROM history_source_rows WHERE status IN ('План','Выходной')",
        "SELECT work_month AS m FROM received_payments",
    ):
        for row in db.execute(query):
            try:parse_month(row["m"])
            except (ValueError,TypeError):continue
            result.add(row["m"])
    return sorted(result,reverse=True)


def _plan_interval(day,plan):
    if not plan or plan["status"]!="shift" or not plan["start"] or not plan["end"]:
        return None
    try:
        start=datetime.strptime(day+" "+plan["start"],"%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        end=datetime.strptime(day+" "+plan["end"],"%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except (ValueError,TypeError):return None
    if end<=start:end+=timedelta(days=1)
    return start,end


def month_ledger(db,month,reference=None):
    first=parse_month(month)
    at=reference or datetime.now(TZ)
    if not isinstance(at,datetime) or at.tzinfo is None:
        raise ValueError("reference must be timezone-aware")
    at=at.astimezone(TZ)
    days_in_month=calendar.monthrange(first.year,first.month)[1]
    default_minutes=default_break(db)
    import json
    actual_off_days={r["day"]:len(json.loads(r["archive_json"])["shifts"]) for r in db.execute("SELECT day,archive_json FROM actual_days_off WHERE day LIKE ?",(month+"-%",))}
    grouped=defaultdict(list)
    raw=shifts(db,month)
    rates=defaultdict(lambda:{"hours":0.0,"earned":0.0,"shifts":0})
    for s in raw:
        day=local_day(s["start_utc"])
        start=read_time(s["start_utc"])
        end=read_time(s["end_utc"]) if s["end_utc"] else None
        hourly=rate_at(db,day)
        hours=paid_hours(s) if end else None
        earned=shift_earnings(db,s) if end else None
        item={"id":s["id"],"day":day,"start":start.strftime("%H:%M"),
              "end":end.strftime("%H:%M") if end else None,
              "start_iso":start.isoformat(),"end_iso":end.isoformat() if end else None,
              "next_day":bool(end and end.date()!=start.date()),"rate":hourly,
              "break_minutes":s["break_minutes"],"hours":hours,"earnings":earned,
              "gross_hours":(end-start).total_seconds()/3600 if end else None,
              "source":{"import_excel":"import","manual":"manual"}.get(s["origin"],"button")}
        grouped[day].append(item)
        if end:
            rates[hourly]["hours"]+=hours
            rates[hourly]["earned"]+=earned
            rates[hourly]["shifts"]+=1
    office={r["day"]:r["hours"] for r in db.execute("SELECT day,hours FROM office_days WHERE day LIKE ?",(month+"-%",))}
    extras=adjustment_totals(db,month)
    payments=payment_totals(db,month)
    adjustment_rows=[dict(r) for r in db.execute("SELECT day,category,amount_rub,note FROM pay_adjustments WHERE day LIKE ? ORDER BY day,id",(month+"-%",))]
    rows=[]
    weekly=defaultdict(float)
    cumulative=0.0
    future_hours=future_pay=0.0
    future_count=0
    for daynum in range(1,days_in_month+1):
        d=date(first.year,first.month,daynum)
        day=d.isoformat()
        records=sorted(grouped.get(day,[]),key=lambda r:r["start_iso"])
        closed=[r for r in records if r["end"]]
        opened=any(not r["end"] for r in records)
        hours=sum(r["hours"] for r in closed)
        earned=round(sum(r["earnings"] for r in closed),2)
        actual_off=day in actual_off_days and not records
        plan=plan_day(db,day)
        interval=_plan_interval(day,plan)
        planned_gross=(interval[1]-interval[0]).total_seconds()/3600 if interval else None
        planned_hours=max(0,planned_gross-default_minutes/60) if interval else None
        projected_pay=round(planned_hours*rate_at(db,day),2) if planned_hours is not None else None
        # A started/planned-in-the-past shift cannot silently become future earnings.
        prospective=bool(interval and interval[0]>=at and not records and not actual_off)
        if prospective:
            future_hours+=planned_hours;future_pay+=projected_pay;future_count+=1
        missed=bool(interval and interval[1]<at and not records and not actual_off)
        status="open" if opened else "worked" if closed else "actual_off" if actual_off else "missing" if missed else "off" if plan and plan["status"]=="off" else "planned" if plan else "empty"
        known=day in office
        delta=hours-office[day] if known and (closed or actual_off) and not opened else None
        comparison="in_progress" if opened else "office_missing" if not known else "personal_missing" if not closed and not actual_off else "matched" if abs(delta)<0.02 else "difference"
        cumulative+=earned
        monday=(d-timedelta(days=d.weekday())).isoformat()
        weekly[monday]+=hours
        rows.append({"day":day,"weekday":DAY_NAMES[d.weekday()],"weekend":d.weekday()>=5,
            "status":status,"status_label":STATUS[status],"records":records,"plan":plan,
            "actual_off":actual_off,"archived_shifts":actual_off_days.get(day,0),
            "plan_gross_hours":planned_gross,"plan_hours":planned_hours,"plan_pay":projected_pay,
            "future_plan":prospective,"closed_shifts":len(closed),"open":opened,
            "hours":hours if closed or actual_off else None,"earnings":earned if closed or actual_off else None,
            "gross_hours":sum(r["gross_hours"] for r in closed) if closed else None,
            "break_minutes":sum(r["break_minutes"] for r in closed) if closed else None,
            "office_hours":office.get(day),"delta_hours":delta,
            "comparison":comparison,"comparison_label":COMPARE[comparison],
            "cumulative_earned":round(cumulative,2)})
    all_closed=[r for day in rows for r in day["records"] if r["end"]]
    earned=round(sum(r["earnings"] for r in all_closed),2)
    parts=[]
    for part in (1,2):
        selected=[r for r in rows if (1 if int(r["day"][-2:])<=15 else 2)==part]
        adj=[r for r in adjustment_rows if (1 if int(r["day"][-2:])<=15 else 2)==part]
        addition=round(sum(float(r["amount_rub"])*(-1 if r["category"]=="deduction" else 1) for r in adj),2)
        base=round(sum(r["earnings"] or 0 for r in selected),2)
        parts.append({"period":part,"hours":sum(r["hours"] or 0 for r in selected),
            "shifts":sum(r["closed_shifts"] for r in selected),"earnings":base,
            "adjustments":addition,"total":round(base+addition,2),"received":round(payments[part],2)})
    comparable=[r for r in rows if r["delta_hours"] is not None]
    return {"month":month,"title":month_title(month),"generated_at":at.isoformat(),"days":rows,
       "summary":{"hours":sum(r["hours"] for r in all_closed),"shifts":len(all_closed),
           "gross_hours":sum(r["gross_hours"] for r in all_closed),
           "break_minutes":sum(r["break_minutes"] for r in all_closed),
           "earned":earned,"additional":extras,"received":round(sum(payments.values()),2),
           "total":round(earned+extras["net_adjustment"],2)},
       "periods":parts,"rates":[{"rate":r,**v} for r,v in sorted(rates.items())],
       "forecast":{"future_shifts":future_count,"future_hours":future_hours,"future_pay":round(future_pay,2),
           "estimated_total":round(earned+extras["net_adjustment"]+future_pay,2),
           "assumed_break_minutes":default_minutes,"excludes_open_shift":True},
       "reconciliation":{"confirmed_days":len(office),"compared_days":len(comparable),
           "differences":sum(r["comparison"]=="difference" for r in rows),
           "delta_hours":sum(r["delta_hours"] for r in comparable) if comparable else None,
           "missing_office_days":sum(r["closed_shifts"]>0 and r["office_hours"] is None for r in rows),
           "complete":False},
       "weeks":[{"label":date.fromisoformat(k).strftime("%d.%m"),"hours":round(v,3)} for k,v in sorted(weekly.items())],
       "adjustment_rows":adjustment_rows}