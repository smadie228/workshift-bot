"""Compact, read-only Telegram screens with inline navigation.
UI does not modify shift times, salaries or office data.
"""
from datetime import datetime, timedelta
from html import escape
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from core import TZ, rate, totals, shifts, paid_hours, read_time, local_day, open_shift, week_totals
from finance import payment_totals, planned_rates, integrity_summary
import pro_assistant
from pathlib import Path
import shutil
from core import DATA_DIR
import workflows

def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text,callback_data=data) for text,data in row] for row in rows])

def money(value):
    v=float(value)
    digits=0 if v.is_integer() else 2
    return f"{v:,.{digits}f}".replace(","," ").replace(".",",")+" ₽"

def duration(hours):
    minutes=round(hours*60)
    return f"{minutes//60} ч {minutes%60:02d} мин"

def month_name(month):
    names=["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"]
    year,number=map(int,month.split("-"))
    return f"{names[number-1].capitalize()} {year}"

def shift_line(s):
    started=read_time(s["start_utc"])
    stamp=started.strftime("%d.%m")
    if not s["end_utc"]:
        return f"• <b>{stamp}</b> · {started:%H:%M} → в работе"
    ended=read_time(s["end_utc"])
    origin=" · из Excel" if s["origin"]=="import_excel" else ""
    return f"• <b>{stamp}</b> · {started:%H:%M}–{ended:%H:%M} · {duration(paid_hours(s))}{origin}"

def home(db):
    cur=datetime.now(TZ).strftime("%Y-%m")
    current=totals(db,cur)
    active=open_shift(db)
    pause=workflows.break_status(db)
    risk=workflows.issues(db)
    if active:
        started=read_time(active["start_utc"])
        gross=max(0,(datetime.now(TZ)-started).total_seconds()/3600)
        status=(f"☕ <b>Перерыв с {read_time(pause['break_started_utc']):%H:%M}</b>"
                if pause["on_break"] else
                f"🟢 <b>На работе с {started:%H:%M}</b>")
        clock=f"\n⏳ С начала смены: {duration(gross)}"
    else:
        status="⚪ Сейчас смена не начата"
        clock=""
    text=(f"🏠 <b>Мои смены</b>\n\n"
          f"{status}{clock}\n\n"
          f"📅 {month_name(cur)} · {current['shifts']} закрытых смен\n"
          f"⏱ Отработано: <b>{duration(current['hours'])}</b>\n"
          f"💰 Начислено: <b>{money(current['earnings'])}</b>"
          +(f"\n⚠️ Проверить: {len(risk)} записей" if risk else "")
          +"\n\nОсновная кнопка прихода/ухода — внизу.")
    rows=[]
    if pause["on_break"]:
        rows.append([("▶️ Закончить перерыв","pause:end")])
    elif active:
        rows.append([("☕ Начать перерыв","pause:start")])
    rows.extend([
      [("💰 Зарплата","ui:pay"),("📅 История","ui:shifts")],
      [("🕒 Неделя","ui:week"),("🗓 Календарь","cal:"+cur)],
      [("📋 Мой план","ui:plan"),("📂 Отчёты","ui:reports")],
      [("🏢 Сверка","ui:office"),("⚠️ Проверка","ui:issues")],
      [("💵 Доплаты","ui:adjustments"),("⚙️ Настройки","ui:settings")],
      [("🧭 Помощник","ui:assistant")]
    ])
    return text,kb(rows)

def pay(db,month=None):
    month=month or datetime.now(TZ).strftime("%Y-%m")
    t=totals(db,month);a=totals(db,month,1);b=totals(db,month,2)
    got=payment_totals(db,month)
    adj=workflows.adjustment_totals(db,month)
    expected=t["earnings"]+adj["net_adjustment"]
    text=(f"💰 <b>Зарплата · {month_name(month)}</b>\n\n"
          f"⏱ {duration(t['hours'])} · {t['shifts']} закрытых смен\n"
          f"За часы: <b>{money(t['earnings'])}</b>\n"
          f"① 1–15: {money(a['earnings'])}\n"
          f"② 16–конец: {money(b['earnings'])}\n\n"
          f"🎁 Премии и доплаты: +{money(adj['bonus']+adj['allowance'])}\n"
          f"🚗 Возмещения: +{money(adj['reimbursement'])}\n"
          f"➖ Удержания: −{money(adj['deduction'])}\n"
          f"🧮 Сумма записей: <b>{money(expected)}</b>\n"
          f"💳 Отмечено поступлений: <b>{money(sum(got.values()))}</b>\n\n"
          "<i>Без самостоятельного расчёта налогов. Ведомость работодателя может отличаться.</i>")
    y,m=map(int,month.split("-"))
    previous=f"{y-1 if m==1 else y:04d}-{12 if m==1 else m-1:02d}"
    following=f"{y+1 if m==12 else y:04d}-{1 if m==12 else m+1:02d}"
    return text,kb([
       [("◀️ Месяц",f"month:{previous}"),("Месяц ▶️",f"month:{following}")],
       [("💵 Доплаты и удержания","ui:adjustments")],
       [("💳 Записать поступление","fin:new:payment")],
       [("📥 Скачать Excel","file:main"),("🏠 Главная","ui:home")]])

