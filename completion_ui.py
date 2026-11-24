"""Explicit history/undo and annual navigation for the private chat."""
import secrets,time
from datetime import datetime
from html import escape
from extra_wizard import markup
from core import TZ
from analytics import year_analytics
import revisions

async def _send(update,context,text,rows):
    await context.bot.send_message(update.effective_chat.id,text=text,parse_mode='HTML',reply_markup=markup(rows))

def year_view(db,year):
    from friendly_ui import money,duration
    data=year_analytics(db,year)
    text=(f"<b>Годовой обзор · {year}</b>\n\n"
          f"{duration(data['hours'])} · {data['shifts']} закрытых смен\n"
          f"За часы: <b>{money(data['earned'])}</b>\n"
          f"С корректировками: <b>{money(data['total'])}</b>\n"
          f"Внесено поступлений: <b>{money(data['received'])}</b>\n\n")
    for month in data['months']:
        text+=month['title'].split()[0]+f" · {duration(month['hours'])} · {money(month['earned'])}\n"
    text+='\n<i>План не включён. Поступления отнесены к рабочему месяцу. Налог и задолженность не предполагаются.</i>'
    buttons=[]
    navigation=[]
    if year>2000:navigation.append(('‹ Год',f'year:{year-1}'))
    if year<2100:navigation.append(('Год ›',f'year:{year+1}'))
    if navigation:buttons.append(navigation)
    for start in range(0,12,3):buttons.append([(m['title'].split()[0],f"month:{m['month']}") for m in data['months'][start:start+3]])
    buttons += [[('PDF за год',f'file:yearpdf:{year}'),('Главная','ui:home')]]
    return text,markup(buttons)

async def callback(update,context,db):
    raw=update.callback_query.data or ''
    if raw.startswith('year:'):
        try:year=int(raw[5:]);text,buttons=year_view(db,year)
        except (ValueError,TypeError):return True
        from telegram.error import BadRequest
        try:await update.callback_query.edit_message_text(text,parse_mode='HTML',reply_markup=buttons)
        except BadRequest as exc:
            if 'not modified' not in str(exc).lower():raise
        return True
    if raw.startswith('audit:'):
        try:
            page=int(raw[6:]);rows=revisions.history(db,page*8)
        except (ValueError,TypeError):return True
        text='<b>История изменений</b>\n\n';buttons=[]
        for row in rows:
            when=datetime.fromisoformat(row['at']).astimezone(TZ).strftime('%d.%m %H:%M')
            text+=f"{when} · {escape(row['label'])}"+(' · отменено' if row['reversed'] else '')+'\n'
            if row['undo_available']:buttons.append([(f"Отменить: {when} · {row['label']}",f"undo:view:{row['id']}")])
        if not rows:text+='Событий на этой странице нет.\n'
        text+='\n<i>Отмена требует подтверждения. Новые изменения защищены; исходная запись сохраняется в архиве отмен.</i>'
        nav=[]
        if page>0:nav.append(('‹ Новее',f'audit:{page-1}'))
        if len(rows)==8:nav.append(('Старее ›',f'audit:{page+1}'))
        if nav:buttons.append(nav)
        buttons.append([('Графики','series:list'),('Главная','ui:home')])
        await _send(update,context,text,buttons);return True
    if not raw.startswith('undo:'):return False
    parts=raw.split(':')
    try:
        if len(parts)==3 and parts[1]=='view':
            item=revisions.preview(db,int(parts[2]))
            for key in ('series_flow','extra_flow','finance_flow','correction'):context.user_data.pop(key,None)
            flow={'token':secrets.token_hex(8),'created':time.time(),'event_id':item['id'],'digest':item['digest']}
            context.user_data['undo_flow']=flow
            await _send(update,context,'<b>Проверь отмену</b>\n\n'+escape(item['description'])+'\n\n'+escape(item['effect'])+'\n\nБез подтверждения ничего не изменится.',[[('Подтвердить отмену','undo:confirm:'+flow['token'])],[('Не отменять','undo:cancel:'+flow['token'])]])
        elif len(parts)==3 and parts[1] in ('confirm','cancel'):
            flow=context.user_data.get('undo_flow')
            if not flow or flow['token']!=parts[2] or time.time()-flow['created']>600:raise ValueError('Подтверждение устарело. Открой историю заново.')
            if parts[1]=='cancel':message='Запись оставлена без изменений.'
            else:
                result=revisions.reverse(db,flow['event_id'],flow['digest'])
                message='Операция уже отменена.' if result['already_reversed'] else 'Операция отменена. Исходная запись сохранена в журнале отмен; отчёты пересчитаются при открытии.'
            context.user_data.pop('undo_flow',None)
            await _send(update,context,message,[[('История','audit:0'),('Главная','ui:home')]])
        else:return True
    except (ValueError,TypeError) as exc:
        await _send(update,context,escape(str(exc)),[[('История','audit:0'),('Исправить время','ui:edit')]])
    return True