"""Safe staging checks on a copied real database; no external API requests."""
from pathlib import Path
from openpyxl import load_workbook
import core
import report
import report_catalog
import friendly_ui
import pro_assistant

db=core.connect()
pre=db.execute("SELECT COUNT(*),COUNT(end_utc) FROM shifts").fetchone()
source=db.execute("SELECT COUNT(*) FROM history_source_rows").fetchone()[0]
check=db.execute("PRAGMA integrity_check").fetchone()[0]
assert check=="ok"
print("PRE",tuple(pre),"SOURCE",source)
print("RATES",[(r["effective_from"],r["rate_rub"]) for r in db.execute("SELECT * FROM rate_history")])
for month in ("2026-10","2026-11"):
    recs=core.shifts(db,month)
    legacy=round(sum(core.paid_hours(r)*300 for r in recs if r["origin"]=="import_excel"),2)
    actual=round(sum(__import__("finance").shift_earnings(db,r) for r in recs if r["origin"]=="import_excel"),2)
    print("MONTH",month,"IMPORTED_OLD",legacy,"IMPORTED_NEW",actual)
    assert abs(legacy-actual)<0.05
for name,view in friendly_ui.VIEWS.items():
    text,kb=view(db)
    assert 5<len(text)<4096,(name,len(text))
    assert "<br>" not in text
    assert "\\n" not in text,(name,text)
    assert kb.inline_keyboard
    print("SCREEN",name,"OK",len(text))
path=report.report(db)
book=load_workbook(path,read_only=True,data_only=True)
expected={"Обзор","Октябрь 2026","Ноябрь 2026","Сверка с офисом","Поступления","История ставок","Источники","Инструкция","Журнал"}
assert expected.issubset(set(book.sheetnames)),book.sheetnames
print("MAIN",book.sheetnames,path.stat().st_size)
book.close()
for kind,func in report_catalog.EXPORTS.items():
    file=func(db)
    with open(file,"rb") as f: assert f.read(4)==b"PK\x03\x04"
    print("EXPORT",kind,file.stat().st_size)
post=db.execute("SELECT COUNT(*),COUNT(end_utc) FROM shifts").fetchone()
assert tuple(pre)==tuple(post)
assert db.execute("SELECT COUNT(*) FROM history_source_rows").fetchone()[0]==source
print("STAGING_OK, zero modifications to shift rows")
