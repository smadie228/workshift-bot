"""One-time, idempotent import of source workbook rows and screenshot evidence.

Never overwrites button-created shifts or office numeric totals.
The official office photo is preserved as a reference, NOT converted into unverified hours.
"""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from core import connect,now,iso,log,local_day,DATA_DIR

TZ=ZoneInfo("Europe/Moscow")
OCTOBER="rabota_zarplata_umnyi_nedelnyi_kontrol_v3_galochki_ispravleno.xlsx"
NOVEMBER="uchet_zarplaty_2026_provereno_yarkii_dizain.xlsx"

def create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS history_source_rows (
        source TEXT NOT NULL,
        sheet TEXT NOT NULL,
        excel_row INTEGER NOT NULL,
        day TEXT NOT NULL,
        weekday TEXT,
        status TEXT,
        start_time TEXT,
        end_time TEXT,
        break_minutes INTEGER,
        hours REAL,
        earnings REAL,
        checked TEXT,
        imported_utc TEXT NOT NULL,
        PRIMARY KEY(source,sheet,excel_row)
    );
    CREATE INDEX IF NOT EXISTS history_source_day
        ON history_source_rows(day);
    CREATE TABLE IF NOT EXISTS history_source_assets(
        name TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL,
        source_size INTEGER,
        kind TEXT,
        metadata_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS history_source_summaries(
        source TEXT NOT NULL,
        sheet TEXT NOT NULL,
        data_json TEXT NOT NULL,
        PRIMARY KEY(source,sheet)
    );
    CREATE TABLE IF NOT EXISTS historical_evidence(
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        event_date TEXT NOT NULL,
        time_text TEXT,
        role TEXT NOT NULL,
        confidence TEXT NOT NULL,
        UNIQUE(source,event_date,time_text,role)
    );
    CREATE TABLE IF NOT EXISTS office_image_days(
        day TEXT PRIMARY KEY,
        image_source TEXT NOT NULL,
        office_hours REAL,
        parse_status TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS historical_imported_shifts(
        day TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        sheet TEXT NOT NULL,
        excel_row INTEGER NOT NULL,
        shift_id INTEGER NOT NULL UNIQUE,
        FOREIGN KEY(shift_id) REFERENCES shifts(id)
    );
    """)
    db.execute("PRAGMA foreign_keys=ON")

def parsed_time(day,clock):
    return datetime.strptime(day+" "+clock,"%Y-%m-%d %H:%M").replace(tzinfo=TZ)

def apply_import(db,bundle):
    create_tables(db)
    if int(bundle.get("schema",0))!=1:
        raise ValueError("Unsupported historical import schema")
    added={"source_rows":0,"evidence":0,"office_dates":0,"historical_shifts":0,
           "source_assets":0,"summaries":0,"conflicts":0}
    db.execute("BEGIN IMMEDIATE")
    try:
        for a in bundle["assets"]:
            cur=db.execute("""INSERT INTO history_source_assets(name,sha256,source_size,kind,metadata_json)
              VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                sha256=excluded.sha256,source_size=excluded.source_size,kind=excluded.kind,
                metadata_json=excluded.metadata_json""",
              (a["name"],a["sha256"],a["bytes"],a["kind"],json.dumps(a,ensure_ascii=False)))
            added["source_assets"]+=1
        for a in bundle["summaries"]:
            db.execute("""INSERT INTO history_source_summaries(source,sheet,data_json)
              VALUES(?,?,?) ON CONFLICT(source,sheet) DO NOTHING""",
              (a["source"],a["sheet"],json.dumps(a["rows"],ensure_ascii=False)))
            added["summaries"]+=1
        for row in bundle["rows"]:
            cur=db.execute("""INSERT INTO history_source_rows
                (source,sheet,excel_row,day,weekday,status,start_time,end_time,break_minutes,hours,earnings,checked,imported_utc)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,sheet,excel_row) DO NOTHING""",
                (row["source"],row["sheet"],row["excel_row"],row["day"],row["weekday"],
                 row["status"],row["start"],row["end"],row["break_minutes"],
                 row["hours"],row["earnings"],row["checked"],iso(now())))
            added["source_rows"]+=cur.rowcount
        for e in bundle["screenshot_times"]:
            cur=db.execute("""INSERT INTO historical_evidence(source,event_date,time_text,role,confidence)
                VALUES(?,?,?,?,?) ON CONFLICT(source,event_date,time_text,role) DO NOTHING""",
                (e["source"],e["date"],e["time_text"],e["role"],e["confidence"]))
            added["evidence"]+=cur.rowcount
        for e in bundle["office_days"]:
            cur=db.execute("""INSERT INTO office_image_days(day,image_source,office_hours,parse_status)
                VALUES(?,?,NULL,?) ON CONFLICT(day) DO NOTHING""",
                (e["day"],e["source"],e["status"]))
            added["office_dates"]+=cur.rowcount
        chosen={}
        for row in bundle["rows"]:
            if row["status"]!="Работал":continue
            day=row["day"]
            if row["source"]==OCTOBER and day<"2026-11-01":
                chosen[day]=row
            if row["source"]==NOVEMBER and day>="2026-11-01":
                chosen[day]=row
        off_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='actual_days_off'").fetchone()
        actual_off_days={r[0] for r in db.execute("SELECT day FROM actual_days_off")} if off_table else set()
        for day,row in sorted(chosen.items()):
            from attendance_recovery import deleted_import_days
            if day in actual_off_days or day in deleted_import_days(db):
                added["conflicts"]+=1;continue
            if not row["start"] or not row["end"]:
                added["conflicts"]+=1;continue
            known=db.execute("SELECT 1 FROM historical_imported_shifts WHERE day=?",(day,)).fetchone()
            if known:continue
            others=db.execute("""SELECT id FROM shifts
                 WHERE substr(datetime(start_utc,'+3 hours'),1,10)=? LIMIT 1""",(day,)).fetchone()
            if others:
                added["conflicts"]+=1;continue
            start=parsed_time(day,row["start"])
            end=parsed_time(day,row["end"])
            if end<=start:end+=timedelta(days=1)
            duration=int(round((end-start).total_seconds()/60))
            mins=int(row["break_minutes"] or 0)
            if duration<=0 or duration>24*60 or mins>=duration:
                added["conflicts"]+=1;continue
            note=json.dumps({"source_file":row["source"],"sheet":row["sheet"],
                  "excel_row":row["excel_row"],"import_kind":"historical_excel_user_entry",
                  "original_status":row["status"],"confirmed_checkbox":row["checked"],
                  "source_hours":row["hours"],"source_earnings":row["earnings"],
                  "break_omitted":row["break_minutes"] is None},ensure_ascii=False)
            cur=db.execute("""INSERT INTO shifts
                (start_utc,end_utc,break_minutes,origin,created_utc,updated_utc,note)
                VALUES(?,?,?,'import_excel',?,?,?)""",
                (iso(start),iso(end),mins,iso(now()),iso(now()),note))
            db.execute("""INSERT INTO historical_imported_shifts
                (day,source,sheet,excel_row,shift_id) VALUES(?,?,?,?,?)""",
                (day,row["source"],row["sheet"],row["excel_row"],cur.lastrowid))
            added["historical_shifts"]+=1
        log(db,"historical_import",None,added)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return added

def main():
    source=Path(os.environ.get("HISTORICAL_SOURCE_FILE",str(DATA_DIR/"historical_import_bundle.json")))
    with source.open("r",encoding="utf-8") as f:
        bundle=json.load(f)
    db=connect()
    try:
        added=apply_import(db,bundle)
        print("IMPORT_COUNTS",json.dumps(added,ensure_ascii=False))
        print("SHIFTS_TOTAL",db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0])
        print("MONTHS",list(db.execute(
            "SELECT substr(start_utc,1,7),count(*) FROM shifts GROUP BY 1 ORDER BY 1")))
    finally:db.close()

if __name__=="__main__":main()
