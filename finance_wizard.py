"""Safe interactive settings and payment receipts; all writes require confirmation."""
from datetime import datetime
from html import escape
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from core import TZ, default_break, put_config, log
from finance import schedule_rate, add_payment

def buttons(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=c) for t,c in row] for row in rows])

def cancel():
    return buttons([[("❌ Отмена","fin:cancel")]])

def response(flow):
    kind=flow["kind"];step=flow["step"]
    if step=="amount":
        return ("💵 <b>Новая ставка</b>\n\nНапиши сумму за час в рублях, например <code>350</code>." if kind=="rate"
          else "☕ <b>Перерыв для будущих смен</b>\n\nНапиши количество минут, например <code>30</code>.")
    if step=="month":
        return "💳 <b>Записать поступление зарплаты</b>\n\nЗа какой рабочий месяц? Пример: <code>2024-08</code>."
    if step=="period":
        return f"💳 <b>{flow['month']}</b>\n\nЭто первая или вторая часть зарплаты?"
    if step=="paid_on":
        return "📅 <b>Когда поступили деньги?</b>\n\nДата в формате <code>2024-08-16</code> или <code>16.11</code>."
    if step=="pay_amount":
        return "💰 <b>Сколько поступило?</b>\n\nВведи сумму в рублях: <code>28689.21</code>."
    if step=="confirm":
        if kind=="rate":
            return (f"✅ <b>Проверка изменения ставки</b>\n\n"
                 f"Новая ставка: {flow['amount']} ₽/час\n"
                 "Начнёт действовать с <b>завтрашнего дня</b>.\n"
                 "Ранее отработанные смены останутся по прежней ставке.")
        if kind=="break":
            return (f"✅ <b>Перерыв по умолчанию: {flow['amount']} минут</b>\n\n"
                "Будет применяться только при начале новых смен, не изменяя старые.")
        return (f"✅ <b>Запись поступления</b>\n\n"
               f"Месяц: {flow['month']}\n"
               f"Часть: {flow['period']}\n"
               f"Сумма: <b>{flow['amount']} ₽</b>\n"
               f"Дата поступления: {flow['paid_on']}\n\n"
               "Запись отдельная от начислений по часам.")
    return ""

def keys(flow):
    if flow["step"]=="period":
        return buttons([[("① Первая","fin:period:1"),("② Вторая","fin:period:2")],[("❌ Отмена","fin:cancel")]])
    if flow["step"]=="confirm":
        return buttons([[("✅ Сохранить","fin:save")],[("❌ Отмена","fin:cancel")]])
    return cancel()

async def send(update,context,flow):
    await context.bot.send_message(chat_id=update.effective_chat.id,
        text=response(flow),parse_mode="HTML",reply_markup=keys(flow))

async def callback(update,context,db):
    data=(update.callback_query.data or "").split(":")
    if data[0]!="fin" or len(data)<2:return False
    if data[1]=="cancel":
        context.user_data.pop("finance_flow",None)
        await context.bot.send_message(chat_id=update.effective_chat.id,
              text="Действие отменено. Ничего не изменено.")
        return True
    if data[1]=="new":
        kind=data[2] if len(data)>2 else ""
        if kind not in ("rate","break","payment"):return True
        context.user_data.pop("correction",None)
        context.user_data.pop("assistant_mode",None)
        context.user_data["finance_flow"]={"kind":kind,
             "step":"month" if kind=="payment" else "amount"}
    elif data[1]=="period":
        flow=context.user_data.get("finance_flow")
        if not flow or flow.get("step")!="period":return True
        flow["period"]=int(data[2])
        flow["step"]="paid_on"
    elif data[1]=="save":
        flow=context.user_data.get("finance_flow")
        if not flow or flow.get("step")!="confirm":return True
        try:
            if flow["kind"]=="rate":
                effective=schedule_rate(db,flow["amount"])
                msg=f"✅ Ставка {flow['amount']} ₽/ч будет действовать с {effective}."
            elif flow["kind"]=="break":
                mins=flow["amount"]
                if not 0<=mins<=600:raise ValueError("Перерыв от 0 до 600 минут")
                db.execute("BEGIN IMMEDIATE")
                try:
                    old=default_break(db)
                    put_config(db,"default_break_minutes",int(mins))
                    log(db,"setting",details={"kind":"break","before":old,"after":mins})
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
                msg=f"✅ Перерыв для новых смен — {mins} минут."
            else:
                payid=add_payment(db,flow["month"],flow["period"],flow["amount"],flow["paid_on"])
                msg=f"✅ Поступление записано, №{payid}. Начисления не изменены."
        except (ValueError,TypeError) as exc:
            context.user_data.pop("finance_flow",None)
            await context.bot.send_message(chat_id=update.effective_chat.id,text="⚠️ "+str(exc))
            return True
        context.user_data.pop("finance_flow",None)
        await context.bot.send_message(chat_id=update.effective_chat.id,text=msg)
        return "saved"
    else:return False
    flow=context.user_data.get("finance_flow")
    if flow:await send(update,context,flow)
    return True

def check_date(value):
    value=value.strip()
    for fmt in ("%Y-%m-%d","%d.%m.%Y","%d.%m"):
        try:
            d=datetime.strptime(value,fmt)
            if fmt=="%d.%m":d=d.replace(year=datetime.now(TZ).year)
            return d.strftime("%Y-%m-%d")
        except ValueError:pass
    raise ValueError("Дата должна быть 16.11 или 2024-08-16")

async def text(update,context):
    flow=context.user_data.get("finance_flow")
    if not flow:return False
    raw=(update.message.text or "").strip()
    try:
        step=flow["step"];kind=flow["kind"]
        if step=="amount":
            num=float(raw.replace(",","."))
            if kind=="rate" and not 1<=num<=100000:raise ValueError("Ставка: 1–100 000 ₽")
            if kind=="break" and (num!=int(num) or not 0<=num<=600):
                raise ValueError("Перерыв: целое число минут 0–600")
            flow["amount"]=int(num) if kind=="break" else round(num,2)
            flow["step"]="confirm"
        elif step=="month":
            month=datetime.strptime(raw,"%Y-%m").strftime("%Y-%m")
            flow["month"]=month;flow["step"]="period"
        elif step=="paid_on":
            flow["paid_on"]=check_date(raw);flow["step"]="pay_amount"
        elif step=="pay_amount":
            from finance import _amount
            flow["amount"]=str(_amount(raw))
            flow["step"]="confirm"
        else:return True
    except (ValueError,TypeError,OverflowError) as exc:
        await update.message.reply_text("⚠️ Неверное значение. "+str(exc)+"\nПопробуй ещё раз.",reply_markup=cancel())
        return True
    await send(update,context,flow)
    return True
