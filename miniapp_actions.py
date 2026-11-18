"""Atomic, replay-safe Mini App actions using the existing audited workflows.

The outer SQLite write lock covers preconditions, the business event and its
cached response. Telegram chat and Mini App writes therefore cannot race between
checking a shift and changing it. No credentials are stored in this ledger.
"""
import hashlib
import json
import re
from core import open_shift, toggle, iso, now, log
from workflows import active_break, start_break, stop_break


def execute_action(db, owner_id, payload):
    action = payload.get("action")
    nonce = payload.get("nonce")
    if action not in ("in", "out", "pause_start", "pause_end"):
        raise ValueError("unknown_action")
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise ValueError("invalid_nonce")
    if set(payload) - {"action", "nonce", "shift_id", "break_id"}:
        raise ValueError("unknown_fields")
    shift_id, break_id = payload.get("shift_id"), payload.get("break_id")
    if action != "in" and (type(shift_id) is not int or shift_id <= 0):
        raise ValueError("expected_shift_required")
    if action == "in" and shift_id is not None:
        raise ValueError("unexpected_shift")
    if action == "pause_end" and (type(break_id) is not int or break_id <= 0):
        raise ValueError("expected_break_required")
    if action != "pause_end" and break_id is not None:
        raise ValueError("unexpected_break")
    request = json.dumps({"action": action, "shift_id": shift_id,
                          "break_id": break_id}, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(request.encode()).hexdigest()
    db.execute("""CREATE TABLE IF NOT EXISTS miniapp_operations(
        owner_id TEXT NOT NULL, nonce TEXT NOT NULL, request_hash TEXT NOT NULL,
        result_json TEXT NOT NULL, created_utc TEXT NOT NULL,
        PRIMARY KEY(owner_id, nonce))""")
    db.execute("BEGIN IMMEDIATE")
    try:
        old = db.execute("SELECT request_hash,result_json FROM miniapp_operations "
                         "WHERE owner_id=? AND nonce=?", (str(owner_id), nonce)).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                raise ValueError("nonce_conflict")
            result = json.loads(old["result_json"])
            db.execute("COMMIT")
            return result
        current = open_shift(db)
        if action == "in":
            if current:
                raise ValueError("Смена уже начата. Обнови кабинет.")
        elif not current or current["id"] != shift_id:
            raise ValueError("Смена изменилась. Обнови кабинет.")
        if action in ("in", "out"):
            synthetic = -int(hashlib.sha256((str(owner_id)+":"+nonce).encode()).hexdigest()[:15],16)-1
            kind, shift = toggle(db, synthetic, manage_transaction=False)
            if kind != action or shift is None:
                raise ValueError("operation_conflict")
            result = {"success": True, "event": kind, "id": shift["id"]}
        elif action == "pause_start":
            result = {"success": True, "event": "break_start",
                      "id": start_break(db, manage_transaction=False)}
        else:
            running = active_break(db, shift_id)
            if not running or running["id"] != break_id:
                raise ValueError("Перерыв изменился. Обнови кабинет.")
            result = {"success": True, "event": "break_end",
                      **stop_break(db, manage_transaction=False)}
        result["server_time"] = iso(now())
        db.execute("INSERT INTO miniapp_operations VALUES(?,?,?,?,?)",
                   (str(owner_id), nonce, request_hash,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")), iso(now())))
        log(db, "miniapp_operation", result.get("id", result.get("shift_id")),
            {"action": action, "nonce": nonce})
        db.execute("COMMIT")
        return result
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
