"""Month-first reporting built from the shared read-only ledger.

A report is a dated snapshot, not a second database. Native tables, sortable dates,
filter-aware totals and cached formulas are supported by the existing exporter.
"""
from datetime import datetime, date
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from ledger import month_ledger
from report_catalog import make_sheet, addrow, save, finish_table, formula, NAVY, WHITE, INK, MINT, AMBER

MONEY='#,##0.00 "₽";[Red](#,##0.00) "₽";"—"'
DURATION='[h]:mm'


def monthly_report(db,month,path=None):
    data=month_ledger(db,month)
    book=Workbook()
    snapshot=datetime.fromisoformat(data['generated_at']).strftime('%d.%m.%Y %H:%M')
    overview=make_sheet(book,'Обзор','МОИ СМЕНЫ / '+data['title'].upper(),
        f'Срез на {snapshot} МСК · это отчёт, а не форма ввода. Изменения Excel не попадают в бот.',
        ['Показатель','Значение','Основание',''],[26,23,30,35])
    # Compact overview first; details and their formulas are on separate sheets.
    metrics=[
        ('Отработано',data['summary']['hours']/24,'Только завершённые смены','Табель'),
        ('Начислено за часы',data['summary']['earned'],'Ставка на дату каждой смены','Смены'),
        ('Корректировки',data['summary']['additional']['net_adjustment'],'Доплаты + возмещения − удержания','Деньги'),
        ('Расчётная сумма',data['summary']['total'],'За часы + корректировки','Деньги'),
        ('Поступило',data['summary']['received'],'Введённые поступления, не начисление','Деньги'),
    ]
    for i,values in enumerate(metrics,7):
        addrow(overview,i,values,MINT if i==10 else None)
        overview.cell(i,2).number_format=DURATION if i==7 else MONEY
        overview.cell(i,4).hyperlink="#'"+values[3]+"'!A1"
        overview.cell(i,4).font=Font(name='Calibri',size=11,color='2463B3',underline='single')
    # Separate periods: amounts received are never subtracted as an alleged debt.
    overview.merge_cells('A13:D13');overview['A13']='ДВЕ ЧАСТИ МЕСЯЦА'
    overview['A13'].font=Font(name='Calibri',bold=True,size=12,color=INK)
    for j,label in enumerate(('Период','Часы','Начислено с доплатами','Поступило'),1):
        c=overview.cell(14,j,label);c.font=Font(bold=True,color=WHITE);c.fill=PatternFill('solid',fgColor=NAVY)
        c.alignment=Alignment(wrap_text=True,vertical='center')
    overview.row_dimensions[14].height=30
    for i,part in enumerate(data['periods'],15):
        addrow(overview,i,['1–15' if part['period']==1 else '16–конец',part['hours']/24,part['total'],part['received']])
        overview.cell(i,2).number_format=DURATION
        for j in (3,4):overview.cell(i,j).number_format=MONEY
    overview.merge_cells('A18:D19');overview['A18']='Поступления и начисления имеют разную природу. Налоги и задолженность автоматически не вычисляются.'
    overview['A18'].alignment=Alignment(wrap_text=True,vertical='center')
    overview['A18'].font=Font(name='Calibri',size=10,color=INK)
    forecast=data['forecast']
    overview.merge_cells('A36:D36');overview['A36']='ПЛАН ВПЕРЕДИ / НЕ НАЧИСЛЕНО'
    overview['A36'].font=Font(name='Calibri',bold=True,size=12,color=INK)
    for i,vals in enumerate([
        ('Будущих смен',forecast['future_shifts'],'Не начаты и не отработаны',''),
        ('Ожидаемое время',forecast['future_hours']/24,f"Перерыв: {forecast['assumed_break_minutes']} мин / смена",''),
        ('Ожидаемо за план',forecast['future_pay'],'По ставкам на будущие даты',''),
    ],37):
        addrow(overview,i,vals,AMBER)
    overview['B38'].number_format=DURATION;overview['B39'].number_format=MONEY
    overview.merge_cells('A41:D42');overview['A41']='План не включён в начисления. Открытая смена в прогноз не входит. Неподтверждённый офисный табель не считается нулевыми часами.'
    overview['A41'].alignment=Alignment(wrap_text=True,vertical='center')
    overview['A41'].font=Font(name='Calibri',size=10,color=INK)

    detail=make_sheet(book,'Смены','РАСШИФРОВКА / '+data['title'].upper(),
        'Ночная смена относится к приходу. Время МСК. Часы и начисления рассчитаны формулами; ставки сохранены на дату смены.',
        ['Дата смены','Приход','Уход','Перерыв, мин','Отработано','Ставка, ₽/ч','За часы, ₽','Источник','№'],
        [16,23,23,15,16,18,19,19,9])
    all_records=[r for day in data['days'] for r in day['records']]
    for i,r in enumerate(all_records,7):
        started=datetime.fromisoformat(r['start_iso']).replace(tzinfo=None)
        ended=datetime.fromisoformat(r['end_iso']).replace(tzinfo=None) if r['end_iso'] else None
        addrow(detail,i,[date.fromisoformat(r['day']),started,ended,r['break_minutes'],None,r['rate'],None,
              {'import':'Импорт','manual':'Ручная запись','button':'Кнопка'}[r['source']],r['id']])
        detail.cell(i,1).number_format='dd.mm.yyyy'
        for col in (2,3):detail.cell(i,col).number_format='dd.mm hh:mm:ss'
        formula(book,detail,f'E{i}',f'=IF(C{i}="","",MAX(0,C{i}-B{i}-D{i}/1440))',r['hours']/24 if ended else None)
        formula(book,detail,f'G{i}',f'=IF(C{i}="","",ROUND(ROUND(E{i}*86400,0)*F{i}/3600,2))',r['earnings'])
        detail.cell(i,5).number_format=DURATION
        for col in (6,7):detail.cell(i,col).number_format=MONEY
    detail_last=max(7,6+len(all_records))
    if all_records:
        finish_table(detail,6+len(all_records),9,{5:data['summary']['hours']/24,7:data['summary']['earned']})
    else:
        addrow(detail,7,['В этом месяце фактических смен нет.']+[None]*8)
    tab=make_sheet(book,'Табель','ТАБЕЛЬ / '+data['title'].upper(),
        'Каждая дата месяца. План ≠ факт. Пустая ячейка офиса = нет подтверждённых данных. Отработано: часы:минуты.',
        ['Дата','План','Факт · МСК','Перерыв, мин','Отработано','За часы, ₽','Офис','Δ, мин','Статус'],
        [17,20,24,14,15,18,15,13,30])
    for i,r in enumerate(data['days'],7):
        p=r['plan']
        plan='Выходной' if p and p['status']=='off' else ((p['start'] or '—')+'–'+(p['end'] or '—')) if p else None
        fact='; '.join(x['start']+'–'+(x['end'] or '…')+(' +1' if x['next_day'] else '') for x in r['records']) or None
        status=r['status_label']
        if r['comparison']=='difference':status+=' · расхождение'
        elif r['comparison']=='matched':status+=' · сверено'
        elif r['closed_shifts'] and r['office_hours'] is None:status+=' · нет табеля'
        addrow(tab,i,[date.fromisoformat(r['day']),plan,fact,r['break_minutes'],None,None,
              r['office_hours']/24 if r['office_hours'] is not None else None,None,status],AMBER if r['status']=='missing' or r['comparison']=='difference' else None)
        tab.cell(i,1).number_format='dd.mm ddd'
        if r['closed_shifts']:
            formula(book,tab,f'E{i}',f'=SUMIF(\'Смены\'!$A$7:$A${detail_last},A{i},\'Смены\'!$E$7:$E${detail_last})',r['hours']/24)
            formula(book,tab,f'F{i}',f'=SUMIF(\'Смены\'!$A$7:$A${detail_last},A{i},\'Смены\'!$G$7:$G${detail_last})',r['earnings'])
        if r['delta_hours'] is not None:
            formula(book,tab,f'H{i}',f'=IF(OR(E{i}="",G{i}=""),"",ROUND((E{i}-G{i})*1440,2))',round(r['delta_hours']*60,2))
        for col in (5,7):tab.cell(i,col).number_format=DURATION
        tab.cell(i,6).number_format=MONEY
        tab.cell(i,8).number_format='+0.##;[Red]-0.##;"0"'
    last=6+len(data['days'])
    finish_table(tab,last,9,{4:data['summary']['break_minutes'],5:data['summary']['hours']/24,6:data['summary']['earned']})
    tab.freeze_panes='B7'
    chart=BarChart();chart.title='Отработано по дням';chart.y_axis.title='Длительность (ч:мм)'
    chart.y_axis.numFmt='[h]:mm';chart.width=19.4;chart.height=7.5
    chart.add_data(Reference(tab,min_col=5,min_row=6,max_row=last),titles_from_data=True)
    chart.set_categories(Reference(tab,min_col=1,min_row=7,max_row=last));chart.legend=None
    overview.add_chart(chart,'A22')
    for row in range(22,35):overview.row_dimensions[row].height=20
    money=make_sheet(book,'Деньги','ДЕНЬГИ / '+data['title'].upper(),
        'Начисления и полученные деньги — разные показатели. Общая сумма по всем типам не выводится намеренно.',
        ['Дата','Тип','Сумма, ₽','Период','Примечание'],[18,27,20,18,44])
    cats={'bonus':'Премия','allowance':'Доплата','reimbursement':'Возмещение','deduction':'Удержание'}
    entries=[]
    for r in data['adjustment_rows']:
        entries.append((r['day'],cats[r['category']],float(r['amount_rub'])*(-1 if r['category']=='deduction' else 1),1 if int(r['day'][-2:])<=15 else 2,r['note']))
    for r in db.execute('SELECT * FROM received_payments WHERE work_month=? ORDER BY paid_on,id',(month,)):
        entries.append((r['paid_on'],'Поступление',float(r['amount_rub']),r['period'],r['note']))
    for i,r in enumerate(sorted(entries,key=lambda e:e[0]),7):
        addrow(money,i,[date.fromisoformat(r[0]),r[1],r[2],'1–15' if r[3]==1 else '16–конец',r[4]])
        money.cell(i,1).number_format='dd.mm.yyyy';money.cell(i,3).number_format=MONEY
    if entries:finish_table(money,6+len(entries),5)
    else:addrow(money,7,['Нет дополнительных записей',None,None,None,'Это не означает, что зарплата не выплачивалась.'])
    # Summary formulas are linked to the visible daily ledger, not independent totals.
    formula(book,overview,'B7',f"=SUM('Табель'!E7:E{last})",data['summary']['hours']/24)
    formula(book,overview,'B8',f"=SUM('Табель'!F7:F{last})",data['summary']['earned'])
    formula(book,overview,'B10','=B8+B9',data['summary']['total'])
    for cell in ('B8','B9','B10','B11'):overview[cell].number_format=MONEY
    overview['B7'].number_format=DURATION
    book._sheets=[overview,tab,detail,money]
    book.active=0
    return save(book,path or f'Мои смены {month}.xlsx')