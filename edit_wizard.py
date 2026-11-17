"""Private owner-only guided corrections; no changes until confirmation."""
from datetime import datetime, timedelta
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from core import TZ, corrected_shift, close_open_shift, open_shift, read_time, default_break, shifts, local_day, log

def markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=c) for t,c in row] for row in rows])

def cancel_markup():
    return markup([[("❌ Отмена","edit:cancel")]])

def date_from_text(value):
    value=value.strip()
    for fmt in ("%Y-%m-%d","%d.%m.%Y","%d.%m"):
        try:
            parsed=datetime.strptime(value,fmt)
            if fmt=="%d.%m":
                parsed=parsed.replace(year=datetime.now(TZ).year)
            if parsed.date() > datetime.now(TZ).date()+timedelta(days=1):
                raise ValueError("Нельзя заранее учитывать будущую смену")
            return parsed.date().isoformat()
        except ValueError:
            pass
    raise ValueError("Напиши дату как 16.11 или 2024-07-16")

def parse_clock(value):
    x=value.strip().replace(".",":")
    dt=datetime.strptime(x,"%H:%M")
    return dt.strftime("%H:%M")

def choice_prompt(flow):
    if flow["step"]=="date":
        return ("🗓 <b>Какого числа была смена?</b>\n\n"
                "Напиши дату, например <code>15.11</code>.")
    if flow["step"]=="start":
        return (f"🟢 <b>Приход: {flow['date']}</b>\n\n"
                "Во сколько пришёл? Например <code>16:08</code>.")
    if flow["step"]=="end":
        return ("🔴 <b>Время ухода</b>\n\n"
                "Во сколько ушёл? Например <code>02:10</code>.\n"
                "Если после полуночи — дату определю автоматически.")
    if flow["step"]=="break":
        return ("☕ <b>Сколько длился перерыв?</b>\n\n"
                "Выбери вариант кнопкой или напиши число минут.")
    if flow["step"]=="confirm":
        end_next=flow["end"] <= flow["start"]
        status="Следующие сутки" if end_next else "Тот же день"
        action="Закрыть забытую смену" if flow.get("close_open") else "Исправить смену" if flow.get("shift_id") else "Добавить смену"
        return (f"✅ <b>Проверка записи</b>\n\n"
                f"Действие: {action}\n"
                f"Дата: <b>{flow['date']}</b>\n"
                f"Приход: <b>{flow['start']}</b>\n"
                f"Уход: <b>{flow['end']}</b> ({status.lower()})\n"
                f"Перерыв: <b>{flow['break_minutes']} мин</b>\n\n"
                "Если всё верно, нажми «Сохранить».")
    return ""

def step_keyboard(flow):
    if flow["step"]=="break":
        return markup([
          [("0 мин","edit:mins:0"),("15 мин","edit:mins:15"),("30 мин","edit:mins:30")],
          [("45 мин","edit:mins:45"),("60 мин","edit:mins:60")],
          [("❌ Отмена","edit:cancel")]])
    if flow["step"]=="confirm":
        return markup([[("✅ Сохранить","edit:save")],[("❌ Отмена","edit:cancel")]])
    return cancel_markup()

async def reply(update,context,text,keyboard):
    if update.callback_query:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,text=text,
            parse_mode="HTML",reply_markup=keyboard)
    else:
        await update.message.reply_text(text,parse_mode="HTML",reply_markup=keyboard)

