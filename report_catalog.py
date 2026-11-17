"""Readable dynamic XLSX exports for the private shift bot.
Uses the pre-existing spreadsheet dependency of the running bot; never shares files publicly.
"""
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from core import DATA_DIR, TZ, shifts, paid_hours, local_day, read_time, office_report, rate

NAVY="17345A"; BLUE="2463B3"; WHITE="FFFFFF"; CLOUD="F1F6FD"
MINT="DBF5EE"; AMBER="FFF0CB"; GREY="E8EDF3"; INK="233550"
BORDER=Border(bottom=Side(style="hair",color="D5DEEA"))

def make_sheet(book,name,title,note,headings,widths):
    ws=book.active if len(book.worksheets)==1 and book.active.max_row==1 and book.active["A1"].value is None else book.create_sheet()
    ws.title=name
    n=len(headings)
    ws.merge_cells(start_row=1,start_column=1,end_row=2,end_column=n)
    top=ws.cell(1,1,title)
    top.font=Font(name="Calibri",size=18,bold=True,color=WHITE)
    top.fill=PatternFill("solid",fgColor=NAVY)
    top.alignment=Alignment(vertical="center",indent=1)
    ws.row_dimensions[1].height=28; ws.row_dimensions[2].height=16
    ws.merge_cells(start_row=3,start_column=1,end_row=4,end_column=n)
    c=ws.cell(3,1,note)
    c.font=Font(name="Calibri",size=10,color=INK)
    c.fill=PatternFill("solid",fgColor=MINT)
    c.alignment=Alignment(vertical="center",wrap_text=True,indent=1)
    ws.row_dimensions[3].height=20;ws.row_dimensions[4].height=16
    for j,(h,w) in enumerate(zip(headings,widths),1):
        c=ws.cell(6,j,h);c.font=Font(name="Calibri",size=11,bold=True,color=WHITE)
        c.fill=PatternFill("solid",fgColor=BLUE)
        c.alignment=Alignment(horizontal="center",vertical="center",wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width=w
    ws.row_dimensions[6].height=29
    ws.freeze_panes="A7"
    ws.sheet_view.showGridLines=False
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    ws.print_title_rows="1:6"
    return ws

def addrow(ws,row,values,tone=None):
    n=len(values);row=min(max(7,row),10000)
    color=tone or (WHITE if row%2 else CLOUD)
    for j,val in enumerate(values,1):
        c=ws.cell(row,j,val)
        c.font=Font(name="Calibri",size=10,color=INK)
        c.fill=PatternFill("solid",fgColor=color)
        c.border=BORDER
        c.alignment=Alignment(vertical="center",wrap_text=True,indent=1)
    ws.row_dimensions[row].height=25

def save(book,filename):
    path=DATA_DIR/filename
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".new.xlsx")
    book.save(temp)
    os.replace(temp,path)
    return path

def plan(db):
    book=Workbook()
    ws=make_sheet(book,"План","ПЛАН СМЕН",
        "Плановые дни из Excel — не подтвержденные смены. Фактическое время фиксируется кнопками Telegram.",
        ["Дата","День","Приход","Уход","Статус","Комментарий"],[18,14,14,14,18,52])
    rows=db.execute("""SELECT day,weekday,start_time,end_time,status FROM (
        SELECT day,weekday,start_time,end_time,status,
        ROW_NUMBER() OVER (PARTITION BY day ORDER BY imported_utc DESC,source DESC) AS rn
        FROM history_source_rows WHERE status IN ('План','Выходной')
    ) WHERE rn=1 ORDER BY day""").fetchall()
    for i,r in enumerate(rows,7):
        status=r["status"]
        addrow(ws,i,[r["day"],r["weekday"] or "",r["start_time"] or "—",r["end_time"] or "—",
             status,"План · не засчитывается в зарплату" if status=="План" else "Выходной"],MINT if status=="Выходной" else (CLOUD if i%2==0 else WHITE))
    if rows:ws.auto_filter.ref=f"A6:F{6+len(rows)}"
    return save(book,"План смен.xlsx")

