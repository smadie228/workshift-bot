"""Readable archival XLSX, computed from the private database on every export.

Read-only source of truth is SQLite. No stale Excel formulas and no retroactive pay-rate changes.
"""
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import PatternFill, Font
from core import TZ, DATA_DIR, shifts, local_day, paid_hours, read_time, rate
from finance import shift_earnings, payment_totals, rate_at
from report_catalog import make_sheet, addrow, save, MINT, AMBER, CLOUD

def mname(month):
    months=["Январь","Февраль","Март","Апрель","Май","Июнь",
            "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]
    y,m=map(int,month.split("-"))
    return f"{months[m-1]} {y}"

def money_cell(c):
    c.number_format='#,##0.00 "₽"'

def hrs_cell(c):
    c.number_format="0.00"

def report(db,path=None):
    wb=Workbook()
    all_shifts=shifts(db)
    grouped=defaultdict(list)
    for r in all_shifts:grouped[local_day(r["start_utc"])[:7]].append(r)
    current=datetime.now(TZ)
    prev=(current.year-1,12) if current.month==1 else (current.year,current.month-1)
    periods={current.strftime("%Y-%m"),f"{prev[0]:04d}-{prev[1]:02d}"}
    periods.update(grouped)
    months=sorted(periods,reverse=True)

    overview=make_sheet(wb,"Обзор","МОИ СМЕНЫ · ОТЧЁТ",
        "Личный учёт зарплаты · Москва · данные офиса и личные отметки хранятся отдельно.",
        ["Месяц","Смены","Часы","Начислено","1–15 число","16–конец","Поступило","Офисных дней"],
        [22,12,14,22,22,22,22,17])
    for row,month in enumerate(months,7):
        recs=grouped.get(month,[])
        closed=[r for r in recs if r["end_utc"]]
        h=sum(paid_hours(r) for r in closed)
        amount=sum(shift_earnings(db,r) for r in closed)
        first=sum(shift_earnings(db,r) for r in closed if int(local_day(r["start_utc"])[8:])<=15)
        second=amount-first
        received=payment_totals(db,month)
        offices=db.execute("SELECT COUNT(*) FROM office_days WHERE day LIKE ?",(month+"%",)).fetchone()[0]
        addrow(overview,row,[mname(month),len(closed),round(h,2),round(amount,2),
              round(first,2),round(second,2),round(sum(received.values()),2),offices],
              MINT if month==current.strftime("%Y-%m") else None)
        for col in (4,5,6,7):money_cell(overview.cell(row,col))
        hrs_cell(overview.cell(row,3))
    if len(months)>=2:
        chart=BarChart()
        chart.title="Начислено по месяцам"
        chart.y_axis.title="Рубли"
        chart.x_axis.title="Месяц"
        chart.width=16;chart.height=7
        chart.add_data(Reference(overview,min_col=4,min_row=6,max_row=6+len(months)),titles_from_data=True)
        chart.set_categories(Reference(overview,min_col=1,min_row=7,max_row=6+len(months)))
        overview.add_chart(chart,"J6")
    overview.auto_filter.ref=f"A6:H{6+len(months)}"
    overview.freeze_panes="B7"

    for month in months:
        ws=make_sheet(wb,mname(month),"РАБОЧЕЕ ВРЕМЯ · "+mname(month).upper(),
            "Фактические и импортированные смены. Ночные смены привязаны к дате прихода. Заработок учитывает ставку на день смены.",
            ["Дата","Приход (МСК)","Уход (МСК)","Перерыв, мин","Отработано, ч",
             "Ставка, ₽","Начислено, ₽","Источник","№ записи"],
            [18,22,22,18,18,16,21,18,14])
        records=sorted(grouped.get(month,[]),key=lambda x:x["start_utc"])
        for idx,s in enumerate(records,7):
            day=local_day(s["start_utc"])
            first=read_time(s["start_utc"]).replace(tzinfo=None)
            last=read_time(s["end_utc"]).replace(tzinfo=None) if s["end_utc"] else None
            source={"import_excel":"Из Excel","button":"Кнопка","manual":"Исправлено"}.get(s["origin"],s["origin"])
            addrow(ws,idx,[day,first,last,s["break_minutes"],
                  round(paid_hours(s),3) if last else None,rate_at(db,day),
                  shift_earnings(db,s) if last else None,source,s["id"]],
                  MINT if s["origin"]=="button" else CLOUD if s["origin"]=="import_excel" else AMBER)
            for col in (2,3):ws.cell(idx,col).number_format="dd.mm.yyyy hh:mm"
            hrs_cell(ws.cell(idx,5))
            for col in (6,7):money_cell(ws.cell(idx,col))
        if records:ws.auto_filter.ref=f"A6:I{6+len(records)}"

    office_ws=make_sheet(wb,"Сверка с офисом","ОФИСНЫЙ ТАБЕЛЬ ↔ МОИ ЧАСЫ",
        "Подтверждённые офисные часы и предварительные записи не подменяют друг друга. Нечёткое фото не используется для расчёта.",
        ["Дата","Офис, ч","Мои часы","Разница, ч","Статус","Источник"],
        [19,20,20,20,30,34])
    measured=defaultdict(float)
    for s in all_shifts:
        if s["end_utc"]:measured[local_day(s["start_utc"])]+=paid_hours(s)
    recorded={r["day"]:(r["hours"],r["source"]) for r in db.execute("SELECT day,hours,source FROM office_days")}
    office_days={r["day"] for r in db.execute("SELECT day FROM office_image_days")}
    dates=sorted(set(recorded)|office_days)
    for idx,day in enumerate(dates,7):
        office=recorded.get(day)
        own=measured.get(day)
        delta=round(own-office[0],3) if office is not None and own is not None else None
        if office is None:status="Ожидает табель"
        elif own is None:status="Нет личной отметки"
        else:status="Совпадает" if abs(delta)<0.02 else "Расхождение"
        source=office[1] if office else "Фото: дата известна, часы не подтверждены"
        addrow(office_ws,idx,[day,round(office[0],3) if office else None,
              round(own,3) if own is not None else None,delta,status,source],
              MINT if status=="Совпадает" else AMBER if status!="Расхождение" else "FEE2E2")
        for col in (2,3,4):hrs_cell(office_ws.cell(idx,col))
    if dates:office_ws.auto_filter.ref=f"A6:F{6+len(dates)}"

    payout_ws=make_sheet(wb,"Поступления","ПОЛУЧЕННАЯ ЗАРПЛАТА",
        "Фактические поступления вводятся отдельно. Не сравниваем автоматически сумму «на руки» с начислением до НДФЛ.",
        ["Рабочий месяц","Часть","Поступило, ₽","Дата поступления","Примечание","№"],
        [24,14,22,22,47,12])
    payments=db.execute("SELECT * FROM received_payments ORDER BY paid_on DESC,id DESC").fetchall()
    for idx,p in enumerate(payments,7):
        addrow(payout_ws,idx,[p["work_month"],p["period"],float(p["amount_rub"]),
              p["paid_on"],p["note"],p["id"]])
        money_cell(payout_ws.cell(idx,3))
    if payments:payout_ws.auto_filter.ref=f"A6:F{6+len(payments)}"

    rate_ws=make_sheet(wb,"История ставок","ИСТОРИЯ ПОЧАСОВОЙ СТАВКИ",
        "Новые ставки применяются с указанной даты; старые смены остаются по исходным условиям.",
        ["Действует с","₽ за час","Когда внесена","Пояснение"],[23,22,29,75])
    for idx,r in enumerate(db.execute("SELECT * FROM rate_history ORDER BY effective_from"),7):
        addrow(rate_ws,idx,[r["effective_from"],float(r["rate_rub"]),r["created_utc"],r["note"]])
        money_cell(rate_ws.cell(idx,2))

    src=make_sheet(wb,"Источники","ПРОИСХОЖДЕНИЕ ДАННЫХ",
        "Импорт, кнопки и данные офиса хранятся раздельно. Подтверждённые офисом значения имеют свой отдельный источник.",
        ["Название","Тип","Размер (байт)","Контрольная сумма SHA-256"],[52,20,21,75])
    source_rows=db.execute("SELECT name,kind,source_size,sha256 FROM history_source_assets ORDER BY name").fetchall()
    for idx,r in enumerate(source_rows,7):
        addrow(src,idx,[r["name"],r["kind"],r["source_size"],r["sha256"]])

    journal=make_sheet(wb,"Журнал","ИСТОРИЯ ИЗМЕНЕНИЙ",
        "Аудит исправлений и настроек. Подробности хранятся в JSON только внутри этого приватного файла.",
        ["Номер","Время UTC","Действие","Смена","Подробности"],
        [14,31,25,14,85])
    audits=db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 2000").fetchall()
    for idx,r in enumerate(audits,7):
        addrow(journal,idx,[r["id"],r["event_utc"],r["kind"],r["shift_id"],r["details"]])

    how=make_sheet(wb,"Инструкция","КАК РАБОТАЕТ МОЙ УЧЁТ",
        "Вопросы — в приватном Telegram-боте. База данных и отчёты постоянно находятся на VPS.",
        ["Раздел","Описание"],[34,102])
    instructions=[
        ("Приход / уход","Одна кнопка на главном экране. Время записывает сервер, московский часовой пояс."),
        ("История смен","Старые записи перенесены из Excel с отметкой происхождения. Кнопки создают новые записи."),
        ("Исправление","Выбери дату и время через подсказки, проверь итог, нажми «Сохранить». Все правки аудируются."),
        ("Зарплата","Две части месяца: 1–15 и 16–конец. Учитывается ставка в дату прихода."),
        ("Выплаты","Вносятся как поступления, отдельно от начислений; НДФЛ и авансы не угадываются."),
        ("Сверка","Офисные часы в отдельной таблице. Пока число из офиса не подтверждено — пусто."),
        ("Офисный файл","В чате выбери загрузку XLSX. Сначала сохраняется и проверяется; без подтверждения офисные часы не перезаписываются."),
        ("Бэкапы","Локальные копии SQLite; дополнительные внешние копии ещё не настроены."),
        ("Безопасность","Бот закрыт для других Telegram-аккаунтов, файлы не публикуются по ссылкам.")
    ]
    for idx,entry in enumerate(instructions,7):addrow(how,idx,entry,MINT if idx%2 else None)

    wb.active=0
    return save(wb,path if path else "Мои смены.xlsx")
