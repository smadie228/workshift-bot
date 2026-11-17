"""Create optional history tables on a clean installation, without private import files."""
def ensure_history(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS history_source_rows(
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
    CREATE INDEX IF NOT EXISTS history_source_day ON history_source_rows(day);
    CREATE TABLE IF NOT EXISTS history_source_assets(
        name TEXT PRIMARY KEY,sha256 TEXT NOT NULL,source_size INTEGER,
        kind TEXT,metadata_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS history_source_summaries(
        source TEXT NOT NULL,sheet TEXT NOT NULL,data_json TEXT NOT NULL,
        PRIMARY KEY(source,sheet)
    );
    CREATE TABLE IF NOT EXISTS historical_evidence(
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,event_date TEXT NOT NULL,time_text TEXT,
        role TEXT NOT NULL,confidence TEXT NOT NULL,
        UNIQUE(source,event_date,time_text,role)
    );
    CREATE TABLE IF NOT EXISTS office_image_days(
        day TEXT PRIMARY KEY,image_source TEXT NOT NULL,
        office_hours REAL,parse_status TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS historical_imported_shifts(
        day TEXT PRIMARY KEY,source TEXT NOT NULL,sheet TEXT NOT NULL,
        excel_row INTEGER NOT NULL,shift_id INTEGER NOT NULL UNIQUE,
        FOREIGN KEY(shift_id) REFERENCES shifts(id)
    );
    """)
