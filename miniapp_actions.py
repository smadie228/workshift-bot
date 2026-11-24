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
import actual_shift
import web_plan
import actual_day
import web_workspace
import web_breaks
import attendance_recovery
import office_photo
import attendance_recovery
import office_photo


def execute_action(db, owner_id, payload):
    if not isinstance(payload, dict):
        raise ValueError("bad_body")
    action, nonce = payload.get("action"), payload.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise ValueError("invalid_nonce")
    if not isinstance(action, str):
        raise ValueError("unknown_action")
    correction = action in actual_shift.ACTIONS
    planning = action in web_plan.ACTIONS
    actual_off = action in actual_day.ACTIONS
    breaks_edit = action in web_breaks.ACTIONS
    workspace = action in web_workspace.ACTIONS
    shift_id, break_id = payload.get("shift_id"), payload.get("break_id")
    recovery = action in attendance_recovery.ACTIONS
    photo = action in office_photo.ACTIONS
    if recovery or photo:
        extra_request = (attendance_recovery if recovery else office_photo).normalize(payload)
        request = json.dumps(extra_request, sort_keys=True, separators=(",", ":"))
    elif breaks_edit:
        break_request = web_breaks.normalize(payload)
        request = json.dumps(break_request, sort_keys=True, separators=(",", ":"))
    elif workspace:
        workspace_request = web_workspace.normalize(payload)
        request = json.dumps(workspace_request, sort_keys=True, separators=(",", ":"))
    elif actual_off:
        off_request = actual_day.normalize(payload)
        request = json.dumps(off_request, sort_keys=True, separators=(",", ":"))
    elif planning:
        plan_request = web_plan.normalize(payload)
        request = json.dumps(plan_request, sort_keys=True, separators=(",", ":"))
    elif correction:
        edit = actual_shift.normalize(payload)
        request = json.dumps(edit, sort_keys=True, separators=(",", ":"))
    else:
        if action not in ("in", "out", "pause_start", "pause_end"):
            raise ValueError("unknown_action")
        if set(payload) - {"action", "nonce", "shift_id", "break_id", "state_revision"}:
            raise ValueError("unknown_fields")
        if action != "in" and (type(shift_id) is not int or not 0 < shift_id <= 2**53-1):
            raise ValueError("expected_shift_required")
        if action == "in" and shift_id is not None:
            raise ValueError("unexpected_shift")
        if action == "pause_end" and (type(break_id) is not int or not 0 < break_id <= 2**53-1):
            raise ValueError("expected_break_required")
        if action != "pause_end" and break_id is not None:
            raise ValueError("unexpected_break")
        canonical = {"action": action, "shift_id": shift_id, "break_id": break_id}
        if "state_revision" in payload:
            expected = payload["state_revision"]
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError("invalid_state_revision")
            canonical["state_revision"] = expected
        # Preserve hashes of already-issued legacy operations.
        request = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
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
        if recovery or photo:
            result = (attendance_recovery if recovery else office_photo).apply(db, extra_request)
        elif breaks_edit:
            result = web_breaks.apply(db, break_request)
        elif workspace:
            result = web_workspace.apply(db, workspace_request, nonce)
        elif actual_off:
            result = actual_day.apply(db, off_request)
        elif planning:
            result = web_plan.apply(db, plan_request)
        elif correction:
            result = actual_shift.apply(db, edit)
        else:
            if "state_revision" in payload and payload["state_revision"] != actual_shift.attendance_revision(db):
                raise ValueError("Состояние смены изменилось. Обнови кабинет и повтори действие.")
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