def sources(db):
    book=Workbook()
    ws=make_sheet(book,"Обзор","ОТКУДА ВЗЯТЫ ДАННЫЕ",
        "Каждая исходная строка сохранена отдельно; несколько версий одной смены не складываются.",
        ["Категория","Количество","Пояснение"],[34,17,72])
    stats=[
      ("Строк Excel",db.execute("SELECT count(*) FROM history_source_rows").fetchone()[0],"История из импортированных документов"),
      ("Исторических смен",db.execute("SELECT count(*) FROM shifts WHERE origin='import_excel'").fetchone()[0],"Только записи со статусом «Работал»"),
      ("Свидетельств переписки",db.execute("SELECT count(*) FROM historical_evidence").fetchone()[0],"Не входят в начисления повторно"),
      ("Дат офисного фото",db.execute("SELECT count(*) FROM office_image_days").fetchone()[0],"Часы требуют проверки по оригиналу")]
    for row,vals in enumerate(stats,7):addrow(ws,row,vals,MINT if row%2 else CLOUD)
    ws2=make_sheet(book,"Исходные строки","АРХИВ ИСХОДНЫХ СТРОК",
        "Оригинальные значения из личных Excel: дата, время и ссылка на строку источника.",
        ["Дата","Состояние","С","До","Часы","Начислено","Исходный файл","Лист","Строка"],
        [16,16,12,12,14,18,50,20,12])
    rows=db.execute("""SELECT day,status,start_time,end_time,hours,earnings,source,sheet,excel_row
        FROM history_source_rows ORDER BY day,source,excel_row""").fetchall()
    for i,r in enumerate(rows,7):
        addrow(ws2,i,[r["day"],r["status"],r["start_time"],r["end_time"],r["hours"],r["earnings"],
                      r["source"],r["sheet"],r["excel_row"]],MINT if r["status"]=="Работал" else AMBER if r["status"]=="План" else None)
    if rows:ws2.auto_filter.ref=f"A6:I{6+len(rows)}"
    return save(book,"Источники.xlsx")

def evidence(db):
    book=Workbook()
    ws=make_sheet(book,"Сообщения","ЗАПИСИ ИЗ ПЕРЕПИСКИ",
        "Это свидетельства из скриншотов, но не автоматически подтверждённые приходы и уходы.",
        ["Дата","Время/заметка","Тип записи","Уверенность","Источник"],[18,26,30,20,35])
    rows=db.execute("SELECT event_date,time_text,role,confidence,source FROM historical_evidence ORDER BY event_date,time_text").fetchall()
    for i,r in enumerate(rows,7):
        addrow(ws,i,[r["event_date"],r["time_text"] or "Фото блокнота",r["role"],r["confidence"],r["source"]],AMBER if "не" in str(r["confidence"]).lower() else None)
    if rows:ws.auto_filter.ref=f"A6:E{6+len(rows)}"
    return save(book,"Отметки из чата.xlsx")

def office(db):
    book=Workbook()
    ws=make_sheet(book,"Сверка","ОФИС ↔ МОИ СМЕНЫ",
        "Числа из неразборчивого фото не заполняются догадками. Ждём точный исходный табель.",
        ["Дата","Часы офиса","Мои часы","Разница","Статус"],[18,19,19,19,34])
    mine=defaultdict(float)
    for s in shifts(db):
        if s["end_utc"]:mine[local_day(s["start_utc"])]+=paid_hours(s)
    known={r["day"]:r["hours"] for r in db.execute("SELECT day,hours FROM office_days")}
    photo=[r["day"] for r in db.execute("SELECT day FROM office_image_days ORDER BY day")]
    dates=sorted(set(photo)|set(known))
    for i,day in enumerate(dates,7):
        o=known.get(day);m=mine.get(day)
        delta=round(m-o,2) if o is not None and m is not None else None
        state="Совпадает" if delta is not None and abs(delta)<0.02 else "Расхождение" if delta is not None else "Нужно подтверждение"
        addrow(ws,i,[day,round(o,2) if o is not None else "—",
               round(m,2) if m is not None else "—",
               delta if delta is not None else "—",state],MINT if state=="Совпадает" else AMBER if state=="Нужно подтверждение" else None)
    if dates:ws.auto_filter.ref=f"A6:E{6+len(dates)}"
    return save(book,"Сверка с офисом.xlsx")

EXPORTS={"plan":plan,"sources":sources,"evidence":evidence,"office":office}
