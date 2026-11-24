"""Приватный бот учёта смен. Один владелец, Telegram polling, без публичного веб-порта."""
import hmac
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from core import (connect,config,put_config,now,iso,read_time,TZ,open_shift,toggle,
                  log,shifts,totals,week_totals,rate,default_break,office_report,
                  set_office,corrected_shift,local_day,DATA_DIR,paid_hours)
from report import report
from report_month import monthly_report
from ledger import parse_month
from historical_views import source_text, plan_text, pending_office_text, storage_text, screenshot_text
import friendly_ui
from report_catalog import EXPORTS as CATALOG_EXPORTS
import edit_wizard
import finance_wizard
import pro_assistant
import office_upload
import calendar_views
import workflows
import extra_wizard
import planning_ui
import completion_ui

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
LOG=logging.getLogger("workshift")
BTN_IN="🟢 Пришёл"
BTN_OUT="🔴 Ушёл"
BTN_BREAK_START="☕ Начать перерыв"
BTN_BREAK_END="▶️ Конец перерыва"
MENU=[
    ["🏠 Главная","💰 Зарплата"],
    ["📅 Смены","📂 Отчёты"]
]

def kb(db):
    active=open_shift(db)
    items=[[BTN_OUT if active else BTN_IN]]
    if active and workflows.break_status(db)["on_break"]:
        # Only recovery for an already-open manual interval; no new pause button.
        items.append([BTN_BREAK_END])
    return ReplyKeyboardMarkup(items+MENU,resize_keyboard=True,is_persistent=True)

def owner(db):
    return config(db,"owner_id")

def authorized(update,db):
    u=update.effective_user
    chat=update.effective_chat
    return bool(u and chat and chat.type=="private" and owner(db)==str(u.id))

def is_private(update):
    return bool(update.effective_chat and update.effective_chat.type=="private")

def friendly_time(value):
    return read_time(value).strftime("%d.%m.%Y %H:%M")

def safe_month(s):
    datetime.strptime(s,"%Y-%m")
    return s

def sync(db):
    try: return report(db)
    except Exception:
        LOG.exception("Excel export failed; database transaction is intact")
        return None

async def reply(update,db,message):
    await update.message.reply_text(message,reply_markup=kb(db))

async def screen(update,context,name="home",month=None,page=0):
    db=context.application.bot_data["db"]
    if not authorized(update,db):
        return await denied(update,context)
    if name=="historymonth":
        text,buttons=friendly_ui.history_month(db,month,page)
    elif name=="calendar":
        text,buttons=calendar_views.month_view(db,month)
    elif name=="day":
        text,buttons=calendar_views.day_view(db,month)
    else:
        view=friendly_ui.VIEWS.get(name)
        if not view:
            return
        text,buttons=view(db,month) if name in ("pay","reports") else view(db)
    if update.callback_query:
        from telegram.error import BadRequest
        try:
            return await update.callback_query.edit_message_text(
                text,reply_markup=buttons,parse_mode="HTML")
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            raise
    return await update.message.reply_text(
        text,reply_markup=buttons,parse_mode="HTML")

async def send_pretty_file(update,context,key):
    db=context.application.bot_data["db"]
    if not authorized(update,db):
        return await denied(update,context)
    if key.startswith(("pdf:","yearpdf:")):
        from report_pdf import monthly_pdf,annual_pdf
        try:
            if key.startswith("pdf:"):
                month=key[4:];parse_month(month);path=monthly_pdf(db,month)
            else:
                year=int(key[8:]);path=annual_pdf(db,year)
            caption="Мои смены · PDF-сводка. Срез на момент формирования; план и поступления разделены."
        except (ValueError,TypeError):return
        except Exception:
            LOG.exception("PDF generation failed")
            await context.bot.send_message(update.effective_chat.id,"Не удалось подготовить PDF. Записи сохранены; Excel остаётся доступен.")
            return
    elif key.startswith("month:"):
        month=key[6:]
        try:
            parse_month(month)
            path=monthly_report(db,month)
        except (ValueError,TypeError):
            return
        except Exception:
            LOG.exception("Monthly report preparation failed")
            await context.bot.send_message(update.effective_chat.id,"Не удалось сформировать отчёт. Записи сохранены; попробуй повторить скачивание.")
            return
        caption="📋 "+friendly_ui.month_name(month)+" · обзор, табель, смены и деньги. Срез на момент скачивания."
    elif key=="main":
        path=sync(db)
        caption="📊 Полная книга: зарплата, месяцы и история изменений"
    else:
        generator=CATALOG_EXPORTS.get(key)
        if generator is None:
            return
        try:
            path=generator(db)
        except Exception:
            LOG.exception("Report preparation failed")
            if update.callback_query:
                await context.bot.send_message(update.effective_chat.id,"Не удалось сформировать файл. Все записи в базе сохранены.")
            else:
                await update.message.reply_text("Не удалось сформировать файл. Все записи в базе сохранены.")
            return
        captions={"plan":"🗓 План и выходные — оформленная таблица",
                  "sources":"📚 Источники и происхождение данных",
                  "evidence":"📸 Отметки и свидетельства из переписки",
                  "office":"🏢 Офисный табель и личные часы — сверка"}
        caption=captions[key]
    if not path:
        return
    with open(path,"rb") as fp:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,document=fp,
            filename=path.name,caption=caption)

