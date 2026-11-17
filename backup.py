"""Transactional SQLite backup with integrity checks and conservative rotation.

No network calls and no dependence on Telegram. Can be invoked by systemd timer.
"""
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from core import DB_PATH, DATA_DIR

KEEP=14

def create_backup(source=DB_PATH,destination=None):
    source=Path(source)
    if not source.is_file():
        return None
    directory=Path(destination) if destination is not None else DATA_DIR/"backups"
    directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    os.chmod(directory,0o700)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    final=directory/(stamp+".sqlite3")
    temporary=directory/(stamp+".partial")
    if temporary.exists():
        raise RuntimeError("A backup is already being created")
    try:
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro",uri=True,timeout=30) as db:
            with sqlite3.connect(temporary,timeout=30) as target:
                db.backup(target)
                integrity=target.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity!="ok":
                    raise RuntimeError("Backup integrity check failed")
        os.chmod(temporary,0o600)
        temporary.replace(final)
        # Only rotate after validating the new backup; preserve at least one.
        old=sorted(directory.glob("*.sqlite3"),key=lambda p:p.stat().st_mtime,reverse=True)
        for item in old[KEEP:]:
            item.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return final

def verify_backup(filename):
    path=Path(filename)
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True) as db:
        status=db.execute("PRAGMA integrity_check").fetchone()[0]
        if status!="ok":
            raise RuntimeError("Backup failed integrity_check")
        return db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]

def main():
    result=create_backup()
    if result:
        print("BACKUP_OK",result.name,"SHIFTS",verify_backup(result))
    else:
        print("BACKUP_SKIPPED: no database")

if __name__=="__main__":
    main()
