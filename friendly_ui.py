"""Compact, read-only Telegram screens with inline navigation.
UI does not modify shift times, salaries or office data.
"""
from datetime import datetime
from html import escape
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from core import TZ, rate, totals, shifts, paid_hours, read_time, local_day, open_shift, week_totals
from finance import payment_totals, planned_rates, integrity_summary
import pro_assistant
from pathlib import Path
import shutil
from core import DATA_DIR

def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text,callback_data=data) for text,data in row] for row in rows])

def money(value):
    return f"{value:,.0f}".replace(","," ")+" ₽"

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
    status=(f"🟢 <b>В работе</b> с {read_time(active['start_utc']):%H:%M}" if active else "⚪ Сейчас смена не начата")
    text=("\u200b🏠 <b>Мои смены</b>\n\n"
          f"{status}\n"
          f"📅 {month_name(cur)} · {current['shifts']} смен\n"
          f"⏱ {duration(current['hours'])}\n"
          f"💰 <b>{money(current['earnings'])}</b>\n\n"
          "Для прихода и ухода используй большую кнопку внизу.")
    return text,kb([
      [("💰 Зарплата","ui:pay"),("📅 История","ui:shifts")],
      [("🗓 План смен","ui:plan"),("📂 Отчёты","ui:reports")],
      [("🏢 Сверка с офисом","ui:office")],
      [("🧭 Помощник","ui:assistant"),("⚙️ Настройки","ui:settings")]])

def pay(db,month=None):
    month=month or datetime.now(TZ).strftime("%Y-%m")
    t=totals(db,month); a=totals(db,month,1); b=totals(db,month,2)
    received=payment_totals(db,month)
    text=(f"💰 <b>Зарплата — {month_name(month)}</b>\n\n"
          f"Начислено: <b>{money(t['earnings'])}</b>\n"
          f"Отработано: {duration(t['hours'])}\n"
          f"Закрыто смен: {t['shifts']}\n\n"
          f"① 1–15 число: <b>{money(a['earnings'])}</b>\n"
          f"② 16–конец: <b>{money(b['earnings'])}</b>\n\n"
          f"💳 Получено по отметкам: <b>{money(sum(received.values()))}</b>\n"
          "<i>Начислено и поступило — разные суммы; налоги и авансы отдельно.</i>")
    y,m=map(int,month.split("-"))
    prev=f"{y-1 if m==1 else y:04d}-{12 if m==1 else m-1:02d}"
    nextm=f"{y+1 if m==12 else y:04d}-{1 if m==12 else m+1:02d}"
    return text,kb([
      [("◀️ Предыдущий",f"month:{prev}"),("Следующий ▶️",f"month:{nextm}")],
      [("💳 Добавить поступление","fin:new:payment")],
      [("📥 Скачать Excel","file:main")],
      [("🏠 Главная","ui:home")]])

def history(db):
    rows=shifts(db,limit=6)
    text="📅 <b>Последние смены</b>\n\n"
    text+="\n".join(shift_line(s) for s in rows) if rows else "Пока нет закрытых смен."
    text+="\n\n<i>Полная история — в книге Excel.</i>"
    return text,kb([
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
    today=datetime.now(TZ).date().isoformat()
    rows=db.execute("""SELECT day,status,start_time,end_time FROM (
        SELECT day,status,start_time,end_time,
        ROW_NUMBER() OVER (PARTITION BY day ORDER BY imported_utc DESC,source DESC) AS rn
        FROM history_source_rows
        WHERE day>=? AND status IN ('План','Выходной')
    ) WHERE rn=1 ORDER BY day LIMIT 5""",(today,)).fetchall()
    text="🗓 <b>Ближайшие дни</b>\n\n"
    if not rows:
        text+="В старой таблице нет ближайших плановых смен."
    for r in rows:
        stamp=datetime.strptime(r["day"],"%Y-%m-%d").strftime("%d.%m")
        if r["status"]=="Выходной":
            text+=f"🌿 <b>{stamp}</b> · выходной\n"
        else:
            text+=f"🟡 <b>{stamp}</b> · {escape(r['start_time'] or '—')}–{escape(r['end_time'] or '—')}\n"
    text+="\n<i>Это план из Excel, а не подтверждённый факт.</i>"
    return text,kb([
      [("📥 Скачать красивый план","file:plan")],
      [("📂 Все отчёты","ui:reports"),("🏠 Главная","ui:home")]])

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
    disk=shutil.disk_usage(DATA_DIR)
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

VIEWS={"home":home,"pay":pay,"shifts":history,"reports":report_menu,
       "plan":plan,"office":office,"sources":sources,"evidence":evidence,
       "settings":settings,"edit":edit,"assistant":assistant,"system":system_status}