async def inline_action(update,context):
    query=update.callback_query
    db=context.application.bot_data["db"]
    if not authorized(update,db):
        return await query.answer("Доступ закрыт",show_alert=True)
    await query.answer()
    action=query.data or ""
    if action.startswith(("cal:","day:","month:","hist:","reports:","ledger:","year:","audit:","edit:new:","edit:day:","edit:fix:","edit:close","fin:new:","extra:plan:","extra:adjust:")):
        for flow_key in ("series_flow","undo_flow","extra_flow","finance_flow","correction"):
            context.user_data.pop(flow_key,None)
        context.user_data["assistant_mode"]=False
    if action=="noop":return
    if action.startswith(("series:","audit:","undo:","year:")):
        if action.startswith("series:"):
            return await planning_ui.callback(update,context,db)
        return await completion_ui.callback(update,context,db)
    if action.startswith("calc:"):
        try:
            parse_month(action[5:])
            text,buttons=friendly_ui.calculation(db,action[5:])
        except ValueError:return
        from telegram.error import BadRequest
        try:return await query.edit_message_text(text,reply_markup=buttons,parse_mode="HTML")
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():raise
        return
    if action.startswith("reports:"):
        try:parse_month(action[8:])
        except ValueError:return
        return await screen(update,context,"reports",action[8:])
    if action.startswith("ledger:"):
        try:
            _,month,page,filter_name=action.split(":")
            parse_month(month)
            page=int(page)
            if not 0<=page<=500:return
            text,buttons=friendly_ui.table_view(db,month,page,filter_name)
        except (ValueError,TypeError):return
        from telegram.error import BadRequest
        try:return await query.edit_message_text(text,reply_markup=buttons,parse_mode="HTML")
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():raise
        return
    if action.startswith("cal:"):
        try:calendar_views.month_parse(action[4:])
        except ValueError:return
        return await screen(update,context,"calendar",action[4:])
    if action.startswith("day:"):
        try:
            date=datetime.strptime(action[4:],"%Y-%m-%d")
            if len(action[4:])!=10 or not 2000<=date.year<=2100:return
        except ValueError:return
        return await screen(update,context,"day",action[4:])
    if action.startswith("ui:"):
        context.user_data.pop("series_flow",None)
        context.user_data.pop("undo_flow",None)
        context.user_data.pop("extra_flow",None)
        context.user_data.pop("correction",None)
        context.user_data.pop("finance_flow",None)
        context.user_data["assistant_mode"]=(action=="ui:assistant")
        return await screen(update,context,action[3:])
    if action.startswith("hist:"):
        try:
            _,month,pagenum=action.split(":",2)
            datetime.strptime(month,"%Y-%m")
            year=int(month[:4]);pagenum=int(pagenum)
            if not (2000<=year<=2100 and 0<=pagenum<=500):return
        except (ValueError,IndexError):
            return
        return await screen(update,context,"historymonth",month,pagenum)
    if action.startswith("month:"):
        month=action[6:]
        try:
            parse_month(month)
            friendly_ui.month_name(month)
        except (ValueError,IndexError):
            return
        return await screen(update,context,"pay",month)
    if action.startswith("pause:"):
        return await pause_action(update,context,action[6:])
    if action.startswith("extra:"):
        outcome=await extra_wizard.callback(update,context,db)
        if outcome=="saved":pass  # Report is generated on demand.
        return
    if action.startswith("file:"):
        return await send_pretty_file(update,context,action[5:])
    if action.startswith("edit:"):
        outcome=await edit_wizard.callback(update,context,db)
        if outcome=="saved":pass  # Report is generated on demand.
        return
    if action.startswith("fin:"):
        outcome=await finance_wizard.callback(update,context,db)
        if outcome=="saved":pass  # Report is generated on demand.
        return
    if action.startswith("helper:"):
        context.user_data["assistant_mode"]=True
        answer=pro_assistant.answer(db,action[7:])
        return await context.bot.send_message(chat_id=update.effective_chat.id,
               text=answer,parse_mode="HTML",reply_markup=pro_assistant.menu())
    if action=="office:upload":
        context.user_data["await_office_document"]=True
        return await context.bot.send_message(chat_id=update.effective_chat.id,
             text="📎 Отправь сюда оригинальный офисный файл .xlsx (до 8 МБ).\n"
                  "Сначала сохраню его и покажу структуру; рабочие часы не изменятся автоматически.",
             reply_markup=friendly_ui.kb([[("❌ Отмена","office:cancel")]]))
    if action=="office:cancel":
        context.user_data.pop("await_office_document",None)
        return await context.bot.send_message(chat_id=update.effective_chat.id,text="Загрузка отменена.")

