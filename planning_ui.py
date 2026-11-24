"""Button-first recurring-plan wizard. All writes require a nonce-bound preview."""
import json,secrets,time
from datetime import datetime,timedelta
from html import escape
from core import TZ
from extra_wizard import markup,parse_clock,parse_day
import planning

DAYS=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
PATTERNS={'2x2':'2 рабочих / 2 выходных','3x3':'3 рабочих / 3 выходных','5x2':'5 рабочих / 2 выходных','weekly':'Дни недели'}

def clear_other(context):
    for key in ('correction','finance_flow','extra_flow','undo_flow','series_flow'):
        context.user_data.pop(key,None)
    context.user_data['assistant_mode']=False

async def send(update,context,text,rows):
    await context.bot.send_message(update.effective_chat.id,text=text,parse_mode='HTML',reply_markup=markup(rows))

def _id(flow):return flow['token']
def _button(flow,label,command):return (label,'series:'+command+':'+_id(flow))

def prompt(db,flow):
    step=flow['step'];token=_id(flow);rows=[]
    cancel=[_button(flow,'Отмена','cancel')]
    if step=='pattern':
        text='<b>Повторяющийся график</b>\n\nВыбери шаблон. Сначала покажу даты, только потом запишу план. Реальные смены не создаются.'
        rows=[[_button(flow,'2 / 2','pattern:2x2'),_button(flow,'3 / 3','pattern:3x3')],[_button(flow,'Цикл 5 / 2','pattern:5x2'),_button(flow,'Дни недели','pattern:weekly')]]
    elif step=='weekdays':
        text='<b>Рабочие дни недели</b>\n\nНажми дни, затем «Продолжить». Остальные дни будут выходными.'
        rows=[[_button(flow,('✓ ' if d in flow['spec']['weekdays'] else '')+DAYS[d],'weekday:'+str(d)) for d in range(4)],[_button(flow,('✓ ' if d in flow['spec']['weekdays'] else '')+DAYS[d],'weekday:'+str(d)) for d in range(4,7)],[_button(flow,'Продолжить','weekdone')]]
    elif step=='start':
        text='<b>С какой даты?</b>\n\nДля цикла это первый рабочий день. Можно написать дату ДД.ММ.ГГГГ. Существующие записи останутся нетронутыми.'
        rows=[[_button(flow,'Завтра','start:tomorrow'),_button(flow,'Сегодня','start:today')]]
    elif step=='end':
        text='<b>До какой даты?</b>\n\nВыбери длину периода или напиши конечную дату ДД.ММ.ГГГГ. Максимум 730 дней. Скрытого бесконечного продления нет.'
        rows=[[_button(flow,'14 дней','span:14'),_button(flow,'30 дней','span:30')],[_button(flow,'90 дней','span:90'),_button(flow,'365 дней','span:365')]]
    elif step=='clock':
        text='<b>Часы рабочих смен</b>\n\nВыбери интервал или напиши своё время: <code>16:00 02:00</code>. Уход после полуночи относится к следующему дню.'
        rows=[[_button(flow,'16:00–02:00','clock:16:00:02:00'),_button(flow,'09:00–18:00','clock:09:00:18:00')]]
    elif step=='confirm':
        item=planning.preview(db,flow['spec']);flow['preview']=item
        spec=item['spec']
        text=(f"<b>Проверь график</b>\n{PATTERNS[spec['pattern']]} · {spec['start_time']}–{spec['end_time']}\n"
              f"{spec['start']} → {spec['end']}\n\n"
              f"Новых рабочих дней: <b>{item['shift_count']}</b>\nВыходных: {item['off_count']}\n"
              f"Пропущено занятых / начавшихся дат: {len(item['skipped'])}\n\n")
        text+='\n'.join(r['day']+' · '+('выходной' if r['status']=='off' else r['start_time']+'–'+r['end_time']) for r in item['days'][:8])
        text+='\n\n<i>Записывается только план. Ничего не начисляется и не отправляется по расписанию.</i>'
        if item['days']:rows=[[_button(flow,'Подтвердить график','save')]]
    elif step=='stop':
        item=planning.cancel_preview(db,flow['series_id']);flow['preview']=item
        text=(f"<b>Отменить будущую часть графика?</b>\n\nБудет убрано дат: <b>{len(item['days'])}</b>.\n"
              f"Сохранятся прошлые, изменённые и связанные с фактом даты: {len(item['kept'])}.\n\n"
              'История серии останется в журнале. Реальные смены и деньги не изменятся.')
        rows=[[_button(flow,'Подтвердить отмену','stop')]] if item['status']!='cancelled' else []
    else:raise ValueError('Неверный шаг.')
    return text,rows+[cancel]

async def show(update,context,db):
    text,rows=prompt(db,context.user_data['series_flow']);await send(update,context,text,rows)

