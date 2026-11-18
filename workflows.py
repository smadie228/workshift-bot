"""Private work-shift workflow extensions.

Actual break events, manually approved schedule, and pay corrections are distinct
from historical imports and official office confirmations.
All writes are validated, transactional and audited. No external service calls.
"""
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from core import TZ, now, iso, local_day, open_shift, log, paid_hours, shifts

def ensure(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS tracked_break_shifts(
        shift_id INTEGER PRIMARY KEY REFERENCES shifts(id),
        created_utc TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS break_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id INTEGER NOT NULL REFERENCES shifts(id),
        started_utc TEXT NOT NULL,
        ended_utc TEXT,
        minutes INTEGER,
        created_utc TEXT NOT NULL
      );
      CREATE UNIQUE INDEX IF NOT EXISTS only_one_break
        ON break_sessions(shift_id) WHERE ended_utc IS NULL;
      CREATE TABLE IF NOT EXISTS plan_days(
        day TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK(status IN ('shift','off')),
        start_time TEXT,
        end_time TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS pay_adjustments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        category TEXT NOT NULL CHECK(category IN ('bonus','allowance','reimbursement','deduction')),
        amount_rub TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_utc TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS pay_adjustments_day ON pay_adjustments(day);
    """)

def _validate_day(s):
    if not isinstance(s,str) or len(s)!=10:
        raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД")
    try:
        val=datetime.strptime(s,"%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Неправильная дата") from exc
    if not 2000<=val.year<=2100 or val.isoformat()!=s:
        raise ValueError("Неправильная дата")
    return val

def active_break(db,shift_id=None):
    shift_id=shift_id or (open_shift(db) or {"id":None})["id"]
    if shift_id is None:return None
    return db.execute("SELECT * FROM break_sessions WHERE shift_id=? AND ended_utc IS NULL",
                      (shift_id,)).fetchone()

def break_status(db):
    s=open_shift(db)
    if not s:return {"active":False,"on_break":False,"tracked":False,"minutes":0}
    running=active_break(db,s["id"])
    tracked=db.execute("SELECT 1 FROM tracked_break_shifts WHERE shift_id=?",(s["id"],)).fetchone()
    return {"active":True,"on_break":running is not None,"tracked":bool(tracked),
            "minutes":s["break_minutes"],"started_utc":s["start_utc"],
            "break_started_utc":running["started_utc"] if running else None,
            "break_id":running["id"] if running else None}

def start_break(db,at=None,*,manage_transaction=True):
    at=at or now()
    if not manage_transaction and not db.in_transaction:
        raise RuntimeError("An outer write transaction is required")
    if manage_transaction:db.execute("BEGIN IMMEDIATE")
    try:
        s=open_shift(db)
        if s is None:raise ValueError("Сначала отметь приход на работу.")
        if s["end_utc"]:raise ValueError("Смена уже закрыта.")
        start=datetime.fromisoformat(s["start_utc"])
        if at<start:raise ValueError("Время перерыва раньше начала смены.")
        if (at-start).total_seconds()>24*3600:
            raise ValueError("Смена открыта больше суток. Сначала уточни время.")
        if active_break(db,s["id"]):raise ValueError("Перерыв уже идёт.")
        tracked=db.execute("SELECT 1 FROM tracked_break_shifts WHERE shift_id=?",(s["id"],)).fetchone()
        if not tracked:
            db.execute("INSERT INTO tracked_break_shifts(shift_id,created_utc) VALUES(?,?)",
                       (s["id"],iso(at)))
            # First use switches from a pre-filled estimate to actual tracked breaks.
            db.execute("UPDATE shifts SET break_minutes=0,updated_utc=? WHERE id=?",
                       (iso(at),s["id"]))
            log(db,"break_tracking_enabled",s["id"],
                {"previous_estimate_minutes":s["break_minutes"]})
        db.execute("""INSERT INTO break_sessions(shift_id,started_utc,created_utc)
            VALUES(?,?,?)""",(s["id"],iso(at),iso(at)))
        log(db,"break_start",s["id"],{"at":iso(at)})
        if manage_transaction:db.execute("COMMIT")
        return s["id"]
    except Exception:
        if manage_transaction and db.in_transaction:db.execute("ROLLBACK")
        raise

def stop_break(db,at=None,*,manage_transaction=True):
    at=at or now()
    if not manage_transaction and not db.in_transaction:
        raise RuntimeError("An outer write transaction is required")
    if manage_transaction:db.execute("BEGIN IMMEDIATE")
    try:
        s=open_shift(db)
        if not s:raise ValueError("Открытой смены нет.")
        running=active_break(db,s["id"])
        if not running:raise ValueError("Сейчас перерыв не идёт.")
        start=datetime.fromisoformat(running["started_utc"])
        span=(at-start).total_seconds()
        if span<0:raise ValueError("Некорректное время завершения перерыва.")
        if span>24*3600:raise ValueError("Перерыв открыт дольше суток. Требуется исправление.")
        minutes=math.ceil(span/60) if span>0 else 0
        total=s["break_minutes"]+minutes
        duration=(at-datetime.fromisoformat(s["start_utc"])).total_seconds()/60
        if total>600 or total>duration:
            raise ValueError("Слишком большой перерыв. Уточни время вручную.")
        db.execute("UPDATE break_sessions SET ended_utc=?,minutes=? WHERE id=?",
                   (iso(at),minutes,running["id"]))
        db.execute("UPDATE shifts SET break_minutes=?,updated_utc=? WHERE id=?",
                   (total,iso(at),s["id"]))
        log(db,"break_end",s["id"],{"minutes":minutes,"total":total})
        if manage_transaction:db.execute("COMMIT")
        return {"shift_id":s["id"],"minutes":minutes,"total":total}
    except Exception:
        if manage_transaction and db.in_transaction:db.execute("ROLLBACK")
        raise

def plan_day(db,day):
    _validate_day(day)
    manual=db.execute("SELECT * FROM plan_days WHERE day=?",(day,)).fetchone()
    if manual:
        return {"day":day,"status":manual["status"],
                "start":manual["start_time"],"end":manual["end_time"],"source":"manual"}
    entry=db.execute("""SELECT status,start_time,end_time FROM history_source_rows
         WHERE day=? AND status IN ('План','Выходной')
         ORDER BY imported_utc DESC,source DESC LIMIT 1""",(day,)).fetchone()
    if entry:
        return {"day":day,"status":"off" if entry["status"]=="Выходной" else "shift",
                "start":entry["start_time"],"end":entry["end_time"],"source":"import"}
    return None

def set_plan(db,day,status,start_time=None,end_time=None,note=""):
    target=_validate_day(day)
    if target<datetime.now(TZ).date():
        raise ValueError("План можно менять только для сегодняшнего и будущих дней.")
    if status not in ("shift","off"):raise ValueError("Некорректный статус.")
    if len(note)>160:raise ValueError("Слишком длинное примечание.")
    if status=="shift":
        if not start_time or not end_time:raise ValueError("Укажи время начала и окончания.")
        try:
            datetime.strptime(start_time,"%H:%M")
            datetime.strptime(end_time,"%H:%M")
        except ValueError as exc:
            raise ValueError("Время в формате ЧЧ:ММ.") from exc
        if start_time==end_time:raise ValueError("Приход и уход не могут совпадать.")
    else:
        start_time=end_time=None
    db.execute("BEGIN IMMEDIATE")
    try:
        old=plan_day(db,day)
        stamp=iso(now())
        db.execute("""INSERT INTO plan_days(day,status,start_time,end_time,note,created_utc,updated_utc)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET
            status=excluded.status,start_time=excluded.start_time,
            end_time=excluded.end_time,note=excluded.note,updated_utc=excluded.updated_utc""",
            (day,status,start_time,end_time,note,stamp,stamp))
        log(db,"plan_change",None,{"day":day,"before":old,"after":{"status":status,
              "start":start_time,"end":end_time}})
        db.execute("COMMIT")
        return plan_day(db,day)
    except Exception:
        db.execute("ROLLBACK")
        raise

def upcoming_plan(db,start=None,limit=14):
    start=start or datetime.now(TZ).date().isoformat()
    _validate_day(start)
    rows=db.execute("""SELECT day FROM plan_days WHERE day>=?
        UNION SELECT day FROM history_source_rows WHERE day>=?
        AND status IN ('План','Выходной')
        ORDER BY day LIMIT ?""",(start,start,limit*3)).fetchall()
    return [p for r in rows if (p:=plan_day(db,r["day"])) is not None][:limit]

def add_adjustment(db,day,category,amount,note=""):
    _validate_day(day)
    if category not in ("bonus","allowance","reimbursement","deduction"):
        raise ValueError("Выбери допустимый вид выплаты.")
    if len(note)>160:raise ValueError("Слишком длинный комментарий.")
    try:
        value=Decimal(str(amount).replace(",","."))
    except (InvalidOperation,ValueError) as exc:
        raise ValueError("Некорректная сумма.") from exc
    if not value.is_finite() or value<=0 or value>1000000:
        raise ValueError("Сумма должна быть от 0,01 до 1 000 000 ₽.")
    value=value.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    db.execute("BEGIN IMMEDIATE")
    try:
        cur=db.execute("""INSERT INTO pay_adjustments(day,category,amount_rub,note,created_utc)
          VALUES(?,?,?,?,?)""",(day,category,str(value),note,iso(now())))
        log(db,"adjustment_added",None,{"id":cur.lastrowid,"day":day,"category":category,
                "amount":str(value)})
        db.execute("COMMIT")
        return cur.lastrowid
    except Exception:
        db.execute("ROLLBACK")
        raise

def adjustment_totals(db,month):
    if len(month)!=7:raise ValueError("Неверный месяц")
    datetime.strptime(month,"%Y-%m")
    sums={"bonus":Decimal("0"),"allowance":Decimal("0"),
          "reimbursement":Decimal("0"),"deduction":Decimal("0")}
    for r in db.execute("SELECT category,amount_rub FROM pay_adjustments WHERE day LIKE ?",(month+"-%",)):
        sums[r["category"]]+=Decimal(r["amount_rub"])
    sums["net_adjustment"]=sums["bonus"]+sums["allowance"]+sums["reimbursement"]-sums["deduction"]
    return {k:float(v) for k,v in sums.items()}

def issues(db,reference=None):
    """Return actionable anomalies, not unverified guesses."""
    today=reference or datetime.now(TZ).date()
    result=[]
    s=open_shift(db)
    if s:
        start=datetime.fromisoformat(s["start_utc"])
        hours=(now()-start).total_seconds()/3600
        if hours>13:
            result.append({"level":"warning","day":local_day(s["start_utc"]),
                "text":f"Открытая смена длится {hours:.1f} ч. Проверь уход."})
        if active_break(db,s["id"]):
            age=(now()-datetime.fromisoformat(active_break(db,s["id"])["started_utc"])).total_seconds()/60
            if age>120:
                result.append({"level":"warning","day":local_day(s["start_utc"]),
                    "text":"Перерыв длится более двух часов. Проверь завершение."})
    from datetime import timedelta
    recent=shifts(db)
    by_day={}
    for rec in recent:
        if rec["end_utc"] and local_day(rec["start_utc"])>=str(today-timedelta(days=30)):
            day=local_day(rec["start_utc"])
            by_day[day]=by_day.get(day,0)+paid_hours(rec)
            if paid_hours(rec)>14:
                result.append({"level":"review","day":day,
                    "text":"Длительная смена, проверь корректность времени."})
    for offset in range(1,8):
        d=(today-timedelta(days=offset)).isoformat()
        plan=plan_day(db,d)
        if plan and plan["status"]=="shift" and d not in by_day:
            result.append({"level":"review","day":d,
                "text":"По плану был рабочий день, но фактическая смена не найдена."})
    return result[:20]
