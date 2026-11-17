"""Reject common private artifacts before pushing this source tree."""
from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]
patterns={
    "telegram_token":re.compile(r"\b[0-9]{7,15}:[A-Za-z0-9_-]{30,}\b"),
    "private_identity":re.compile(r"(?i)(?:mononoke|chernikov|smadie228|danil)"),
    "private_records":re.compile(r"(?:rabota_zarplata_|uchet_zarplaty_2026|history_2026)")
}
for file in root.rglob("*"):
    if not file.is_file() or ".git" in file.parts or "__pycache__" in file.parts or file.suffix==".pyc":continue
    path=file.relative_to(root)
    if path.as_posix()=="scripts/check_public.py":continue
    if file.suffix.lower() in (".sqlite3",".xls",".xlsx",".db",".png",".jpg",".pdf",".zip"):
        raise SystemExit("Forbidden binary in release: "+str(path))
    contents=file.read_text(encoding="utf-8")
    for name,regex in patterns.items():
        if regex.search(contents):
            raise SystemExit("Release scan failed: "+name+" in "+str(path))
print("Release scan passed")
