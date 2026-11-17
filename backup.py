"""Небольшая локальная резервная копия SQLite, без внешних отправок."""
import sqlite3
from pathlib import Path
from datetime import datetime,timezone
from core import DB_PATH,DATA_DIR
def main():
    if not DB_PATH.is_file():return
    dest=DATA_DIR/"backups"
    dest.mkdir(mode=0o700,exist_ok=True)
    filename=dest/(datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")+".sqlite3")
    tmp=filename.with_suffix(".partial")
    source=sqlite3.connect(DB_PATH)
    target=sqlite3.connect(tmp)
    try:
        source.backup(target)
        target.close();source.close()
        tmp.replace(filename)
    finally:
        target.close();source.close()
    for stale in sorted(dest.glob("*.sqlite3"),reverse=True)[14:]:
        stale.unlink()
    print("Backup completed",filename.name)
if __name__=="__main__":main()
