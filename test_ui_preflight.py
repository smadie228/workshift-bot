"""Read-only database invariant checks for the redesigned Telegram interface."""
import html
import zipfile
import sqlite3
import os
from pathlib import Path
from core import connect
from friendly_ui import VIEWS
from report_catalog import EXPORTS

def snapshot(db):
    return (
        db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM history_source_rows").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM office_days").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
    )

db=connect()
before=snapshot(db)
print("BEFORE",before)
assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
for name,view in VIEWS.items():
    text,markup=view(db)
    assert 1<len(text)<4000, (name,len(text))
    assert markup.inline_keyboard
    assert all(len(b.callback_data or "")<=64 for row in markup.inline_keyboard for b in row)
    print("SCREEN",name,len(text),"buttons",sum(len(row) for row in markup.inline_keyboard))
for kind,render in EXPORTS.items():
    p=render(db)
    with zipfile.ZipFile(p) as z:
        assert z.testzip() is None, (kind,"corrupt")
        assert "[Content_Types].xml" in z.namelist()
        data=z.read("xl/workbook.xml")
        assert b"sheet" in data
    print("FILE",kind,p.stat().st_size)
after=snapshot(db)
print("AFTER",after)
assert before==after, "Unexpected modification of shift data"
print("UX_PREFLIGHT_OK")
db.close()