async def pause_action(update,context,action):
    db=context.application.bot_data["db"]
    if not authorized(update,db):
        return await denied(update,context)
    try:
        if action=="start":
            workflows.start_break(db)
            message="☕ Перерыв начался. Теперь время считается автоматически."
        elif action=="end":
            item=workflows.stop_break(db)
            message="▶️ Перерыв завершён: "+str(item["minutes"])+" мин."
        else:
            return
    except ValueError as exc:
        message="⚠️ "+str(exc)
    if update.callback_query:
        return await screen(update,context,"home")
    return await update.message.reply_text(message,reply_markup=kb(db))

async def started(update,context):
    db=context.application.bot_data["db"]
    if not is_private(update):return
    if authorized(update,db):
        args=getattr(context,"args",None) or []
        if args and args[0].startswith("q_"):
            from quick_routes import open_destination
            return await open_destination(update,context,args[0])
        if args and args[0].startswith("b_"):
            return await extra_wizard.begin_batch_draft(update,context,db,args[0])
        if args and args[0].startswith("p_"):
            return await extra_wizard.begin_calendar_draft(update,context,db,args[0])
        await update.message.reply_text(
            "👋 Готово. Кнопка прихода/ухода всегда внизу.\n"
            "Остальные данные — в разделах без ручных команд.",
            reply_markup=kb(db))
        return await screen(update,context,"home")
    if owner(db):
        return await update.message.reply_text("Это личный бот. Доступ закрыт.")
    await update.message.reply_text(
        "Бот ещё не привязан. Для владельца: /pair <код из закрытого файла сервера>.")

async def pairing(update,context):
    db=context.application.bot_data["db"]
    if not is_private(update):return
    if owner(db):return await update.message.reply_text("Привязка уже выполнена.")
    path=Path(os.getenv("WORKSHIFTS_PAIR_FILE","/etc/workshift-bot/pair.code"))
    expected=path.read_text(encoding="utf-8").strip() if path.exists() else ""
    supplied=context.args[0] if context.args else ""
    if not expected or not hmac.compare_digest(supplied,expected):
        return await update.message.reply_text("Неверный код.")
    db.execute("BEGIN IMMEDIATE")
    try:
        if owner(db):raise ValueError("Бот уже привязан.")
        put_config(db,"owner_id",str(update.effective_user.id))
        log(db,"owner_pair",None,{"telegram_user_id":update.effective_user.id})
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK");raise
    path.unlink(missing_ok=True)
    await reply(update,db,"Готово. Доступ только у этого Telegram-аккаунта.")

async def denied(update,context):
    if is_private(update):
        await update.message.reply_text("Это личный бот. Доступ закрыт.")

