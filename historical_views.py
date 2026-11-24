"""User-facing descriptions of imported, provenance-labelled historical evidence."""
from datetime import datetime
from core import TZ,DATA_DIR

def source_text(db):
    batches=db.execute("SELECT name,kind FROM history_source_assets ORDER BY name").fetchall()
    records=db.execute("SELECT count(*) FROM history_source_rows").fetchone()[0]
    ev=db.execute("SELECT count(*) FROM historical_evidence").fetchone()[0]
    office=db.execute("SELECT count(*) FROM office_image_days").fetchone()[0]
    actual=db.execute("SELECT count(*) FROM shifts WHERE origin='import_excel'").fetchone()[0]
    files=[b["name"] for b in batches if b["kind"]=="xlsx"]
    lines=[
       "📚 Источники исторических данных",
       f"Excel: {len(files)} файла; исходных дневных строк: {records}.",
       f"В историю смен перенесено: {actual} записей со статусом «Работал».",
       f"Скриншоты: {ev} свидетельств (не все — время прихода/ухода).",
       f"Фото офисного табеля: {office} дат; численные часы требуют уточнения.",
       "Исходники не смешиваются: каждому сохранены имя файла, лист и строка.",
       "",
       "Загруженные файлы:",
    ]
    for name in files:
       if "umnyi" in name:caption="вариант с галочками"
       elif "chistaya" in name:caption="ранняя версия"
       else:caption="более заполненная версия ноября"
       lines.append("• "+caption)
    lines.append("\nДля всех исходных строк: /archive")
    return "\n".join(lines)

def plan_text(db):
    today=datetime.now(TZ).date().isoformat()
    rows=db.execute("""
      SELECT day,status,start_time,end_time FROM (
        SELECT day,status,start_time,end_time,
        ROW_NUMBER() OVER (PARTITION BY day ORDER BY imported_utc DESC,source DESC) AS rn
        FROM history_source_rows WHERE day>=? AND status IN ('План','Выходной')
      ) WHERE rn=1 ORDER BY day LIMIT 12
    """,(today,)).fetchall()
    lines=["🗓 План из Excel (не фактическая смена):"]
    if not rows:lines.append("Будущих смен в загруженном плане пока нет.")
    for r in rows:
        if r["status"]=="Выходной":
            lines.append(f"{r['day']} — выходной по старому плану")
        else:
            lines.append(f"{r['day']} · {r['start_time']}–{r['end_time']} · план")
    lines.append("Для достоверного учёта используй кнопку прихода и ухода.")
    return "\n".join(lines)

def pending_office_text(db):
    span=db.execute("SELECT COUNT(*),MIN(day),MAX(day) FROM office_image_days").fetchone()
    count=span[0]
    if count:
        return (f"📊 Фото офисного табеля зарегистрировано: {count} дат "
                f"({span[1]} — {span[2]}).\n"
             "Часы в нём пока не переводились в числа: фотография размыта, ошибка приведёт к ложным расхождениям.\n"
             "Личные рабочие смены из Excel уже есть. Для надёжной сверки нужен исходный офисный XLSX "
             "или подтверждение отдельных чисел.")
    return "📊 Офисного табеля пока нет."

def storage_text(db):
    return ("🗄 Где хранятся данные\n"
            "Приватный сервер, отдельная служба workshift-bot.\n"
            f"База: {DATA_DIR / 'shifts.sqlite3'}\n"
            f"Импорт: {DATA_DIR / 'sources'}\n"
            f"Excel: {DATA_DIR / 'Мои смены.xlsx'}\n"
            f"Резервные копии: {DATA_DIR / 'backups'}\n"
            "Исходники из разных источников хранятся отдельно.\n"
            "Файлы не публикуются по открытым ссылкам.")

def screenshot_text(db):
    rows=db.execute("""SELECT event_date,time_text,role FROM historical_evidence
        ORDER BY event_date,time_text""").fetchall()
    lines=["📸 Отметки с присланных скриншотов",
           "Это записанные в сообщениях значения, а не подтверждённые кнопками приход/уход."]
    if not rows:return "\n".join(lines+["Записей нет."])
    for r in rows:
        marker=r["time_text"] if r["time_text"] else "фото блокнота (неразборчиво)"
        lines.append(f"{r['event_date']}: {marker}")
    lines.append("Времена сохранены отдельно и не прибавляются к зарплате.")
    return "\n".join(lines)

