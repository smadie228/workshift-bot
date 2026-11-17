"""Secure owner-uploaded office workbooks; preview only, no unverified import."""
import hashlib
import os
import re
import uuid
from datetime import datetime, date
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook
from core import DATA_DIR, TZ, log

MAX_BYTES=8*1024*1024
MAX_UNPACKED=90*1024*1024

def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS office_uploaded_files (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       filename TEXT NOT NULL,
       stored_filename TEXT NOT NULL UNIQUE,
       sha256 TEXT NOT NULL UNIQUE,
       bytes INTEGER NOT NULL,
       sheet_names TEXT NOT NULL,
       detected_date_cells INTEGER NOT NULL,
       received_at TEXT NOT NULL
    )""")

def inspect(path):
    try:
        with ZipFile(path,"r") as z:
            entries=z.infolist()
            if len(entries)>700 or sum(x.file_size for x in entries)>MAX_UNPACKED:
                raise ValueError("Книга слишком большая или содержит слишком много частей")
            if not any(x.filename=="xl/workbook.xml" for x in entries):
                raise ValueError("Не найден Excel-файл")
            if any("vbaProject.bin" in x.filename for x in entries):
                raise ValueError("Книги с макросами не принимаются")
            if any(x.filename.startswith("xl/externalLinks/") for x in entries):
                raise ValueError("Книга содержит внешние ссылки; экспортируй версию без ссылок")
    except BadZipFile as exc:raise ValueError("Файл не является корректным .xlsx") from exc
    workbook=load_workbook(path,read_only=True,data_only=True,keep_links=False)
    count=0
    names=[]
    try:
        for ws in workbook.worksheets[:12]:
            names.append(ws.title[:60])
            for row in ws.iter_rows(min_row=1,max_row=min(ws.max_row,45),
                                    min_col=1,max_col=min(ws.max_column,65),values_only=True):
                for x in row:
                    if isinstance(x,(datetime,date)) and x.year in (2025,2026,2027,2028):
                        count+=1
                    elif isinstance(x,str) and re.fullmatch(r"\d{2}[./-]\d{2}[./-]\d{2,4}",x.strip()):
                        count+=1
    finally:workbook.close()
    return names,count

async def accept(update,context,db):
    ensure(db)
    doc=update.message.document
    if not doc:
        return
    if not context.user_data.get("await_office_document"):
        await update.message.reply_text(
           "📄 Файл получен, но импорт не запрошен. Открой «🏢 Сверка» → «Загрузить табель» для безопасной проверки.")
        return
    context.user_data.pop("await_office_document",None)
    original=doc.file_name or "office.xlsx"
    if not original.lower().endswith(".xlsx"):
        return await update.message.reply_text("Принимаю только .xlsx без макросов. Экспортируй официальный табель из офисной системы.")
    if not doc.file_size or doc.file_size>MAX_BYTES:
        return await update.message.reply_text("Размер Excel должен быть не больше 8 МБ.")
    folder=DATA_DIR/"office-documents"
    folder.mkdir(parents=True,mode=0o700,exist_ok=True)
    path=folder/(uuid.uuid4().hex+".xlsx")
    try:
        file=await context.bot.get_file(doc.file_id)
        await file.download_to_drive(custom_path=str(path))
        os.chmod(path,0o600)
        if path.stat().st_size>MAX_BYTES:raise ValueError("Файл превышает ограничение")
        names,date_cells=inspect(path)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        existing=db.execute("SELECT id FROM office_uploaded_files WHERE sha256=?",(digest,)).fetchone()
        if existing:
            path.unlink(missing_ok=True)
            return await update.message.reply_text("Этот офисный файл уже сохранён. Повторный импорт не требуется.")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("""INSERT INTO office_uploaded_files
               (filename,stored_filename,sha256,bytes,sheet_names,detected_date_cells,received_at)
               VALUES(?,?,?,?,?,?,?)""",
              (original[:200],path.name,digest,path.stat().st_size,repr(names),date_cells,
               datetime.now(TZ).isoformat()))
            log(db,"office_file_received",details={"sha256_prefix":digest[:12],"size":path.stat().st_size,"sheet_count":len(names)})
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        return await update.message.reply_text("⚠️ Не смог сохранить документ: "+str(exc)[:200])
    await update.message.reply_text(
        "✅ <b>Офисный XLSX безопасно сохранён</b>\n\n"
        f"Листов: <b>{len(names)}</b>\n"
        f"Ячеек, похожих на даты: <b>{date_cells}</b>\n"
        "Офисные часы пока не менялись. Для точного импорта требуется проверка структуры листов и подтверждение данных.\n\n"
        "Проверяемый исходник сохранён отдельно от личных смен.",
        parse_mode="HTML")