async def toggle_action(update,context,mode="toggle"):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    was=open_shift(db)
    if mode=="out" and was and workflows.active_break(db,was["id"]):
        return await reply(update,db,"☕ Сначала нажми «Конец перерыва», затем «Ушёл».")
    if mode=="in" and was:return await reply(update,db,"Смена уже начата: "+friendly_time(was["start_utc"]))
    if mode=="out" and not was:return await reply(update,db,"Нет начатой смены.")
    prev=db.execute("SELECT event_utc FROM events WHERE kind IN ('in','out') ORDER BY id DESC LIMIT 1").fetchone()
    if prev:
        gap=(now()-datetime.fromisoformat(prev["event_utc"])).total_seconds()
        if 0<=gap<8:
            return await reply(update,db,"Отметка только что записана. Повторное нажатие временно заблокировано.")
    try:
        kind,s=toggle(db,update.update_id)
    except ValueError as exc:
        await update.message.reply_text(
            "⚠️ "+str(exc)+"\n\nВремя не изменилось. Используй исправление с подтверждением.",
            reply_markup=friendly_ui.kb([
                [("✏️ Указать правильный уход","edit:close")],
                [("🏠 Главная","ui:home")]]))
        return
    if kind=="duplicate":return await reply(update,db,"Эта отметка уже обработана.")
    # SQLite is authoritative; generate reports only on explicit export.
    if kind=="in":
        await reply(update,db,
            "✅ Приход сохранён · "+friendly_time(s["start_utc"])+
            f"\nПерерыв: {s['break_minutes']} мин (учитывается автоматически).\n"
            "Если время нужно исправить, открой «📅 Смены» → «Исправления».")
    else:
        hours=paid_hours(s)
        from finance import shift_earnings
        amount=shift_earnings(db,s)
        pay=f"{amount:,.2f}".replace(","," ").replace(".",",")
        await reply(update,db,
            "✅ Уход сохранён · "+friendly_time(s["end_utc"])+
            f"\n⏱ Отработано: {friendly_ui.duration(hours)}"
            f"\n💰 Начислено: {pay} ₽"
            "\n\nЕсли время неточное, открой «📅 Смены» → «Исправления».")