async def callback(update,context,db):
    raw=update.callback_query.data or ''
    if not raw.startswith('series:'):return False
    parts=raw.split(':');command=parts[1]
    if command in ('home','new','list','inspect'):
        clear_other(context)
        if command=='home':
            await send(update,context,'<b>Мой график</b>\n\nПовторяющиеся смены с предпросмотром и защитой уже внесённых дней.',[[('Создать график','series:new')],[('Мои серии','series:list'),('Обычный план','ui:plan')],[('Главная','ui:home')]])
            return True
        if command=='list':
            rows=[]
            for series in planning.series_list(db):
                spec=json.loads(series['spec_json'])
                label=spec['start']+' → '+spec['end']+(' · отменён' if series['status']=='cancelled' else '')
                rows.append([(label,'series:inspect:'+series['id'])])
            await send(update,context,'<b>Созданные графики</b>\n\n'+('Выбери серию для безопасной отмены будущих дат.' if rows else 'Серий пока нет.'),rows+[[('Создать','series:new'),('Назад','series:home')]])
            return True
        flow={'token':secrets.token_hex(6),'nonce':secrets.token_hex(16),'created':time.time(),'spec':{},'step':'pattern'}
        if command=='inspect':
            if len(parts)!=3:return True
            flow.update(step='stop',series_id=parts[2])
        context.user_data['series_flow']=flow
    else:
        flow=context.user_data.get('series_flow')
        if not flow or len(parts)<3 or parts[-1]!=flow['token'] or time.time()-flow['created']>1800:
            await send(update,context,'Подтверждение устарело. Открой график заново.',[[('График','series:home')]])
            return True
        try:
            if command=='cancel':
                context.user_data.pop('series_flow',None);await send(update,context,'Изменения не применены.',[[('Главная','ui:home')]]);return True
            if command=='pattern' and flow['step']=='pattern' and parts[2] in PATTERNS:
                flow['spec'].update(pattern=parts[2],weekdays=[]);flow['step']='weekdays' if parts[2]=='weekly' else 'start'
            elif command=='weekday' and flow['step']=='weekdays':
                d=int(parts[2])
                if not 0<=d<=6:raise ValueError('Неверный день.')
                days=flow['spec']['weekdays']
                if d in days:days.remove(d)
                else:days.append(d)
            elif command=='weekdone' and flow['step']=='weekdays':
                if not flow['spec']['weekdays']:raise ValueError('Нужно выбрать хотя бы один день.')
                flow['step']='start'
            elif command=='start' and flow['step']=='start' and parts[2] in ('today','tomorrow'):
                flow['spec']['start']=(datetime.now(TZ).date()+timedelta(days=parts[2]=='tomorrow')).isoformat();flow['step']='end'
            elif command=='span' and flow['step']=='end':
                days=int(parts[2])
                if days not in (14,30,90,365):raise ValueError('Неверный период.')
                flow['spec']['end']=(datetime.fromisoformat(flow['spec']['start']).date()+timedelta(days=days-1)).isoformat();flow['step']='clock'
            elif command=='clock' and flow['step']=='clock' and len(parts)==7:
                flow['spec']['start_time']=parse_clock(parts[2]+':'+parts[3]);flow['spec']['end_time']=parse_clock(parts[4]+':'+parts[5]);flow['step']='confirm'
            elif command=='save' and flow['step']=='confirm':
                result=planning.apply(db,flow['spec'],flow['nonce'],flow['preview']['digest'])
                context.user_data.pop('series_flow',None)
                await send(update,context,f"График сохранён: {result['shifts']} рабочих дней, {result['off']} выходных.\nЗанятые даты пропущены: {result['skipped']}.",[[('Календарь','cal:'+flow['spec']['start'][:7]),('Мои серии','series:list')]])
                return 'saved'
            elif command=='stop' and flow['step']=='stop':
                result=planning.cancel(db,flow['series_id'],flow['preview']['digest'])
                context.user_data.pop('series_flow',None)
                await send(update,context,f"Будущих дат убрано: {result['removed']}. История сохранена.",[[('Мои серии','series:list'),('Главная','ui:home')]])
                return 'saved'
            else:raise ValueError('Эта кнопка относится к другому шагу.')
        except (ValueError,TypeError) as exc:
            await send(update,context,escape(str(exc)),[[('Начать заново','series:new'),('Главная','ui:home')]])
            return True
    try:await show(update,context,db)
    except (ValueError,TypeError) as exc:
        await send(update,context,escape(str(exc)),[[('График','series:home')]])
    return True

async def text(update,context,db):
    flow=context.user_data.get('series_flow')
    if not flow:return False
    if time.time()-flow['created']>1800:
        context.user_data.pop('series_flow',None);await send(update,context,'Время ввода истекло.',[[('График','series:home')]]);return True
    try:
        raw=(update.message.text or '').strip()
        if flow['step'] in ('start','end'):
            parsed=parse_day(raw);day=parsed.isoformat() if hasattr(parsed,'isoformat') else parsed
            if day<datetime.now(TZ).date().isoformat():raise ValueError('План начинается не раньше сегодня.')
            if flow['step']=='end' and (datetime.fromisoformat(day)-datetime.fromisoformat(flow['spec']['start'])).days not in range(730):raise ValueError('Конец должен быть после начала, период не длиннее 730 дней.')
            flow['spec'][flow['step']]=day;flow['step']='end' if flow['step']=='start' else 'clock'
        elif flow['step']=='clock':
            values=raw.replace('–',' ').replace('—',' ').split()
            if len(values)!=2:raise ValueError('Напиши два времени: 16:00 02:00.')
            flow['spec']['start_time']=parse_clock(values[0]);flow['spec']['end_time']=parse_clock(values[1])
            if flow['spec']['start_time']==flow['spec']['end_time']:raise ValueError('Время начала и окончания совпадает.')
            flow['step']='confirm'
        else:raise ValueError('На этом шаге используй кнопки.')
        await show(update,context,db)
    except (ValueError,TypeError) as exc:
        await send(update,context,escape(str(exc)),[[_button(flow,'Отмена','cancel')]])
    return True