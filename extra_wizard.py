"""Button-first, confirmation-required workflows for schedules and pay adjustments."""
from datetime import datetime
from html import escape
import confirmation
import calendar_plan
import batch_plan
import workflows
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

def valid_plan_day(day):
    # Single-day retrospective changes are deliberate, confirmed and audited.
    _validate_day(day)
    return day

def flow_prompt(flow):
    if flow["kind"]=="batch_plan":return batch_plan.prompt(flow)
    if flow["kind"]=="plan":
        if flow["step"]=="start":return "🕒 <b>Время прихода по плану</b>\n\nНапиши, например, <code>16:00</code>."
        if flow["step"]=="end":return "🌙 <b>Время окончания по плану</b>\n\nНапиши <code>02:00</code>. Ночная смена будет учтена правильно."
        if flow["step"]=="confirm":
            status=("Не задан (план снят)" if flow["status"]=="unset" else "🌿 Выходной" if flow["status"]=="off"
                else f"🟡 Рабочий день · {flow['start']}–{flow['end']}")
            return (f"🗓 <b>Проверь изменение плана</b>\n\n"
                f"Дата: <b>{flow['day']}</b>\n"
                f"Было: {escape(flow.get('before_label','Не задан'))}\n"
                f"Будет: {status}\n"
                + ("Прошедшая дата: уточняется только план.\n" if flow['day']<datetime.now(TZ).date().isoformat() else "")
                + ("Уход на следующий день.\n" if flow["status"]=="shift" and flow["end"]<flow["start"] else "")
                + "\nИзменится только выбранный день, не весь повторяющийся график.\n"
                +
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
        return markup([[("✅ Сохранить","extra:save:"+confirmation.issue(flow))],[("❌ Отменить","extra:cancel")]])
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
            if fmt=="%d.%m":
                t=datetime.strptime(f"{raw}.{datetime.now(TZ).year}","%d.%m.%Y")
            else:
                t=datetime.strptime(raw,fmt)
            return _validate_day(t.date().isoformat()).isoformat()
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
        try:valid_plan_day(day)
        except ValueError as exc:
            await send(update,context,escape(str(exc)),markup([[("🗓 Календарь",f"cal:{day[:7]}")]]))
            return True
        flow={"kind":"plan","day":day,"status":action if action in ("off","unset") else "shift",
              "expected_revision":calendar_plan.revision(db,day),
              "before_label":calendar_plan.label(workflows.plan_day(db,day))}
        if action in ("off","unset"):
            flow["step"]="confirm"
        elif action in ("quick","work"):
            previous=workflows.plan_day(db,day)
            begin=previous.get("start") if previous and previous["status"]=="shift" else None
            finish=previous.get("end") if previous and previous["status"]=="shift" else None
            flow.update({"start":begin or "16:00","end":finish or "02:00","step":"confirm"})
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
        if not flow or flow["step"]!="confirm" or not confirmation.valid(flow,tokens[2] if len(tokens)==3 else None):
            await send(update,context,"Это подтверждение устарело. Открой раздел ещё раз.",
                 markup([[("🏠 Главная","ui:home")]]))
            return True
        try:
            if flow["kind"]=="batch_plan":
                result=batch_plan.apply(db,flow)
                message=f"✅ План обновлён для {result['count']} дат. Фактические смены и деньги не изменены."
            elif flow["kind"]=="plan":
                calendar_plan.apply(db,flow)
                message="✅ План на "+flow["day"]+" сохранён."
            else:
                num=add_adjustment(db,flow["day"],flow["category"],flow["amount"])
                message=f"✅ Запись №{num} сохранена в выплатах."
        except (ValueError,TypeError) as exc:
            if flow["kind"] in ("plan","batch_plan"):
                context.user_data.pop("extra_flow",None)
                await send(update,context,"⚠️ "+escape(str(exc)),markup([[("Открыть календарь","cal:"+flow["month"]) if flow["kind"]=="batch_plan" else ("Открыть день","day:"+flow["day"])]]))
            else:
                await send(update,context,"⚠️ "+escape(str(exc)),actions(flow))
            return True
        context.user_data.pop("extra_flow",None)
        await send(update,context,message,markup([
          [("Календарь","cal:"+flow["month"]) if flow["kind"]=="batch_plan" else ("📅 Открыть день","day:"+flow["day"]) if flow["kind"]=="plan" else
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


async def begin_calendar_draft(update,context,db,payload):
    """Owner-only boundary; an untrusted link is never authorization."""
    from bot import authorized
    if not authorized(update,db):return
    for key in ("extra_flow","finance_flow","correction","series_flow","undo_flow"):
        context.user_data.pop(key,None)
    context.user_data["assistant_mode"]=False
    try:
        day,status,start,end,expected=calendar_plan.parse(payload)
        selected=calendar_plan.flow(db,day,status,start,end,expected)
    except (ValueError,TypeError) as exc:
        await send(update,context,escape(str(exc)),markup([[("Календарь","cal:"+datetime.now(TZ).strftime("%Y-%m"))]]))
        return
    context.user_data["extra_flow"]=selected
    await send(update,context,flow_prompt(selected),actions(selected))


async def begin_batch_draft(update,context,db,payload):
    from bot import authorized
    if not authorized(update,db):return
    for key in ("extra_flow","finance_flow","correction","series_flow","undo_flow","await_office_document"):
        context.user_data.pop(key,None)
    context.user_data["assistant_mode"]=False
    try:
        month,days,status,start,end,expected=batch_plan.parse(payload)
        selected=batch_plan.flow(db,month,days,status,start,end,expected)
    except (ValueError,TypeError) as exc:
        await send(update,context,escape(str(exc)),markup([[("Календарь","cal:"+datetime.now(TZ).strftime("%Y-%m"))]]))
        return
    context.user_data["extra_flow"]=selected
    await send(update,context,flow_prompt(selected),actions(selected))