async def summary(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    month=context.args[0] if context.args else datetime.now(TZ).strftime("%Y-%m")
    try:month=safe_month(month)
    except ValueError:return await reply(update,db,"Формат: /month 2026-11")
    return await screen(update,context,"pay",month)

async def history(update,context):
    return await screen(update,context,"shifts")

async def week(update,context):
    return await screen(update,context,"week")

async def office(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    if len(context.args)!=2:
        return await reply(update,db,"Сверка: /office 2026-11-12 9.23\n"
            "Часы с офисного табеля вводятся отдельно. «9.23» — десятичные часы, «9:23» — часы и минуты.")
    try:
        day,raw=context.args
        datetime.strptime(day,"%Y-%m-%d")
        hrs=(int(raw.split(":")[0])+int(raw.split(":")[1])/60) if ":" in raw else float(raw.replace(",","."))
        set_office(db,day,hrs)
        # Report is generated on demand.
        return await reply(update,db,f"Офис: {day} — {hrs:.3f} ч. Данные бота не менялись.")
    except (ValueError,TypeError):
        return await reply(update,db,"Проверь дату и часы: /office 2026-11-12 9.23")

async def comparison(update,context):
    return await screen(update,context,"office")

async def backup_file(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    path=sync(db)
    if not path:return await reply(update,db,"Не удалось подготовить файл. Записи в базе сохранены.")
    with open(path,"rb") as f:
        await context.bot.send_document(chat_id=update.effective_chat.id,document=f,
             filename="Мои смены.xlsx",caption="История по месяцам, выплаты и сверка с офисом.")

async def edit_help(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    await reply(update,db,"✏️ Исправление времени\n"
        "1. /history — посмотреть номер смены.\n"
        "2. /fix 12 2026-11-12 16:08 02:10 30 — исправить смену №12.\n"
        "3. /manual 2026-11-12 16:08 02:10 30 — добавить пропущенную.\n"
        "После полуночи дата ухода определяется автоматически.\n"
        "Изменения сохраняются в журнале.")

async def edit(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    try:
        a=context.args
        if update.message.text.startswith("/manual"):
            if len(a) not in (3,4):return await edit_help(update,context)
            sid=corrected_shift(db,a[0],a[1],a[2],int(a[3]) if len(a)==4 else None)
        else:
            if len(a) not in (4,5):return await edit_help(update,context)
            sid=corrected_shift(db,a[1],a[2],a[3],int(a[4]) if len(a)==5 else None,int(a[0]))
        # Report is generated on demand.
        await reply(update,db,f"Исправление записано для смены №{sid}. История изменений сохранена.")
    except (ValueError,TypeError) as exc:await reply(update,db,"Ошибка: "+str(exc))

async def break_time(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    if len(context.args)!=2:return await reply(update,db,"Перерыв: /break 12 30 (№ смены, минуты)")
    try:
        sid=int(context.args[0]);mins=int(context.args[1])
        if not 0<=mins<=600:raise ValueError("Перерыв 0–600 минут")
        r=db.execute("SELECT * FROM shifts WHERE id=?",(sid,)).fetchone()
        if not r:raise ValueError("Смена не найдена")
        old=r["break_minutes"]
        if r["end_utc"]:
            duration=(datetime.fromisoformat(r["end_utc"])-datetime.fromisoformat(r["start_utc"])).total_seconds()/60
            if mins>=duration:raise ValueError("Перерыв не может превышать смену")
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE shifts SET break_minutes=?,updated_utc=? WHERE id=?",(mins,iso(now()),sid))
        log(db,"break",sid,{"old":old,"new":mins})
        db.execute("COMMIT")
        # Report is generated on demand.
        await reply(update,db,f"Смена №{sid}: перерыв {mins} мин.")
    except (ValueError,TypeError) as exc:await reply(update,db,"Ошибка: "+str(exc))

async def settings(update,context):
    return await screen(update,context,"settings")

async def changed_setting(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    try:
        value=float(context.args[0].replace(",",".")) if context.args else None
        if value is None:raise ValueError("Не указано значение")
        is_break=update.message.text.startswith("/defaultbreak")
        if is_break:
            if value!=int(value) or not 0<=value<=600:raise ValueError("Целое число минут 0–600")
            db.execute("BEGIN IMMEDIATE")
            try:
                previous=default_break(db)
                put_config(db,"default_break_minutes",int(value))
                log(db,"setting",None,{"key":"break","old":previous,"new":value})
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            msg=f"Перерыв для будущих смен: {int(value)} минут."
        else:
            from finance import schedule_rate
            when=schedule_rate(db,value)
            msg=f"Новая ставка {value:.2f} ₽/час действует с {when}, предыдущие начисления не меняются."
        # Report is generated on demand.
        await reply(update,db,"✅ "+msg)
    except (ValueError,IndexError) as exc:await reply(update,db,"Ошибка: "+str(exc))

async def received_photo(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    if not update.message.photo:return
    last=update.message.photo[-1]
    if last.file_size and last.file_size>15_000_000:
        return await reply(update,db,"Фотография слишком большая (макс. 15 МБ).")
    folder=DATA_DIR / "office-evidence";folder.mkdir(mode=0o700,exist_ok=True)
    target=folder / (datetime.now(TZ).strftime("%Y%m%d-%H%M%S")+"-"+last.file_unique_id+".jpg")
    file=await context.bot.get_file(last.file_id)
    await file.download_to_drive(custom_path=str(target))
    log(db,"office_photo",None,{"filename":target.name})
    await reply(update,db,"Фото офисного табеля сохранено отдельно. Часы с него не подменяют записи бота.")

async def received_document(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):
        return await denied(update,context)
    return await office_upload.accept(update,context,db)

async def source_view(update,context):
    return await screen(update,context,"sources")

async def plan_view(update,context):
    return await screen(update,context,"plan")

async def screenshot_view(update,context):
    return await screen(update,context,"evidence")

async def storage_view(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    await reply(update,db,storage_text(db))

async def archive_file(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    directory=DATA_DIR / "sources"
    files=sorted(directory.glob("*.json"),key=lambda item:item.stat().st_mtime,reverse=True) if directory.is_dir() else []
    if not files:
        return await reply(update,db,"Архив исходных строк пока недоступен.")
    path=files[0]
    if not path.is_file():
        return await reply(update,db,"Архив исходных строк пока недоступен.")
    with path.open("rb") as file:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,document=file,
            filename="Архив исходных записей.json",
            caption="Исходные строки трёх таблиц и записи со скриншотов; изображения хранятся отдельно от текстовой расшифровки.")

async def on_text(update,context):
    db=context.application.bot_data["db"]
    if not authorized(update,db):return await denied(update,context)
    text=update.message.text.strip()
    all_buttons={name for line in MENU for name in line}|{BTN_IN,BTN_OUT,BTN_BREAK_START,BTN_BREAK_END}
    if context.user_data.get("series_flow"):
        if text not in all_buttons:
            if await planning_ui.text(update,context,db):return
        else:
            context.user_data.pop("series_flow",None)
    if context.user_data.get("extra_flow"):
        if text not in all_buttons:
            if await extra_wizard.text(update,context):return
        else:
            context.user_data.pop("extra_flow",None)
    if context.user_data.get("finance_flow"):
        if text not in all_buttons:
            if await finance_wizard.text(update,context):return
        else:
            context.user_data.pop("finance_flow",None)
    if context.user_data.get("correction"):
        if text not in all_buttons:
            if await edit_wizard.text_input(update,context):return
        else:
            context.user_data.pop("correction",None)
    if context.user_data.get("assistant_mode"):
        if text not in all_buttons:
            answer=pro_assistant.answer(db,pro_assistant.detect(text))
            return await update.message.reply_text(answer,parse_mode="HTML",reply_markup=pro_assistant.menu())
        else:
            context.user_data.pop("assistant_mode",None)
    if text in (BTN_IN,BTN_OUT):return await toggle_action(update,context,"in" if text==BTN_IN else "out")
    if text in (BTN_BREAK_START,BTN_BREAK_END):
        return await pause_action(update,context,"start" if text==BTN_BREAK_START else "end")
    if text=="🏠 Главная":return await screen(update,context,"home")
    if text=="💰 Зарплата":return await screen(update,context,"pay")
    if text in ("📅 Смены","📅 Мои смены"):return await screen(update,context,"shifts")
    if text=="📂 Отчёты":return await screen(update,context,"reports")
    if text=="🗓 План":return await screen(update,context,"plan")
    if text=="📚 Источники":return await screen(update,context,"sources")
    if text=="📊 Сверка":return await screen(update,context,"office")
    if text=="📸 Скриншоты":return await screen(update,context,"evidence")
    if text=="📥 Таблица":return await send_pretty_file(update,context,"main")
    if text=="📦 Архив":return await send_pretty_file(update,context,"sources")
    if text=="✏️ Исправить":return await screen(update,context,"edit")
    if text=="⚙️ Настройки":return await screen(update,context,"settings")
    if text=="🕒 Неделя":return await week(update,context)
    await screen(update,context,"home")

async def on_error(update,context):
    LOG.error("Update handler failure: %s",type(context.error).__name__,exc_info=context.error)
    db=context.application.bot_data.get("db")
    if isinstance(update,Update) and db is not None and authorized(update,db):
        try:await context.bot.send_message(chat_id=update.effective_chat.id,
             text="Не удалось выполнить действие. Проверь историю смен в главном меню.")
        except Exception:pass

def main():
    token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set; bot is staged but not started.")
    db=connect()
    # SQLite is authoritative; generate reports only on explicit export.
    app=ApplicationBuilder().token(token).concurrent_updates(False).build()
    app.bot_data["db"]=db
    commands=[
        ("start",started),("help",started),("pair",pairing),
        ("in",lambda u,c:toggle_action(u,c,"in")),("out",lambda u,c:toggle_action(u,c,"out")),
        ("month",summary),("history",history),("week",week),("office",office),
        ("compare",comparison),("export",backup_file),("manual",edit),("fix",edit),
        ("sources",source_view),("plan",plan_view),("storage",storage_view),("archive",lambda u,c:send_pretty_file(u,c,"sources")),
        ("screenshots",screenshot_view),
        ("break",break_time),("settings",settings),("rate",changed_setting),
        ("defaultbreak",changed_setting)]
    for name,handler in commands:app.add_handler(CommandHandler(name,handler))
    app.add_handler(CallbackQueryHandler(inline_action,pattern=r"^(noop$|series:|audit:|undo:|year:|ui:|calc:|reports:|ledger:|month:|hist:|cal:|day:|pause:|extra:|file:|edit:|fin:|helper:|office:)"))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE,received_photo))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE,received_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,on_text))
    app.add_error_handler(on_error)
    LOG.info("Starting private shift tracker with Telegram long polling")
    app.run_polling(allowed_updates=["message","callback_query"],drop_pending_updates=False,close_loop=False)

if __name__=="__main__":main()