def history(db):
    rows=shifts(db,limit=6)
    text="📅 <b>Последние смены</b>\n\n"
    text+="\n".join(shift_line(s) for s in rows) if rows else "Пока нет закрытых смен."
    text+="\n\n<i>Полная история — в книге Excel.</i>"
    return text,kb([
      [("🗓 Календарь","cal:"+datetime.now(TZ).strftime("%Y-%m")),
       ("📆 По месяцам","hist:"+datetime.now(TZ).strftime("%Y-%m")+":0")],
      [("📥 Все смены в Excel","file:main")],
      [("✏️ Исправления","ui:edit"),("🏠 Главная","ui:home")]])

def report_menu(db):
    return ("📂 <b>Отчёты</b>\n\n"
            "Выбери нужный файл. Он придёт прямо в этот чат — с заголовками, цветами и удобными вкладками.\n\n"
            "Исходные данные остаются закрытыми, доступ — только у владельца."),kb([
      [("📊 Зарплата и все смены","file:main")],
      [("🗓 План и выходные","file:plan"),("🏢 Сверка с офисом","file:office")],
      [("📸 Отметки из чата","file:evidence"),("📚 Источники","file:sources")],
      [("🏠 Главная","ui:home")]])

def plan(db):
    rows=workflows.upcoming_plan(db,limit=8)
    text="📋 <b>План смен</b>\n\n"
    if not rows:text+="Пока нет ближайших запланированных дней."
    for p in rows:
        stamp=datetime.strptime(p["day"],"%Y-%m-%d").strftime("%d.%m")
        label="🌿 Выходной" if p["status"]=="off" else f"🟡 {p['start'] or '—'}–{p['end'] or '—'}"
        text+=f"• <b>{stamp}</b> · {label}\n"
    text+="\n<i>План не прибавляется к фактически отработанным часам.</i>"
    today=datetime.now(TZ).date().isoformat()
    return text,kb([
      [("🗓 Открыть календарь","cal:"+today[:7])],
      [("➕ Завтра · 16–02","extra:plan:quick:"+(datetime.now(TZ).date()+timedelta(days=1)).isoformat())],
      [("📥 Скачать план","file:plan"),("🏠 Главная","ui:home")]])

def office(db):
    total=db.execute("SELECT COUNT(*) FROM office_days").fetchone()[0]
    photo=db.execute("SELECT COUNT(*) FROM office_image_days").fetchone()[0]
    if total:
        text=(f"🏢 <b>Сверка с офисом</b>\n\n"
              f"Подтверждённых записей: <b>{total}</b>\n"
              "Полная сравнительная таблица доступна в файле.")
    else:
        text=("🏢 <b>Сверка с офисом</b>\n\n"
              f"📷 Зарегистрировано дат со снимка: {photo}\n"
              "⏳ Точные офисные часы пока не подтверждены.\n\n"
              "Личные записи сохранены отдельно. До проверки исходного файла расхождения не вычисляю.")
    return text,kb([
       [("📎 Загрузить офисный XLSX","office:upload")],
       [("📥 Таблица для сверки","file:office")],
       [("📂 Отчёты","ui:reports"),("🏠 Главная","ui:home")]])

def sources(db):
    rows=db.execute("SELECT count(*) FROM history_source_rows").fetchone()[0]
    imported=db.execute("SELECT count(*) FROM shifts WHERE origin='import_excel'").fetchone()[0]
    screenshots=db.execute("SELECT count(*) FROM historical_evidence").fetchone()[0]
    return (f"📚 <b>Источники</b>\n\n"
            f"📄 Строк из таблиц: <b>{rows}</b>\n"
            f"✅ Исторических смен: <b>{imported}</b>\n"
            f"📸 Отметок со снимков: <b>{screenshots}</b>\n\n"
            "В файле указаны даты, исходные названия и номера строк."),kb([
              [("📥 Скачать источники","file:sources")],
              [("📸 Отметки со снимков","ui:evidence")],
              [("🏠 Главная","ui:home")]])

