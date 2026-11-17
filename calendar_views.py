"""Interactive Telegram work calendar built only from the private local SQLite database."""
import calendar
from datetime import date, datetime
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from core import TZ, shifts, local_day, read_time, paid_hours
from finance import shift_earnings
import workflows

MONTHS=["январь","февраль","март","апрель","май","июнь",
        "июль","август","сентябрь","октябрь","ноябрь","декабрь"]

def month_parse(raw):
    try:
        if len(raw)!=7:raise ValueError("Invalid length")
        x=datetime.strptime(raw,"%Y-%m")
        if not 2000<=x.year<=2100:raise ValueError("Invalid year")
        return x.year,x.month
    except ValueError as exc:
        raise ValueError("Выбери допустимый месяц") from exc

def next_month(month,delta):
    y,m=month_parse(month)
    n=y*12+m-1+delta
    return f"{n//12:04d}-{n%12+1:02d}"

def kbd(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(title,callback_data=data)
        for title,data in row] for row in rows])

def month_view(db,month=None):
    month=month or datetime.now(TZ).strftime("%Y-%m")
    year,number=month_parse(month)
    current=date.today()
    today=datetime.now(TZ).date()
    first=f"{month}-01"
    end=f"{next_month(month,1)}-01"
    plans=db.execute("""SELECT day,status FROM history_source_rows
        WHERE day>=? AND day<? AND status IN ('План','Выходной')
        ORDER BY imported_utc,source""",(first,end)).fetchall()
    marks={r["day"]:("🟡" if r["status"]=="План" else "🌿") for r in plans}
    for r in db.execute("SELECT day,status FROM plan_days WHERE day>=? AND day<?",(first,end)):
        marks[r["day"]]="🌿" if r["status"]=="off" else "🟡"
    actual=shifts(db,month)
    for row in actual:
        marks[local_day(row["start_utc"])]="✅" if row["end_utc"] else "🟢"
    if today.strftime("%Y-%m")==month and today.isoformat() not in marks:
        marks[today.isoformat()]="🔹"
    weeks=calendar.monthcalendar(year,number)
    rows=[
        [("◀️",f"cal:{next_month(month,-1)}"),
         (f"{MONTHS[number-1].capitalize()} {year}","noop"),
         ("▶️",f"cal:{next_month(month,1)}")],
        [(x,"noop") for x in ("Пн","Вт","Ср","Чт","Пт","Сб","Вс")]
    ]
    for week in weeks:
        line=[]
        for day in week:
            if day==0:line.append(("·","noop"))
            else:
                stamp=f"{month}-{day:02d}"
                icon=marks.get(stamp,"")
                line.append((f"{icon}{day}",f"day:{stamp}"))
        rows.append(line)
    rows.extend([
        [("📆 История месяца",f"hist:{month}:0")],
        [("📂 Отчёты","ui:reports"),("🏠 Главная","ui:home")]
    ])
    completed=[s for s in actual if s["end_utc"]]
    total_hours=sum(paid_hours(s) for s in completed)
    text=(f"🗓 <b>Календарь · {MONTHS[number-1].capitalize()} {year}</b>\n\n"
          f"✅ Отработано: <b>{len(completed)} смен</b> · <b>{total_hours:.1f} ч</b>\n"
          f"🟢 открыта · ✅ закрыта · 🟡 план · 🌿 выходной\n\n"
          "Нажми дату, чтобы посмотреть время и начисления.")
    return text,kbd(rows)

def day_view(db,day):
    try:
        if len(day)!=10:raise ValueError
        target=datetime.strptime(day,"%Y-%m-%d").date()
        if not 2000<=target.year<=2100:raise ValueError
    except ValueError as exc:
        raise ValueError("Неверная дата") from exc
    rows=[s for s in shifts(db,target.strftime("%Y-%m"))
          if local_day(s["start_utc"])==day]
    plan=workflows.plan_day(db,day)
    off=db.execute("SELECT hours FROM office_days WHERE day=?",(day,)).fetchone()
    names=("Пн","Вт","Ср","Чт","Пт","Сб","Вс")
    text=f"📅 <b>{target:%d.%m.%Y}</b> · {names[target.weekday()]}\n\n"
    if rows:
        for idx,row in enumerate(rows,1):
            start=read_time(row["start_utc"])
            if row["end_utc"]:
                end=read_time(row["end_utc"])
                hours=paid_hours(row)
                earned=shift_earnings(db,row)
                text+=(f"✅ <b>Смена {idx}</b>\n"
                    f"🟢 Приход: {start:%H:%M}\n"
                    f"🔴 Уход: {end:%H:%M}"
                    +(" (след. день)" if end.date()!=start.date() else "")
                    +f"\n☕ Перерыв: {row['break_minutes']} мин"
                    +f"\n⏱ Отработано: {hours:.2f} ч"
                    +f"\n💰 Начислено: {earned:,.2f} ₽\n\n")
            else:
                text+=f"🟢 <b>Открытая смена</b> с {start:%H:%M}\n\n"
    else:
        text+="За этот день фактических отметок нет.\n\n"
    if plan:
        if plan["status"]=="off":
            text+="🌿 По импортированному плану: выходной.\n"
        else:
            text+=(f"🟡 План: {escape(str(plan['start'] or '—'))}"
                   f"–{escape(str(plan['end'] or '—'))}\n")
    if off:
        text+=f"🏢 Подтверждено офисом: {off['hours']:.2f} ч.\n"
    text+="\n<i>Плановые часы не прибавляются к отработанным.</i>"
    actions=[]
    if rows:
        first=rows[0]
        if first["end_utc"]:
            actions.append([("✏️ Исправить смену",f"edit:fix:{first['id']}")])
        else:
            actions.append([("🔴 Уточнить уход","edit:close")])
    elif target<=datetime.now(TZ).date():
        actions.append([("➕ Добавить пропущенную смену",f"edit:day:{day}")])
    if target>=datetime.now(TZ).date():
        actions.extend([
          [("🟡 План 16–02",f"extra:plan:quick:{day}"),("🌿 Выходной",f"extra:plan:off:{day}")],
          [("🕒 Другое время",f"extra:plan:custom:{day}")]
        ])
    actions.extend([
        [("◀️ Календарь",f"cal:{target:%Y-%m}")],
        [("📆 История месяца",f"hist:{target:%Y-%m}:0"),("🏠 Главная","ui:home")]
    ])
    return text,kbd(actions)
