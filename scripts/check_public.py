"""Prevent accidental release of credentials, private exports and local identifiers."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN_EXTENSIONS={".sqlite3",".sqlite",".db",".xlsx",".xls",".xlsm",
                      ".png",".jpg",".jpeg",".pdf",".zip",".pem",".key"}
PATTERNS={
    "bot_access_token":re.compile(r"\b[0-9]{7,15}:[A-Za-z0-9_-]{30,}\b"),
    "service_credential":re.compile(r"(?i)\b(?:github_pat_|ghp_|gho_|sk-proj-)[A-Za-z0-9_-]{12,}"),
    "local_profile_path":re.compile(r"(?i)(?:[A-Z]:[\\/](?:Users|Documents)[\\/][^\\/\s]+|/(?:home|Users)/[^/\s]+/)"),
    "ipv4_address":re.compile(r"\b(?!127\.0\.0\.1\b)(?:\d{1,3}\.){3}\d{1,3}\b"),
}
SAFE_SCREENSHOTS={
    "assets/screenshots/mobile-home-dark.png",
    "assets/screenshots/mobile-calendar-dark.png",
    "assets/screenshots/mobile-finance-dark.png",
    "assets/screenshots/mobile-settings-dark.png",
    "assets/screenshots/mobile-pin-lock.png",
    "assets/screenshots/mobile-home-light.png",
    "assets/screenshots/desktop-overview-dark.png",
    "assets/screenshots/desktop-finance-light.png",
}
for file in ROOT.rglob("*"):
    if not file.is_file() or any(part in {".git","__pycache__","node_modules"} for part in file.parts):
        continue
    relative=file.relative_to(ROOT)
    if relative.as_posix() in SAFE_SCREENSHOTS:
        if not file.read_bytes().startswith(bytes((137,80,78,71,13,10,26,10))) or file.stat().st_size > 500000:
            raise SystemExit(f"Unexpected screenshot format or size: {relative}")
        continue
    if file.suffix.lower() in FORBIDDEN_EXTENSIONS:
        raise SystemExit(f"Unexpected binary export: {relative}")
    if relative.as_posix()=="scripts/check_public.py":
        continue
    try:
        contents=file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SystemExit(f"Unrecognized non-text content: {relative}")
    for label,regex in PATTERNS.items():
        if regex.search(contents):
            raise SystemExit(f"Privacy check failed ({label}): {relative}")
print("Release privacy check passed")