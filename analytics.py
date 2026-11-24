"""Annual read-only analytics, using the same monthly model as all reports."""
from ledger import month_ledger

def year_analytics(db,year,reference=None):
    if type(year) is not int or not 2000<=year<=2100:raise ValueError('Год: 2000–2100.')
    months=[]
    for m in range(1,13):
        data=month_ledger(db,f'{year:04d}-{m:02d}',reference)
        months.append({'month':data['month'],'title':data['title'],**data['summary'],
                       'forecast':data['forecast'],'reconciliation':data['reconciliation']})
    hours=sum(m['hours'] for m in months);count=sum(m['shifts'] for m in months)
    active=[m for m in months if m['shifts']]
    return {'year':year,'months':months,'hours':hours,'shifts':count,
            'earned':round(sum(m['earned'] for m in months),2),
            'adjustments':round(sum(m['additional']['net_adjustment'] for m in months),2),
            'total':round(sum(m['total'] for m in months),2),
            'received':round(sum(m['received'] for m in months),2),
            'average_shift_hours':hours/count if count else None,
            'active_months':len(active),'average_active_month_hours':hours/len(active) if active else None,
            'most_hours_month':max(active,key=lambda m:m['hours'])['month'] if active else None}