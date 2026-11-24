"""Private financial ledger: effective-dated rates and received payments.

No bank integration or external data transfer.
The historical shifts remain unchanged; rates take effect on the specified date.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
import sqlite3

MOSCOW=ZoneInfo("Europe/Moscow")

def _amount(value):
    try: v=Decimal(str(value).replace(",","."))
    except Exception as e: raise ValueError("Проверь сумму") from e
    if not v.is_finite() or v<=0 or v>Decimal("1000000"):raise ValueError("Сумма вне допустимого диапазона")
    result=v.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    if result<Decimal("0.01"):raise ValueError("Минимальная сумма — 0,01 ₽")
    return result

def ensure(db, base_rate=300):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS rate_history (
      effective_from TEXT PRIMARY KEY,
      rate_rub TEXT NOT NULL,
      created_utc TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS received_payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      work_month TEXT NOT NULL,
      period INTEGER NOT NULL CHECK(period IN (1,2)),
      amount_rub TEXT NOT NULL,
      paid_on TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT '',
      created_utc TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS payments_period_idx
        ON received_payments(work_month,period);
    """)
    previous=db.execute("SELECT COUNT(*) FROM rate_history").fetchone()[0]
    if previous==0:
        db.execute("INSERT INTO rate_history(effective_from,rate_rub,created_utc,note) VALUES(?,?,?,?)",
             ("1970-01-01",str(_amount(base_rate)),datetime.now(MOSCOW).isoformat(),"Начальная ставка при переносе системы"))

def rate_at(db,day):
    row=db.execute("SELECT rate_rub FROM rate_history WHERE effective_from<=? ORDER BY effective_from DESC LIMIT 1",(day,)).fetchone()
    if row:return float(row["rate_rub"])
    return 300.0

def current_rate(db):
    day=datetime.now(MOSCOW).date().isoformat()
    return rate_at(db,day)

def planned_rates(db):
    today=datetime.now(MOSCOW).date().isoformat()
    return db.execute("SELECT effective_from,rate_rub FROM rate_history WHERE effective_from>? ORDER BY effective_from",(today,)).fetchall()

def shift_earnings(db,shift):
    if shift["end_utc"] is None:return 0.0
    from core import paid_hours,local_day
    hrs=Decimal(str(paid_hours(shift)))
    amount=hrs*Decimal(str(rate_at(db,local_day(shift["start_utc"]))))
    return float(amount.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))

def schedule_rate(db,value,effective_from=None):
    from core import log
    rate=_amount(value)
    today=datetime.now(MOSCOW).date()
    target=effective_from or (today+timedelta(days=1)).isoformat()
    try:date=datetime.strptime(target,"%Y-%m-%d").date()
    except Exception as e:raise ValueError("Неверная дата") from e
    if date<today+timedelta(days=1):
        raise ValueError("Ставку можно менять только на будущие даты, чтобы не переписать прошлые начисления")
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""INSERT INTO rate_history(effective_from,rate_rub,created_utc,note)
          VALUES(?,?,?,?) ON CONFLICT(effective_from)
          DO UPDATE SET rate_rub=excluded.rate_rub,created_utc=excluded.created_utc,note=excluded.note""",
          (date.isoformat(),str(rate),datetime.now(MOSCOW).isoformat(),"Изменено владельцем"))
        log(db,"future_rate",details={"effective_from":date.isoformat(),"rate":str(rate)})
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return date.isoformat()

def add_payment(db,work_month,period,amount,paid_on,note=""):
    from core import log
    if not (len(work_month)==7 and work_month[4]=="-" and work_month[:4].isdigit()):
        raise ValueError("Формат месяца: 2026-11")
    try:
        datetime.strptime(work_month,"%Y-%m")
        datetime.strptime(paid_on,"%Y-%m-%d")
    except ValueError as exc:raise ValueError("Проверь дату и месяц") from exc
    if period not in (1,2):raise ValueError("Выбери первую или вторую выплату")
    amount=_amount(amount)
    if len(note)>150:raise ValueError("Слишком длинное примечание")
    db.execute("BEGIN IMMEDIATE")
    try:
        cur=db.execute("""INSERT INTO received_payments(work_month,period,amount_rub,paid_on,note,created_utc)
          VALUES(?,?,?,?,?,?)""",(work_month,period,str(amount),paid_on,note,datetime.now(MOSCOW).isoformat()))
        log(db,"payment_received",details={"id":cur.lastrowid,"work_month":work_month,"period":period,
             "amount":str(amount),"paid_on":paid_on})
        db.execute("COMMIT")
        return cur.lastrowid
    except Exception:
        db.execute("ROLLBACK")
        raise

def payment_totals(db,month):
    rows=db.execute("SELECT period,amount_rub FROM received_payments WHERE work_month=?",(month,)).fetchall()
    return {1:sum(float(x["amount_rub"]) for x in rows if x["period"]==1),
            2:sum(float(x["amount_rub"]) for x in rows if x["period"]==2)}

def integrity_summary(db):
    missing_end=db.execute("SELECT COUNT(*) FROM shifts WHERE end_utc IS NULL").fetchone()[0]
    zero_hours=db.execute("""SELECT COUNT(*) FROM shifts WHERE end_utc IS NOT NULL
        AND end_utc<=start_utc""").fetchone()[0]
    office=db.execute("SELECT count(*) FROM office_days").fetchone()[0]
    total=db.execute("SELECT count(*) FROM shifts").fetchone()[0]
    return {"open":missing_end,"invalid_dates":zero_hours,"office":office,"shifts":total}