async def callback(update,context,db):
    q=update.callback_query
    action=(q.data or "").split(":")
    if len(action)<2 or action[0]!="edit":return False
    if action[1]=="cancel":
        context.user_data.pop("correction",None)
        await reply(update,context,"Исправление отменено. Данные не менялись.",markup([[("🏠 Главная","ui:home")]]))
        return True
    if action[1]=="new":
        mode=action[2] if len(action)>2 else "date"
        today=datetime.now(TZ).date()
        if mode=="today":day=today.isoformat()
        elif mode=="yesterday":day=(today-timedelta(days=1)).isoformat()
        else:day=None
        context.user_data["correction"]={
            "step":"start" if day else "date",
            "date":day,"shift_id":None,"break_minutes":default_break(db)}
    elif action[1]=="close":
        s=open_shift(db)
        if not s:
            await reply(update,context,"Сейчас нет открытой смены.",cancel_markup())
            return True
        context.user_data["correction"]={
             "step":"end","date":local_day(s["start_utc"]),
             "start":read_time(s["start_utc"]).strftime("%H:%M"),
             "shift_id":s["id"],"break_minutes":s["break_minutes"],"close_open":True}
    elif action[1]=="day" and len(action)>2:
        try:
            target=date_from_text(action[2])
            if target>datetime.now(TZ).date().isoformat():
                raise ValueError("Будущие смены не добавляются")
        except ValueError:
            await reply(update,context,"Некорректная дата. Открой календарь заново.",cancel_markup())
            return True
        context.user_data["correction"]={
            "step":"start","date":target,"shift_id":None,
            "break_minutes":default_break(db)}
    elif action[1]=="fix" and len(action)>2:
        try:shift_id=int(action[2])
        except ValueError:return True
        s=db.execute("SELECT * FROM shifts WHERE id=? AND end_utc IS NOT NULL",(shift_id,)).fetchone()
        if not s:
            await reply(update,context,"Смена не найдена или ещё открыта.",cancel_markup())
            return True
        context.user_data["correction"]={
           "step":"start","date":local_day(s["start_utc"]),
           "shift_id":s["id"],"break_minutes":s["break_minutes"]}
    elif action[1]=="mins":
        flow=context.user_data.get("correction")
        if not flow or flow["step"]!="break":
            await reply(update,context,"Начни исправление заново.",cancel_markup())
            return True
        mins=int(action[2])
        if mins not in (0,15,30,45,60):return True
        flow["break_minutes"]=mins
        flow["step"]="confirm"
    elif action[1]=="save":
        flow=context.user_data.get("correction")
        if not flow or flow["step"]!="confirm":
            await reply(update,context,"Подтверждение устарело. Начни заново.",cancel_markup())
            return True
        try:
            if flow.get("close_open"):
                sid=close_open_shift(db,flow["shift_id"],flow["end"],flow["break_minutes"])
            else:
                sid=corrected_shift(db,flow["date"],flow["start"],flow["end"],
                                    flow["break_minutes"],flow.get("shift_id"))
        except (ValueError,TypeError) as exc:
            await reply(update,context,
                "Не получилось сохранить: "+escape(str(exc)),
                markup([[("📝 Исправить выбор","ui:edit")]]))
            context.user_data.pop("correction",None)
            return True
        context.user_data.pop("correction",None)
        await reply(update,context,
             f"✅ <b>Смена №{sid} сохранена</b>\n\n"
             "Изменение занесено в журнал. Офисный табель не затронут.",
             markup([[("📅 Мои смены","ui:shifts"),("🏠 Главная","ui:home")]]))
        return "saved"
    else:return False
    flow=context.user_data.get("correction")
    if flow:
        await reply(update,context,choice_prompt(flow),step_keyboard(flow))
    return True

async def text_input(update,context):
    flow=context.user_data.get("correction")
    if not flow:return False
    text=(update.message.text or "").strip()
    try:
        if flow["step"]=="date":
            flow["date"]=date_from_text(text)
            flow["step"]="start"
        elif flow["step"]=="start":
            flow["start"]=parse_clock(text)
            flow["step"]="end"
        elif flow["step"]=="end":
            flow["end"]=parse_clock(text)
            if flow["end"]==flow["start"]:raise ValueError("Время ухода должно отличаться от прихода")
            flow["step"]="break"
        elif flow["step"]=="break":
            minutes=int(text)
            if minutes<0 or minutes>600:raise ValueError("Перерыв от 0 до 600 минут")
            flow["break_minutes"]=minutes
            flow["step"]="confirm"
        else:
            return True
    except (TypeError,ValueError) as exc:
        await update.message.reply_text(
            "⚠️ "+str(exc)+"\nПопробуй ещё раз или нажми «Отмена».",
            reply_markup=step_keyboard(flow))
        return True
    await update.message.reply_text(choice_prompt(flow),
           parse_mode="HTML",reply_markup=step_keyboard(flow))
    return True