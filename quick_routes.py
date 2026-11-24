"""Strict owner-only navigation from Mini App. Links never save accounting data."""
import re
from ledger import parse_month
from workflows import _validate_day


def parse_destination(payload):
    if not isinstance(payload,str) or len(payload)>64:raise ValueError('Некорректная ссылка.')
    exact=re.fullmatch(r'q_fix_([0-9]{8})_([1-9][0-9]{0,15})',payload)
    if exact:
        stamp,number=exact.groups();day=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:]
        _validate_day(day);shift_id=int(number)
        if shift_id>9007199254740991:raise ValueError('Некорректный номер смены.')
        return 'fix',(day,shift_id)
    if payload in ('q_plan','q_edit','q_home'):
        return payload[2:],None
    match=re.fullmatch(r'q_(reports|payment|day)_(\d{6}|\d{8})',payload)
    if not match:raise ValueError('Некорректная ссылка.')
    destination,stamp=match.groups()
    if destination=='day':
        if len(stamp)!=8:raise ValueError('Неверная дата.')
        value=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:]
        _validate_day(value)
    else:
        if len(stamp)!=6:raise ValueError('Неверный месяц.')
        value=stamp[:4]+'-'+stamp[4:];parse_month(value)
    return destination,value


async def open_destination(update,context,payload):
    import bot,finance_wizard
    db=context.application.bot_data['db']
    if not bot.authorized(update,db):
        await bot.denied(update,context);return
    try:destination,value=parse_destination(payload)
    except ValueError:
        await update.message.reply_text('Ссылка устарела или повреждена. Открой нужный раздел в кабинете заново.')
        return
    for key in ('extra_flow','finance_flow','correction','series_flow','undo_flow','await_office_document'):
        context.user_data.pop(key,None)
    context.user_data['assistant_mode']=False
    if destination=='fix':
        import edit_wizard
        day,shift_id=value
        try:flow=edit_wizard.existing_shift_flow(db,shift_id,day)
        except ValueError as exc:
            await update.message.reply_text(str(exc));return
        context.user_data['correction']=flow
        await edit_wizard.reply(update,context,edit_wizard.choice_prompt(flow),edit_wizard.step_keyboard(flow))
    elif destination=='payment':
        flow={'kind':'payment','step':'period','month':value}
        context.user_data['finance_flow']=flow
        await finance_wizard.send(update,context,flow)
    elif destination=='reports':await bot.screen(update,context,'reports',value)
    elif destination=='day':await bot.screen(update,context,'day',value)
    else:await bot.screen(update,context,destination)
