"""Private, read-only PDF summaries. No remote fonts, HTML, JavaScript or uploads."""
import os,tempfile,threading,re
from pathlib import Path
from datetime import datetime
from html import escape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,Flowable,KeepTogether
from core import DATA_DIR,TZ
from ledger import month_ledger
from analytics import year_analytics

INK=colors.HexColor('#17345A');MUTED=colors.HexColor('#536779')
GOLD=colors.HexColor('#B18A48');PALE=colors.HexColor('#EFF4F7');LINE=colors.HexColor('#D7E1E8')
_LOCK=threading.Lock()

def _fonts():
    with _LOCK:
        if 'ShiftSans' in pdfmetrics.getRegisteredFontNames():return
        candidates=[(Path(os.environ.get('WORKSHIFT_PDF_FONT','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')),Path(os.environ.get('WORKSHIFT_PDF_FONT_BOLD','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))),
                    (Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arial.ttf',Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arialbd.ttf')]
        for normal,bold in candidates:
            if normal.is_file() and bold.is_file():break
        else:raise RuntimeError('Для PDF нужен установленный шрифт с кириллицей.')
        pdfmetrics.registerFont(TTFont('ShiftSans',str(normal)))
        pdfmetrics.registerFont(TTFont('ShiftSansBold',str(bold)))
        pdfmetrics.registerFontFamily('ShiftSans',normal='ShiftSans',bold='ShiftSansBold')


def _styles():
    _fonts()
    return {
        'title':ParagraphStyle('title',fontName='ShiftSansBold',fontSize=23,leading=29,textColor=INK,spaceAfter=10),
        'h2':ParagraphStyle('h2',fontName='ShiftSansBold',fontSize=12,leading=17,textColor=INK,spaceBefore=15,spaceAfter=8,keepWithNext=True),
        'body':ParagraphStyle('body',fontName='ShiftSans',fontSize=9,leading=14,textColor=INK,spaceAfter=7),
        'small':ParagraphStyle('small',fontName='ShiftSans',fontSize=8,leading=11,textColor=MUTED),
        'cell':ParagraphStyle('cell',fontName='ShiftSans',fontSize=8,leading=11,textColor=INK),
        'numeric':ParagraphStyle('numeric',fontName='ShiftSans',fontSize=8,leading=11,textColor=INK,alignment=2),
        'metric':ParagraphStyle('metric',fontName='ShiftSansBold',fontSize=14,leading=20,textColor=INK),
        'head':ParagraphStyle('head',fontName='ShiftSansBold',fontSize=8,leading=11,textColor=colors.white),
    }


def _money(value):
    if abs(value)<0.005:value=0
    return f'{value:,.2f}'.replace(',',' ').replace('.',',')+' ₽'
def _hours(value):
    if value is None:return '—'
    mins=round(value*60)
    return f'{mins//60}:{mins%60:02d}'

def _table(rows,widths,styles,header=True,compact=False,metrics=False):
    rendered=[]
    for i,row in enumerate(rows):
        cells=[]
        for j,value in enumerate(row):
            text=escape(str(value)) if value is not None else '—'
            style=styles['head'] if i==0 and header else styles['metric'] if metrics else styles['numeric'] if j>0 and re.fullmatch(r'[+\-]?[\d\s,.:]+(?: ₽)?',str(value)) else styles['cell']
            cells.append(Paragraph(text,style))
        rendered.append(cells)
    table=Table(rendered,colWidths=widths,repeatRows=1 if header else 0,hAlign='LEFT')
    commands=[('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),3 if compact else 6),('BOTTOMPADDING',(0,0),(-1,-1),3 if compact else 6),('LINEBELOW',(0,0),(-1,-1),.3,LINE)]
    if header:commands += [('BACKGROUND',(0,0),(-1,0),INK),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,PALE])]
    table.setStyle(TableStyle(commands));return table

class Bars(Flowable):
    def __init__(self,values,labels,width=523,height=120):
        Flowable.__init__(self);self.values=values;self.labels=labels;self.width=width;self.height=height
    def draw(self):
        c=self.canv;max_value=max([1]+self.values);left=25;bottom=22;top=self.height-18
        c.setStrokeColor(LINE);c.line(left,bottom,self.width,bottom)
        c.setFont('ShiftSans',7);c.setFillColor(MUTED);c.drawString(0,top-3,f'{max_value:.0f}')
        step=(self.width-left)/max(1,len(self.values));bar=min(22,step*.62)
        for i,value in enumerate(self.values):
            x=left+step*i+(step-bar)/2;height=(top-bottom)*value/max_value
            c.setFillColor(INK);c.rect(x,bottom,bar,max(0,height),stroke=0,fill=1)
            if len(self.values)<=12 or i%5==0 or i==len(self.values)-1:
                c.setFont('ShiftSans',7);c.setFillColor(MUTED);c.drawCentredString(x+bar/2,9,str(self.labels[i]))


def _save(story,path,styles):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.private-report-',suffix='.pdf',dir=path.parent);os.close(fd)
    temp=Path(name)
    def footer(canvas,document):
        canvas.saveState();canvas.setStrokeColor(GOLD);canvas.line(36,35,A4[0]-36,35)
        canvas.setFont('ShiftSans',7);canvas.setFillColor(MUTED)
        canvas.drawString(36,22,'Мои смены · личный учёт · данные на момент формирования')
        canvas.drawRightString(A4[0]-36,22,str(document.page));canvas.restoreState()
    try:
        doc=SimpleDocTemplate(str(temp),pagesize=A4,rightMargin=36,leftMargin=36,topMargin=32,bottomMargin=49,
                              title='Мои смены',author='Мои смены',pageCompression=1)
        doc.build(story,onFirstPage=footer,onLaterPages=footer)
        os.chmod(temp,0o600);os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
    return path


def monthly_pdf(db,month,path=None):
    data=month_ledger(db,month);s=data['summary'];st=_styles();width=A4[0]-72
    story=[Paragraph('Мои смены',st['title']),Paragraph(escape(data['title']),st['h2']),
           Paragraph('Сформировано '+datetime.fromisoformat(data['generated_at']).strftime('%d.%m.%Y %H:%M')+' МСК. План, фактическая работа и поступления разделены.',st['small']),Spacer(1,16)]
    story.append(_table([['Отработано','За часы','С корректировками'],[_hours(s['hours']),_money(s['earned']),_money(s['total'])]],[width/3]*3,st,metrics=True))
    story.append(Paragraph('Две части месяца',st['h2']))
    rows=[['Период','Время, ч:мин','Начислено с корректировками','Поступило']]
    for part in data['periods']:rows.append(['1–15' if part['period']==1 else '16–конец',_hours(part['hours']),_money(part['total']),_money(part['received'])])
    story.append(_table(rows,[83,96,183,width-362],st))
    story.append(Paragraph('Расшифровка начислений',st['h2']))
    rows=[['Основание','Время / пояснение','Сумма']]
    for r in data['rates']:rows.append([_money(r['rate'])+'/ч',_hours(r['hours'])+' · '+str(r['shifts'])+' смен',_money(r['earned'])])
    extra=s['additional']
    rows.extend([['Премии и доплаты','Отдельные записи',_money(extra['bonus']+extra['allowance'])],['Возмещения','Не почасовая оплата',_money(extra['reimbursement'])],['Удержания','Без автоматических налогов',_money(-extra['deduction'])]])
    story.append(_table(rows,[164,204,width-368],st))
    story.append(Paragraph('Рабочие часы по дням',st['h2']))
    story.append(Bars([r['hours'] or 0 for r in data['days']],[r['day'][-2:] for r in data['days']],width,105))
    forecast=data['forecast'];comparison=data['reconciliation']
    story.append(Paragraph(f"План впереди: {forecast['future_shifts']} смен, {_hours(forecast['future_hours'])} ч:мин, {_money(forecast['future_pay'])}. Это оценка, не начисление. Оценочный перерыв: {forecast['assumed_break_minutes']} мин/смена; открытая смена исключена.",st['small']))
    story.append(Spacer(1,7))
    story.append(Paragraph(f"Офис: подтверждено {comparison['confirmed_days']} дат; сравнимых дней {comparison['compared_days']}. Полнота табеля не подтверждена. Отсутствующие данные не считаются нулём.",st['small']))
    story.append(Spacer(1,7))
    story.append(Paragraph('Ночная смена относится к дате прихода. Ставка берётся на дату смены; начисление округляется по каждой записи. Налоги и задолженность не вычисляются автоматически. Отчёт не заменяет ведомость работодателя.',st['small']))
    story += [PageBreak(),Paragraph('Табель · '+escape(data['title']),st['h2'])]
    rows=[['День','План','Факт · МСК','Ч:мин','За часы','Офис','Статус']]
    for r in data['days']:
        p=r['plan'];planned='Выходной' if p and p['status']=='off' else (p['start'] or '—')+'–'+(p['end'] or '—') if p else None
        factual='; '.join(x['start']+'–'+(x['end'] or '…')+(' +1' if x['next_day'] else '') for x in r['records']) or None
        status=r['status_label']
        if r['comparison']=='difference':status+=' · разница '+str(round(r['delta_hours']*60,1))+' мин'
        elif r['comparison']=='matched':status+=' · сверено'
        rows.append([r['day'][-2:]+'.'+r['day'][5:7],planned,factual,_hours(r['hours']),_money(r['earnings']) if r['earnings'] is not None else None,_hours(r['office_hours']),status])
    story.append(_table(rows,[38,65,90,50,82,55,width-380],st,compact=True))
    story += [Spacer(1,10),Paragraph('«+1» означает уход на следующий день. «—» означает отсутствие данных, не нулевое время. Исправления вносятся через подтверждение в боте, а не в этом файле.',st['small'])]
    return _save(story,path or DATA_DIR/f'Мои смены {month}.pdf',st)


def annual_pdf(db,year,path=None):
    data=year_analytics(db,year);st=_styles();width=A4[0]-72
    story=[Paragraph('Мои смены · '+str(year),st['title']),Paragraph('Годовой обзор',st['h2']),Paragraph(datetime.now(TZ).strftime('Срез на %d.%m.%Y %H:%M МСК'),st['small']),Spacer(1,15)]
    story.append(_table([['Отработано','Начислено с корректировками','Поступило'],[_hours(data['hours']),_money(data['total']),_money(data['received'])]],[width/3]*3,st,metrics=True))
    story += [Paragraph('Часы по месяцам',st['h2']),Bars([m['hours'] for m in data['months']],[m['month'][5:] for m in data['months']],width,115),Spacer(1,10)]
    rows=[['Месяц','Смены','Ч:мин','За часы','Корректировки','Поступило']]
    for m in data['months']:rows.append([m['title'].split()[0],m['shifts'],_hours(m['hours']),_money(m['earned']),_money(m['additional']['net_adjustment']),_money(m['received'])])
    story.append(_table(rows,[88,50,62,104,104,width-408],st))
    story += [Spacer(1,14),Paragraph(f"Закрытых смен: {data['shifts']}. Месяцев с работой: {data['active_months']}. Средняя длительность смены: {_hours(data['average_shift_hours'])}.",st['small']),Spacer(1,7),Paragraph('Плановые и открытые смены не включены в отработанное время. Поступления относятся к указанному рабочему месяцу, а не обязательно к месяцу банковского перевода. Ноль поступлений означает отсутствие внесённых записей; это не доказательство невыплаты.',st['small']),Spacer(1,7),Paragraph('Не заменяет ведомость работодателя. Налоги и задолженность не вычисляются автоматически. Все показатели получены из того же расчёта, что и месячные отчёты.',st['small'])]
    return _save(story,path or DATA_DIR/f'Мои смены {year} год.pdf',st)