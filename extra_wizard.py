"""Button-first, confirmation-required workflows for schedules and pay adjustments."""
from datetime import datetime
from html import escape
from telegram import InlineKeyboardMarkup,InlineKeyboardButton
from core import TZ
from workflows import set_plan,add_adjustment,_validate_day

CATEGORIES={
 "bonus":"🎁 Премия",
 "allowance":"➕ Доплата",
 "reimbursement":"🚗 Возмещение расходов",
 "deduction":"➖ Удержание"
}

def markup(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=c) for t,c in row] for row in rows])

def valid_future(day):
    target=_validate_day(day)
    if target<datetime.now(TZ).date():
        raise ValueError("План за прошедший день не изменяется.")
    return day

def flow_prompt(flow):
    if flow["kind"]=="plan":
        if flow["step"]=="start":return "🕒 <b>Время прихода по плану</b>\n\nНапиши, например, <code>16:00</code>."
        if flow["step"]=="end":return "🌙 <b>Время окончания по плану</b>\n\nНапиши <code>02:00</code>. Ночная смена будет учтена правильно."
        if flow["step"]=="confirm":
            status=("🌿 Выходной" if flow["status"]=="off"
                else f"🟡 Рабочий день · {flow['start']}–{flow['end']}")
            return (f"🗓 <b>Проверь изменение плана</b>\n\n"
                f"Дата: <b>{flow['day']}</b>\n"
                f"Статус: {status}\n\n"
                "<i>Это только план; фактические часы и заработок не изменятся.</i>")
    if flow["kind"]=="adjustment":
        if flow["step"]=="amount":
            return (f"💰 <b>{CATEGORIES[flow['category']]}</b>\n\n"
                "Напиши сумму в рублях, например <code>1250.50</code>.")
        if flow["step"]=="date":
            return ("📆 <b>За какую дату записать сумму?</b>\n\n"
                "Напиши дату в формате <code>17.11</code> или <code>2026-11-17</code>.\n"
                "Можно ответить <code>сегодня</code>.")
        if flow["step"]=="confirm":
            return (f"✅ <b>Подтверждение</b>\n\n"
                f"Вид: {CATEGORIES[flow['category']]}\n"
                f"Дата: <b>{flow['day']}</b>\n"
                f"Сумма: <b>{flow['amount']} ₽</b>\n\n"
                "<i>Запись отдельна от почасовой зарплаты и будет видна в отчётах.</i>")
    return "Выбери действие."

def actions(flow):
    if flow["step"]=="confirm":
        return markup([[("✅ Сохранить","extra:save")],[("❌ Отменить","extra:cancel")]])
    return markup([[("❌ Отменить","extra:cancel")]])

async def send(update,context,text,keys):
    await context.bot.send_message(chat_id=update.effective_chat.id,text=text,
                      parse_mode="HTML",reply_markup=keys)

def parse_clock(raw):
    x=raw.strip().replace(".",":")
    try:
        t=datetime.strptime(x,"%H:%M")
    except ValueError as exc:
        raise ValueError("Время должно быть в формате ЧЧ:ММ.") from exc
    return t.strftime("%H:%M")

def parse_day(raw):
    raw=raw.strip().lower()
    if raw=="сегодня":return datetime.now(TZ).date().isoformat()
    for fmt in ("%d.%m","%d.%m.%Y","%Y-%m-%d"):
        try:
            t=datetime.strptime(raw,fmt)
            if fmt=="%d.%m":t=t.replace(year=datetime.now(TZ).year)
            return _validate_day(t.date().isoformat())
        except ValueError:
            pass
    raise ValueError("Введи дату 17.11 или 2026-11-17.")

async def callback(update,context,db):
    tokens=(update.callback_query.data or "").split(":")
    if tokens[0]!="extra":return False
    cmd=tokens[1] if len(tokens)>1 else ""
    if cmd=="cancel":
        context.user_data.pop("extra_flow",None)
        await send(update,context,"Отменено, изменения не сохранены.",
             markup([[("🏠 Главная","ui:home")]]))
        return True
    if cmd=="plan" and len(tokens)==4:
        _,_,action,day=tokens
        try:valid_future(day)
        except ValueError as exc:
            await send(update,context,escape(str(exc)),markup([[("🗓 Календарь",f"cal:{day[:7]}")]]))
            return True
        flow={"kind":"plan","day":day,"status":"off" if action=="off" else "shift"}
        if action=="off":
            flow["step"]="confirm"
        elif action=="quick":
            flow.update({"start":"16:00","end":"02:00","step":"confirm"})
        elif action=="custom":
            flow["step"]="start"
        else:return True
        context.user_data["extra_flow"]=flow
    elif cmd=="adjust" and len(tokens)==3:
        category=tokens[2]
        if category not in CATEGORIES:return True
        context.user_data["extra_flow"]={"kind":"adjustment","category":category,"step":"amount"}
    elif cmd=="save":
        flow=context.user_data.get("extra_flow")
        if not flow or flow["step"]!="confirm":
            await send(update,context,"Это подтверждение устарело. Открой раздел ещё раз.",
                 markup([[("🏠 Главная","ui:home")]]))
            return True
        try:
            if flow["kind"]=="plan":
                set_plan(db,flow["day"],flow["status"],
                         flow.get("start"),flow.get("end"))
                message="✅ План на "+flow["day"]+" сохранён."
            else:
                num=add_adjustment(db,flow["day"],flow["category"],flow["amount"])
                message=f"✅ Запись №{num} сохранена в выплатах."
        except (ValueError,TypeError) as exc:
            await send(update,context,"⚠️ "+escape(str(exc)),actions(flow))
            return True
        context.user_data.pop("extra_flow",None)
        await send(update,context,message,markup([
          [("📅 Календарь","cal:"+flow["day"][:7]) if flow["kind"]=="plan" else
           ("💰 Зарплата","ui:pay")],
          [("🏠 Главная","ui:home")]]))
        return "saved"
    else:return False
    flow=context.user_data.get("extra_flow")
    if flow:await send(update,context,flow_prompt(flow),actions(flow))
    return True

async def text(update,context):
    flow=context.user_data.get("extra_flow")
    if not flow:return False
    raw=(update.message.text or "").strip()
    try:
        if flow["kind"]=="plan":
            if flow["step"]=="start":
                flow["start"]=parse_clock(raw)
                flow["step"]="end"
            elif flow["step"]=="end":
                flow["end"]=parse_clock(raw)
                if flow["start"]==flow["end"]:raise ValueError("Приход и уход совпадают.")
                flow["step"]="confirm"
            else:return True
        else:
            if flow["step"]=="amount":
                from finance import _amount
                flow["amount"]=str(_amount(raw))
                flow["step"]="date"
            elif flow["step"]=="date":
                flow["day"]=parse_day(raw)
                flow["step"]="confirm"
            else:return True
    except ValueError as exc:
        await update.message.reply_text("⚠️ "+str(exc),reply_markup=actions(flow))
        return True
    await update.message.reply_text(flow_prompt(flow),
       parse_mode="HTML",reply_markup=actions(flow))
    return True
