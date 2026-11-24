"""Authenticated report download. No public file URL, no token in a URL.
Reuses the unchanged report builders in an isolated temporary directory.
"""
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from ledger import parse_month


def build(db, payload):
    if not isinstance(payload, dict) or set(payload) != {'format', 'month'}:
        raise ValueError('unknown_fields')
    month = payload['month']; parse_month(month)
    kind = payload['format']
    if not isinstance(kind, str) or kind not in ('xlsx', 'pdf', 'annual_pdf'):
        raise ValueError('Выбери Excel или PDF.')
    extension = 'xlsx' if kind == 'xlsx' else 'pdf'
    name = 'workshifts-'+(month[:4] if kind == 'annual_pdf' else month)+'.'+extension
    with TemporaryDirectory(prefix='workshift-download-') as folder:
        target = Path(folder)/name
        if kind == 'xlsx':
            from report_month import monthly_report
            monthly_report(db, month, path=target)
        elif kind == 'pdf':
            from report_pdf import monthly_pdf
            monthly_pdf(db, month, path=target)
        else:
            from report_pdf import annual_pdf
            annual_pdf(db, int(month[:4]), path=target)
        if target.stat().st_size > 8*1024*1024:
            raise ValueError('Отчёт слишком большой. Выбери меньший период.')
        raw = target.read_bytes()
    return {'name': name, 'mime': 'application/pdf' if extension == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'base64': base64.b64encode(raw).decode('ascii'), 'bytes': len(raw)}