def evidence(db):
    n=db.execute("SELECT COUNT(*) FROM historical_evidence").fetchone()[0]
    return (f"📸 <b>Отметки из переписки</b>\n\n"
            f"Сохранено свидетельств: <b>{n}</b>\n"
            "Они не считаются отдельными сменами, чтобы не удвоить зарплату.\n\n"
            "Даты и исходные отметки — в оформленном файле."),kb([
              [("📥 Скачать отметки","file:evidence")],
              [("📚 Источники","ui:sources"),("🏠 Главная","ui:home")]])

def settings(db):
    from core import default_break
    upcoming=planned_rates(db)
    future=("\nБудущая ставка: "+str(upcoming[0]["rate_rub"])+" ₽ с "+upcoming[0]["effective_from"]) if upcoming else ""
    return (f"⚙️ <b>Центр настроек</b>\n\n"
            f"Сегодня: <b>{money(rate(db))}/час</b>\n"
            f"Перерыв для новых смен: <b>{default_break(db)} мин</b>\n"
            f"Часовой пояс: Москва{future}\n"
            "Доступ: только владелец"),kb([
             [("💵 Ставка с завтрашнего дня","fin:new:rate")],
             [("☕ Перерыв для новых смен","fin:new:break")],
             [("💳 Отметить выплату","fin:new:payment")],
             [("✏️ Исправить время","ui:edit"),("🩺 Состояние","ui:system")],
             [("🏠 Главная","ui:home")]])

def assistant(db):
    return pro_assistant.intro(),pro_assistant.menu()

def system_status(db):
    stats=integrity_summary(db)
    backups=DATA_DIR/"backups"
    recent=max(backups.glob("*.sqlite3"),key=lambda p:p.stat().st_mtime,default=None) if backups.exists() else None
    stamp=datetime.fromtimestamp(recent.stat().st_mtime,TZ).strftime("%d.%m %H:%M") if recent else "не найдена"
    disk=shutil.disk_usage(DATA_DIR if DATA_DIR.exists() else Path(__file__).resolve().parent)
    free=round(disk.free/disk.total*100)
    return ("🩺 <b>Состояние данных</b>\n\n"
       f"Смен: {stats['shifts']}\n"
       f"Открытых: {stats['open']}\n"
       f"Неверных интервалов: {stats['invalid_dates']}\n"
       f"Офисных подтверждений: {stats['office']}\n"
       f"Последняя локальная копия: {stamp}\n"
       f"Свободно на диске VPS: {free}%\n\n"
       "<i>Локальная резервная копия не заменяет резервирование на другом сервере.</i>"),kb([
       [("📂 Отчёты","ui:reports"),("⚙️ Настройки","ui:settings")],
       [("🏠 Главная","ui:home")]])

def edit(db):
    recent=db.execute("SELECT id,start_utc FROM shifts WHERE end_utc IS NOT NULL ORDER BY start_utc DESC LIMIT 4").fetchall()
    choices=[
      [("➕ Вчера","edit:new:yesterday"),("➕ Сегодня","edit:new:today")],
      [("📅 Другая дата","edit:new:date")]
    ]
    if open_shift(db):
        choices.insert(0,[("🔴 Закрыть незавершённую смену","edit:close")])
    for r in recent:
        day=read_time(r["start_utc"]).strftime("%d.%m")
        choices.append([(f"✏️ Исправить смену {day}",f"edit:fix:{r['id']}")])
    choices.append([("🏠 Главная","ui:home")])
    return ("✏️ <b>Исправление смены</b>\n\n"
            "Выбери, что нужно сделать. Бот сам спросит дату, время и перерыв.\n\n"
            "<i>Данные изменятся только после нажатия «Сохранить».</i>"),kb(choices)




def _adjacent_month(month, delta):
    year,number=map(int,month.split("-"))
    index=year*12+number-1+delta
    return f"{index//12:04d}-{index%12+1:02d}"

