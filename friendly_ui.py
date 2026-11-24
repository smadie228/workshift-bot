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
from ledger import month_ledger, parse_month, month_title

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
    month=datetime.now(TZ).strftime("%Y-%m")
    data=month_ledger(db,month)
    total=data["summary"]
    active=open_shift(db);pause=workflows.break_status(db)
    risk=workflows.issues(db)
    status="Смена не начата"
    if active:
        started=read_time(active["start_utc"])
        status=(f"☕ Перерыв с {read_time(pause['break_started_utc']):%H:%M}"
                if pause["on_break"] else f"🟢 На работе с {started:%H:%M}")
        status+=f" · с начала {duration(max(0,(datetime.now(TZ)-started).total_seconds()/3600))}"
    text=(f"<b>Мои смены</b>\n{status}\n\n"
          f"<b>{month_name(month)}</b>\n"
          f"{duration(total['hours'])} · {total['shifts']} закрытых смен\n"
          f"За часы <b>{money(total['earned'])}</b>\n\n"
          "Приход и уход — большая кнопка внизу.\n"
          "В табеле можно открыть любой день и проверить расчёт.")
    rows=[]
    if active and pause["on_break"]:
        rows.append([("▶️ Завершить ранее начатый перерыв","pause:end")])
    rows.extend([[("📋 Табель",f"ledger:{month}:0:all"),("💰 Деньги",f"month:{month}")],
                 [("📥 Отчёт за месяц",f"reports:{month}"),("⋯ Ещё","ui:more")]])
    if risk:rows.append([(f"⚠️ Проверить записи · {len(risk)}","ui:issues")])
    return text,kb(rows)

def pay(db,month=None):
    month=month or datetime.now(TZ).strftime("%Y-%m")
    data=month_ledger(db,month);summary=data["summary"];first,second=data["periods"]
    text=(f"<b>Деньги · {month_title(month)}</b>\n\n"
          f"Начислено: <b>{money(summary['total'])}</b>\n"
          f"Получено: <b>{money(summary['received'])}</b>\n\n"
          f"За часы: {money(summary['earned'])}\n"
          f"{duration(summary['hours'])} · {summary['shifts']} закрытых смен\n"
          f"Корректировки: {money(summary['additional']['net_adjustment'])}\n\n"
          f"<b>За часы по частям месяца</b>\n"
          f"1–15: {money(first['earnings'])}\n"
          f"16–конец: {money(second['earnings'])}")
    future=data['forecast']
    if future['future_shifts']:
        text+=(f"\n\nПлан впереди: +{money(future['future_pay'])} · {future['future_shifts']} смен."
               "\n<i>Это оценка, не начисленные деньги.</i>")
    text+="\n\n<i>Налоги и задолженность автоматически не рассчитываются.</i>"
    return text,kb([
      [("‹ Месяц",f"month:{_adjacent_month(month,-1)}"),("Месяц ›",f"month:{_adjacent_month(month,1)}")],
      [("Как рассчитано",f"calc:{month}"),("Табель",f"ledger:{month}:0:all")],
      [("Записать поступление","fin:new:payment"),("Доплаты","ui:adjustments")],
      [("📥 Этот месяц",f"file:month:{month}"),("Главная","ui:home")]])


def calculation(db,month):
    data=month_ledger(db,month);summary=data['summary'];extra=summary['additional']
    text=f"<b>Расчёт · {month_title(month)}</b>\n\n"
    for rate in data['rates'][:20]:
        text+=f"{duration(rate['hours'])} × {money(rate['rate'])}/ч = <b>{money(rate['earned'])}</b>\n"
    if not data['rates']:text+="Завершённых смен нет.\n"
    text+=(f"\nЗа часы: {money(summary['earned'])}\n"
           f"Премии и доплаты: +{money(extra['bonus']+extra['allowance'])}\n"
           f"Возмещения: +{money(extra['reimbursement'])}\n"
           f"Удержания: −{money(extra['deduction'])}\n"
           f"<b>Итого: {money(summary['total'])}</b>\n\n"
           "Применяется ставка на дату прихода. Сумма округляется по каждой смене; время на экране — до минуты.\n"
           "Поступления не уменьшают начисления: это отдельный журнал.")
    future=data['forecast']
    if future['future_shifts']:
        text+=(f"\n\n<b>Отдельный прогноз</b>\n{duration(future['future_hours'])} · {money(future['future_pay'])}. "
               f"Оценочный перерыв {future['assumed_break_minutes']} мин/смена. Открытая смена и прошлые пропуски исключены.")
    return text,kb([[("Разбор по дням",f"ledger:{month}:0:all")],[("‹ Деньги",f"month:{month}"),("📥 Отчёт",f"file:month:{month}")]])

def history(db):
    rows=shifts(db,limit=6)
    text="📅 <b>Последние смены</b>\n\n"
    text+="\n".join(shift_line(s) for s in rows) if rows else "Пока нет закрытых смен."
    text+="\n\n<i>Открой табель для просмотра по дням. Для скачивания доступен отчёт за выбранный месяц.</i>"
    return text,kb([
      [("📋 Табель","ledger:"+datetime.now(TZ).strftime("%Y-%m")+":0:all"),
       ("🗓 Календарь","cal:"+datetime.now(TZ).strftime("%Y-%m"))],
      [("📆 По месяцам","hist:"+datetime.now(TZ).strftime("%Y-%m")+":0"),("📥 Отчёты","ui:reports")],
      [("✏️ Исправления","ui:edit"),("🏠 Главная","ui:home")]])

