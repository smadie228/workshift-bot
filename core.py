"""Локальное хранилище смен. Время событий — UTC, отчёты — Europe/Moscow."""
import json
import os
import sqlite3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
UTC = timezone.utc
DATA_DIR = Path(os.getenv("WORKSHIFTS_DATA", "/var/lib/workshift-bot"))
DB_PATH = DATA_DIR / "shifts.sqlite3"

def connect(path=None):
    target = Path(path) if path else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS shifts(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         start_utc TEXT NOT NULL,
         end_utc TEXT,
         break_minutes INTEGER NOT NULL DEFAULT 30 CHECK(break_minutes BETWEEN 0 AND 600),
         origin TEXT NOT NULL DEFAULT 'button',
         created_utc TEXT NOT NULL,
         updated_utc TEXT NOT NULL,
         note TEXT NOT NULL DEFAULT ''
      );
      CREATE UNIQUE INDEX IF NOT EXISTS only_one_open_shift
         ON shifts((1)) WHERE end_utc IS NULL;
      CREATE TABLE IF NOT EXISTS office_days(
         day TEXT PRIMARY KEY, hours REAL NOT NULL CHECK(hours >= 0 AND hours <= 24),
         source TEXT NOT NULL, updated_utc TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS events(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         event_utc TEXT NOT NULL, kind TEXT NOT NULL, shift_id INTEGER,
         details TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS processed_updates(
         update_id INTEGER PRIMARY KEY, event_utc TEXT NOT NULL
      );
    """)
    from finance import ensure
    from history_schema import ensure_history
    ensure(db, float(config(db, "rate", "300")))
    ensure_history(db)
    return db

def now():
    return datetime.now(UTC)

def iso(dt):
    return dt.astimezone(UTC).isoformat(timespec="seconds")

def read_time(s):
    return datetime.fromisoformat(s).astimezone(TZ)

def local_day(s):
    return read_time(s).date().isoformat()

def config(db, key, default=None):
    r = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def put_config(db, key, value):
    db.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))

def rate(db):
    from finance import current_rate
    return current_rate(db)

def default_break(db):
    return int(config(db, "default_break_minutes", "30"))

def open_shift(db):
    return db.execute("SELECT * FROM shifts WHERE end_utc IS NULL LIMIT 1").fetchone()

def log(db, kind, shift_id=None, details=None):
    db.execute("INSERT INTO events(event_utc,kind,shift_id,details) VALUES(?,?,?,?)",
               (iso(now()), kind, shift_id, json.dumps(details or {}, ensure_ascii=False)))

def toggle(db, update_id, time=None):
    """Атомарный переход + идемпотентность Telegram update_id."""
    at = time or now()
    db.execute("BEGIN IMMEDIATE")
    try:
        old = db.execute("SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,)).fetchone()
        if old:
            db.execute("COMMIT")
            return "duplicate", None
        r = open_shift(db)
        if r:
            start = datetime.fromisoformat(r["start_utc"])
            if at <= start:
                raise ValueError("Время окончания не может предшествовать приходу")
            if (at-start).total_seconds()>24*3600:
                raise ValueError("Смена открыта дольше 24 часов. Укажи корректное время ухода через «Исправить».")
            db.execute("UPDATE shifts SET end_utc=?,updated_utc=? WHERE id=?",
                       (iso(at), iso(at), r["id"]))
            action, sid = "out", r["id"]
        else:
            cur = db.execute(
                "INSERT INTO shifts(start_utc,break_minutes,created_utc,updated_utc) VALUES(?,?,?,?)",
                (iso(at), default_break(db), iso(at), iso(at)))
            action, sid = "in", cur.lastrowid
        log(db, action, sid, {"telegram_update_id": update_id, "at": iso(at)})
        db.execute("INSERT INTO processed_updates(update_id,event_utc) VALUES(?,?)",
                   (update_id, iso(at)))
        db.execute("COMMIT")
        return action, db.execute("SELECT * FROM shifts WHERE id=?", (sid,)).fetchone()
    except Exception:
        db.execute("ROLLBACK")
        raise

def paid_hours(shift):
    if shift["end_utc"] is None: return 0
    start = datetime.fromisoformat(shift["start_utc"])
    end = datetime.fromisoformat(shift["end_utc"])
    hours = (end - start).total_seconds() / 3600.0 - shift["break_minutes"] / 60.0
    return max(0.0, hours)

def shifts(db, month=None, limit=None):
    rows = db.execute("SELECT * FROM shifts ORDER BY start_utc DESC").fetchall()
    if month:
        rows = [r for r in rows if local_day(r["start_utc"]).startswith(month)]
    return rows[:limit] if limit else rows

def totals(db, month=None, period=None):
    rows = shifts(db, month)
    if period is not None:
        rows = [r for r in rows if (1 if int(local_day(r["start_utc"])[8:]) <= 15 else 2) == period]
    completed = [r for r in rows if r["end_utc"]]
    hours = sum(paid_hours(r) for r in completed)
    from finance import shift_earnings
    return {"hours": hours, "earnings": round(sum(shift_earnings(db,r) for r in completed), 2),
            "shifts": len(completed), "open": sum(not r["end_utc"] for r in rows)}

def week_totals(db, reference=None):
    today = reference or datetime.now(TZ).date()
    monday = today - timedelta(days=today.weekday())
    rows = [r for r in shifts(db) if monday <= date.fromisoformat(local_day(r["start_utc"])) < monday + timedelta(days=7)]
    hours = sum(paid_hours(r) for r in rows)
    from finance import shift_earnings
    completed = [r for r in rows if r["end_utc"]]
    return monday, round(hours, 2), round(sum(shift_earnings(db,r) for r in completed), 2), len(completed)

def office_report(db, month=None):
    rows = db.execute("SELECT * FROM office_days ORDER BY day DESC").fetchall()
    if month:
        rows = [r for r in rows if r["day"].startswith(month)]
    all_shifts = shifts(db)
    ours = {}
    for s in all_shifts:
        if s["end_utc"]:
            d = local_day(s["start_utc"])
            ours[d] = ours.get(d, 0) + paid_hours(s)
    report = []
    for r in rows:
        own = ours.get(r["day"])
        report.append((r["day"], r["hours"], own, None if own is None else own - r["hours"]))
    return report

def set_office(db, day, hours, source="office"):
    if hours < 0 or hours > 24:
        raise ValueError("Часы офиса должны быть в диапазоне 0–24")
    db.execute("BEGIN IMMEDIATE")
    try:
        old = db.execute("SELECT hours FROM office_days WHERE day=?", (day,)).fetchone()
        db.execute("""INSERT INTO office_days(day,hours,source,updated_utc) VALUES(?,?,?,?)
           ON CONFLICT(day) DO UPDATE SET hours=excluded.hours,source=excluded.source,updated_utc=excluded.updated_utc""",
           (day, hours, source, iso(now())))
        log(db, "office", None, {"day": day, "old": old["hours"] if old else None, "new": hours, "source": source})
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

def parse_local_datetime(day, hhmm):
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ).astimezone(UTC)

def corrected_shift(db, day, start_hhmm, end_hhmm, minutes=None, sid=None):
    start = parse_local_datetime(day, start_hhmm)
    end = parse_local_datetime(day, end_hhmm)
    if end <= start:
        end += timedelta(days=1)
    duration = (end - start).total_seconds() / 3600
    if end > now()+timedelta(minutes=3):
        raise ValueError("Нельзя записать завершённую смену с уходом из будущего")
    if duration > 24:
        raise ValueError("Смена не может продолжаться более 24 часов")
    brk = default_break(db) if minutes is None else int(minutes)
    if not (0 <= brk <= 600) or brk >= duration * 60:
        raise ValueError("Проверь перерыв")
    db.execute("BEGIN IMMEDIATE")
    try:
        overlap = db.execute("""SELECT id FROM shifts WHERE start_utc < ?
             AND (end_utc IS NULL OR end_utc > ?) AND (? IS NULL OR id != ?)
             LIMIT 1""",(iso(end),iso(start),sid,sid)).fetchone()
        if overlap:
            raise ValueError("Время пересекается с другой сменой. Проверь даты и часы.")
        if sid is None:
            existing = db.execute("SELECT id FROM shifts WHERE start_utc >= ? AND start_utc < ?",
                                  (iso(parse_local_datetime(day,"00:00")), iso(parse_local_datetime(day,"00:00")+timedelta(days=1)))).fetchall()
            if existing:
                raise ValueError("В этот день уже есть смена. Используй /fix с номером смены.")
            cur = db.execute("""INSERT INTO shifts(start_utc,end_utc,break_minutes,origin,created_utc,updated_utc)
                VALUES(?,?,?,'manual',?,?)""", (iso(start),iso(end),brk,iso(now()),iso(now())))
            sid = cur.lastrowid
            kind = "manual"
            old = None
        else:
            r = db.execute("SELECT * FROM shifts WHERE id=?", (sid,)).fetchone()
            if not r:
                raise ValueError("Смена не найдена")
            if not r["end_utc"]:
                raise ValueError("Текущую открытую смену нельзя менять через /fix")
            old = dict(r)
            db.execute("UPDATE shifts SET start_utc=?,end_utc=?,break_minutes=?,updated_utc=? WHERE id=?",
                       (iso(start),iso(end),brk,iso(now()),sid))
            kind = "fix"
        log(db, kind, sid, {"old":old, "day":day,"start":start_hhmm,"end":end_hhmm,"break":brk})
        db.execute("COMMIT")
        return sid
    except Exception:
        db.execute("ROLLBACK")
        raise


def close_open_shift(db,sid,end_hhmm,break_minutes=None):
    """Close a forgotten active shift with an audited, owner-confirmed time."""
    db.execute("BEGIN IMMEDIATE")
    try:
        row=db.execute("SELECT * FROM shifts WHERE id=? AND end_utc IS NULL",(sid,)).fetchone()
        if not row:raise ValueError("Смена уже закрыта или не найдена")
        start=datetime.fromisoformat(row["start_utc"])
        end=parse_local_datetime(local_day(row["start_utc"]),end_hhmm)
        if end<=start:end+=timedelta(days=1)
        minutes=row["break_minutes"] if break_minutes is None else int(break_minutes)
        duration=(end-start).total_seconds()/60
        if not 0<duration<=24*60:raise ValueError("Проверь время: смена должна быть не длиннее 24 часов")
        if not 0<=minutes<duration:raise ValueError("Перерыв не должен превышать длительность смены")
        if end>now()+timedelta(minutes=3):
            raise ValueError("Время ухода не может быть в будущем")
        conflict=db.execute("""SELECT id FROM shifts WHERE id!=?
            AND start_utc<? AND (end_utc IS NULL OR end_utc>?) LIMIT 1""",
            (sid,iso(end),iso(start))).fetchone()
        if conflict:raise ValueError("Эти часы пересекаются с другой сменой")
        db.execute("UPDATE shifts SET end_utc=?,break_minutes=?,updated_utc=? WHERE id=?",
                   (iso(end),minutes,iso(now()),sid))
        log(db,"late_close",sid,{"end_utc":iso(end),"break_minutes":minutes,
                                  "original_start":row["start_utc"]})
        db.execute("COMMIT")
        return sid
    except Exception:
        db.execute("ROLLBACK")
        raise