def history_month(db, month=None, page=0):
    """Readable paginated history; never mutates the database."""
    month=month or datetime.now(TZ).strftime("%Y-%m")
    month_name(month)  # validate year and month, including boundary cases
    rows=shifts(db,month)
    count=len(rows)
    page_size=7
    total_pages=max(1,(count+page_size-1)//page_size)
    page=max(0,min(int(page),total_pages-1))
    closed=[r for r in rows if r["end_utc"]]
    hours=sum(paid_hours(r) for r in closed)
    text=(f"📆 <b>История — {month_name(month)}</b>\n\n"
          f"Смен: <b>{len(closed)}</b> · Часов: <b>{duration(hours)}</b>\n\n")
    displayed=rows[page*page_size:(page+1)*page_size]
    if not displayed:
        text+="В этом месяце записей пока нет."
    else:
        text+="\n".join(shift_line(row) for row in displayed)
    text+=f"\n\n<i>Страница {page+1} из {total_pages}. Полная история — в Excel.</i>"
    controls=[]
    if total_pages>1:
        paging=[]
        if page>0:paging.append(("◀️ Новее",f"hist:{month}:{page-1}"))
        if page+1<total_pages:paging.append(("Старее ▶️",f"hist:{month}:{page+1}"))
        controls.append(paging)
    controls.extend([
        [("◀️ Месяц",f"hist:{_adjacent_month(month,-1)}:0"),
         ("Месяц ▶️",f"hist:{_adjacent_month(month,1)}:0")],
        [("📥 Все смены (Excel)","file:main")],
        [("✏️ Исправить","ui:edit"),("🏠 Главная","ui:home")]
    ])
    return text,kb(controls)

def week_card(db):
    monday,hours,earned,count=week_totals(db)
    today=datetime.now(TZ)
    total=7
    return (f"📊 <b>Неделя · {monday:%d.%m}–{monday+timedelta(days=6):%d.%m}</b>\n\n"
        f"✅ Закрыто смен: <b>{count}</b>\n"
        f"⏱ Время: <b>{duration(hours)}</b>\n"
        f"💰 Начислено: <b>{money(earned)}</b>\n\n"
        "<i>Только закрытые смены. Ночная смена учитывается по дате прихода.</i>"),kb([
       [("📆 История по месяцам","hist:"+today.strftime("%Y-%m")+":0")],
       [("📂 Отчёты","ui:reports"),("🏠 Главная","ui:home")]
    ])

def issues_view(db):
    found=workflows.issues(db)
    text="⚠️ <b>Проверка учёта</b>\n\n"
    if not found:
        text+="Сейчас подозрительных и незакрытых записей не обнаружено."
    else:
        for item in found[:8]:
            text+=f"• <b>{item['day']}</b> · {escape(item['text'])}\n"
    text+="\n\n<i>Это подсказки для проверки, а не автоматические исправления.</i>"
    return text,kb([
      [("✏️ Проверить и исправить","ui:edit")],
      [("📅 Календарь","cal:"+datetime.now(TZ).strftime("%Y-%m"))],
      [("🏠 Главная","ui:home")]])

def adjustments_view(db):
    current=datetime.now(TZ).strftime("%Y-%m")
    rows=db.execute("""SELECT day,category,amount_rub FROM pay_adjustments
        ORDER BY day DESC,id DESC LIMIT 5""").fetchall()
    labels={"bonus":"🎁 Премия","allowance":"➕ Доплата",
            "reimbursement":"🚗 Возмещение","deduction":"➖ Удержание"}
    text="💵 <b>Доплаты и удержания</b>\n\n"
    if not rows:text+="Пока ничего не записано.\n"
    for r in rows:
        text+=(f"• {r['day'][5:]} · {labels.get(r['category'],'Запись')}: "
               f"{money(float(r['amount_rub']))}\n")
    text+="\n<i>Отдельно от почасовых начислений, с записью в журнале.</i>"
    return text,kb([
      [("🎁 Премия","extra:adjust:bonus"),("➕ Доплата","extra:adjust:allowance")],
      [("🚗 Возмещение","extra:adjust:reimbursement"),("➖ Удержание","extra:adjust:deduction")],
      [("💰 Зарплата","ui:pay"),("🏠 Главная","ui:home")]])


VIEWS={"home":home,"pay":pay,"shifts":history,"reports":report_menu,
       "plan":plan,"office":office,"sources":sources,"evidence":evidence,
       "settings":settings,"edit":edit,"assistant":assistant,
       "system":system_status,"week":week_card,
       "issues":issues_view,"adjustments":adjustments_view}