def report_menu(db,month=None):
    month=month or datetime.now(TZ).strftime("%Y-%m")
    data=month_ledger(db,month);summary=data["summary"]
    text=(f"<b>Отчёт · {month_title(month)}</b>\n\n"
          f"{summary['shifts']} закрытых смен · {duration(summary['hours'])}\n"
          f"За часы: <b>{money(summary['earned'])}</b>\n\n"
          "В одном файле: <b>Обзор → Табель → Смены → Деньги</b>.\n"
          "Все дни месяца, план рядом с фактом, расшифровка по ставкам и итоги по фильтру.\n\n"
          "<i>Файл — срез на момент скачивания. Правки Excel не изменяют записи бота.</i>")
    return text,kb([
      [("‹ Месяц",f"reports:{_adjacent_month(month,-1)}"),("Месяц ›",f"reports:{_adjacent_month(month,1)}")],
      [("Excel за месяц",f"file:month:{month}"),("PDF за месяц",f"file:pdf:{month}")],
      [("Годовой обзор",f"year:{month[:4]}")],
      [("📋 Посмотреть в чате",f"ledger:{month}:0:all")],
      [("Полный архив и источники","ui:exports"),("Главная","ui:home")]])


def exports_menu(db):
    return ("<b>Архив и первичные данные</b>\n\n"
            "Полная книга содержит все месяцы, ставки и журнал. Отдельные файлы нужны для подробной проверки источников.\n\n"
            "Для обычного просмотра удобнее отчёт за один месяц."),kb([
      [("📚 Полная книга за все месяцы","file:main")],
      [("План и выходные","file:plan"),("Сверка с офисом","file:office")],
      [("Исходные строки","file:sources"),("Отметки из чата","file:evidence")],
      [("‹ Отчёт за месяц","ui:reports"),("Главная","ui:home")]])


def more(db):
    return ("<b>Ещё</b>\n\nПланирование, проверка и настройки. Личные смены, офисный табель и поступления сохраняются отдельно."),kb([
      [("Повторяющийся график","series:home"),("История и отмена","audit:0")],
      [("Годовая аналитика","year:"+str(datetime.now(TZ).year))],
      [("🗓 План","ui:plan"),("🏢 Офисный табель","ui:office")],
      [("💵 Доплаты","ui:adjustments"),("✏️ Исправить время","ui:edit")],
      [("🕒 Неделя","ui:week"),("⚙️ Настройки","ui:settings")],
      [("📚 Архив","ui:exports"),("🧭 Подсказки","ui:assistant")],
      [("Главная","ui:home")]])


def table_view(db,month,page=0,filter_name="all"):
    data=month_ledger(db,month)
    if filter_name not in ("all","worked","review"):raise ValueError("Неверный фильтр")
    relevant=[r for r in data["days"] if r["records"] or r["plan"] or r["office_hours"] is not None]
    if filter_name=="worked":relevant=[r for r in relevant if r["closed_shifts"]]
    elif filter_name=="review":relevant=[r for r in relevant if r["open"] or r["status"]=="missing" or r["comparison"]=="difference"]
    pages=max(1,(len(relevant)+5)//6);page=max(0,min(int(page),pages-1));shown=relevant[page*6:page*6+6]
    text=f"<b>Табель · {month_title(month)}</b>\n"
    text+=f"Факт: {duration(sum(r['hours'] or 0 for r in relevant))} · {money(sum(r['earnings'] or 0 for r in relevant))}\n\n"
    buttons=[]
    for row in shown:
        stamp=row['day'][8:]+'.'+row['day'][5:7]
        if row['closed_shifts']:
            detail=f"{duration(row['hours'])} · {money(row['earnings'])}"
        elif row['open']:detail="Смена открыта"
        elif row['plan'] and row['plan']['status']=='shift':detail=f"План {row['plan']['start'] or '—'}–{row['plan']['end'] or '—'}"
        else:detail=row['status_label']
        badge="⚠️" if row['comparison']=='difference' or row['status']=='missing' else "🟢" if row['open'] else ""
        text+=f"<b>{stamp}</b> {badge} · {escape(detail)}\n"
        buttons.append([(f"{stamp} · разбор дня",'day:'+row['day'])])
    if not shown:text+="По этому фильтру записей нет.\n"
    text+=f"\n<i>Страница {page+1}/{pages}. Нажми дату для подробностей. План не входит в итог факта.</i>"
    rows=[[("Все",f"ledger:{month}:0:all"),("Факт",f"ledger:{month}:0:worked"),("Проверить",f"ledger:{month}:0:review")]]+buttons
    navigation=[]
    if page>0:navigation.append(("‹ Дни",f"ledger:{month}:{page-1}:{filter_name}"))
    if page+1<pages:navigation.append(("Дни ›",f"ledger:{month}:{page+1}:{filter_name}"))
    if navigation:rows.append(navigation)
    rows.extend([[("‹ Месяц",f"ledger:{_adjacent_month(month,-1)}:0:{filter_name}"),("Месяц ›",f"ledger:{_adjacent_month(month,1)}:0:{filter_name}")],
      [("Календарь",f"cal:{month}"),("📥 Этот месяц",f"file:month:{month}")],[("Главная","ui:home")]])
    return text,kb(rows)

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
      [("Повторяющийся график","series:home"),("Мои серии","series:list")],
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


VIEWS={"more":more,"exports":exports_menu,"home":home,"pay":pay,"shifts":history,"reports":report_menu,
       "plan":plan,"office":office,"sources":sources,"evidence":evidence,
       "settings":settings,"edit":edit,"assistant":assistant,
       "system":system_status,"week":week_card,
       "issues":issues_view,"adjustments":adjustments_view}
