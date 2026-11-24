"""Opt-in second lock for the owner-only Telegram Mini App.

Security file is kept outside the web root and separate from shift/payroll SQLite.
Only salted scrypt PIN/recovery verifiers and hashes of random biometric tokens
are persisted. Server-issued unlock sessions are in memory, short-lived and
bound to the Telegram initData hash by the caller.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

PIN_N = 2 ** 15
SESSION_TTL = 15 * 60
PIN_PATTERN = re.compile(r"[0-9]{6}\Z")
DEVICE_PATTERN = re.compile(r"[A-Za-z0-9_-]{4,128}\Z")
TOKEN_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
RECOVERY_PATTERN = re.compile(r"[a-f0-9]{32}\Z")

def _secret_hash(value: str, salt: str) -> str:
    return hashlib.scrypt(value.encode("ascii"), salt=bytes.fromhex(salt),
                          n=PIN_N, r=8, p=1, dklen=32, maxmem=128*1024*1024).hex()

def _pin_valid(pin) -> bool:
    if not isinstance(pin, str) or not PIN_PATTERN.fullmatch(pin):
        return False
    # Avoid widely guessed 6-digit PINs while keeping leading zeros valid.
    if len(set(pin)) == 1 or pin in {"123456", "654321", "012345", "987654", "121212", "112233"}:
        return False
    return True

class SecurityGate:
    def __init__(self, filename):
        self.file = Path(filename)
        self._lock = threading.RLock()
        self._sessions = {}
        self._state = self._read()
        self._failed_count = int(self._state.get("failed_count", 0)) if self._state else 0
        self._blocked_until = float(self._state.get("blocked_until", 0)) if self._state else 0.0

    def _read(self):
        if not self.file.exists():
            return None
        if self.file.is_symlink():
            raise RuntimeError("Refusing a symlinked Mini App security file")
        state = json.loads(self.file.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("version") != 1 or
                not all(isinstance(state.get(k), str) and len(state[k]) == 64
                        for k in ("pin_hash", "recovery_hash")) or
                not all(isinstance(state.get(k), str) and len(state[k]) == 32
                        for k in ("pin_salt", "recovery_salt")) or
                not isinstance(state.get("biometrics"), dict)):
            raise RuntimeError("Invalid Mini App security configuration")
        return state

    @property
    def enabled(self):
        return self._state is not None

    def _persist(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if self.file.is_symlink():
            raise RuntimeError("Refusing a symlinked Mini App security file")
        fd, name = tempfile.mkstemp(prefix=".miniapp-security-", dir=self.file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _session(self, identity):
        raw = secrets.token_urlsafe(32)
        key = hashlib.sha256(raw.encode("ascii")).digest()
        self._sessions[key] = (identity, time.monotonic() + SESSION_TTL)
        return raw

    def authorized(self, raw, identity):
        if not self.enabled:
            return True
        if not isinstance(raw, str) or len(raw) > 128:
            return False
        if not raw:
            return False
        key = hashlib.sha256(raw.encode("utf-8", "replace")).digest()
        record = self._sessions.get(key)
        if record is None:
            return False
        if time.monotonic() > record[1]:
            self._sessions.pop(key, None)
            return False
        return hmac.compare_digest(record[0], identity)

    def _failed(self):
        self._failed_count += 1
        if self._failed_count >= 5:
            # Persisted cooldown prevents reset by restarting the Mini App service.
            delays = (30, 60, 120, 300, 600, 1800, 3600)
            self._blocked_until = time.time() + delays[min(self._failed_count - 5, len(delays) - 1)]
        self._state["failed_count"] = self._failed_count
        self._state["blocked_until"] = self._blocked_until
        self._persist()
        if self._blocked_until > time.time():
            return 429, {"error": "temporarily_locked", "retry_after": max(1, int(self._blocked_until - time.time()))}
        return 403, {"error": "invalid_credential"}

    def _reset_failures(self):
        self._failed_count = 0
        self._blocked_until = 0.0
        self._state["failed_count"] = 0
        self._state["blocked_until"] = 0.0
        self._persist()

    def _cooldown(self):
        if self._blocked_until > time.time():
            return 429, {"error": "temporarily_locked", "retry_after": max(1, int(self._blocked_until - time.time()))}
        return None

    def _verify_pin(self, pin):
        if not isinstance(pin, str) or not PIN_PATTERN.fullmatch(pin):
            return False
        return hmac.compare_digest(_secret_hash(pin, self._state["pin_salt"]), self._state["pin_hash"])

    def _verify_recovery(self, recovery):
        if not isinstance(recovery, str) or not RECOVERY_PATTERN.fullmatch(recovery):
            return False
        return hmac.compare_digest(_secret_hash(recovery, self._state["recovery_salt"]), self._state["recovery_hash"])

    def _check_method(self, data):
        method = data.get("method")
        if method == "pin":
            return self._verify_pin(data.get("pin"))
        if method == "recovery":
            return self._verify_recovery(data.get("recovery"))
        if method == "biometric":
            device = data.get("device_id")
            secret = data.get("token")
            if (not isinstance(device, str) or not DEVICE_PATTERN.fullmatch(device)
                    or not isinstance(secret, str) or not TOKEN_PATTERN.fullmatch(secret)):
                return False
            expected = self._state["biometrics"].get(device, "")
            return bool(expected) and hmac.compare_digest(
                hashlib.sha256(bytes.fromhex(secret)).hexdigest(), expected)
        return False

    def process(self, data, identity, unlocked):
        if not isinstance(data, dict):
            return 400, {"error": "bad_request"}
        action = data.get("action")
        with self._lock:
            if action == "status" and set(data) == {"action"}:
                return 200, {"enabled": self.enabled, "unlocked": self.authorized(unlocked, identity),
                             "biometric_enrolled": bool(self._state and self._state["biometrics"])}
            if action == "setup" and set(data) == {"action", "pin"}:
                if self.enabled:
                    return 409, {"error": "already_enabled"}
                pin = data.get("pin")
                if not _pin_valid(pin):
                    return 400, {"error": "weak_pin"}
                recovery = secrets.token_hex(16)
                self._state = {
                    "version": 1,
                    "pin_salt": secrets.token_hex(16),
                    "recovery_salt": secrets.token_hex(16),
                    "pin_hash": "",
                    "recovery_hash": "",
                    "biometrics": {},
                    "failed_count": 0,
                    "blocked_until": 0.0,
                }
                self._state["pin_hash"] = _secret_hash(pin, self._state["pin_salt"])
                self._state["recovery_hash"] = _secret_hash(recovery, self._state["recovery_salt"])
                self._persist()
                self._failed_count = 0
                self._blocked_until = 0
                return 200, {"enabled": True, "session": self._session(identity),
                             "recovery_code": recovery}
            if not self.enabled:
                return 409, {"error": "not_enabled"}
            if action == "unlock" and set(data) in (
                {"action", "method", "pin"}, {"action", "method", "recovery"},
                {"action", "method", "device_id", "token"}):
                cooldown=self._cooldown()
                if cooldown:return cooldown
                if not self._check_method(data):
                    return self._failed()
                self._reset_failures()
                if data["method"] == "recovery":
                    # Recovery code is single-use; rotate it immediately after use.
                    recovery = secrets.token_hex(16)
                    self._state["recovery_salt"] = secrets.token_hex(16)
                    self._state["recovery_hash"] = _secret_hash(recovery, self._state["recovery_salt"])
                    self._persist()
                    return 200, {"session": self._session(identity), "recovery_code": recovery}
                return 200, {"session": self._session(identity)}
            if action == "lock" and set(data) == {"action"}:
                if unlocked:
                    key = hashlib.sha256(unlocked.encode("utf-8", "replace")).digest()
                    self._sessions.pop(key, None)
                return 200, {"locked": True}
            if not self.authorized(unlocked, identity):
                return 423, {"error": "security_locked"}
            if action == "biometric_add" and set(data) == {"action", "device_id", "token"}:
                device, secret = data.get("device_id"), data.get("token")
                if (not isinstance(device, str) or not DEVICE_PATTERN.fullmatch(device) or
                        not isinstance(secret, str) or not TOKEN_PATTERN.fullmatch(secret)):
                    return 400, {"error": "bad_biometric"}
                if len(self._state["biometrics"]) >= 5 and device not in self._state["biometrics"]:
                    return 409, {"error": "too_many_devices"}
                self._state["biometrics"][device] = hashlib.sha256(bytes.fromhex(secret)).hexdigest()
                self._persist()
                return 200, {"biometric_enrolled": True}
            if action == "biometric_remove" and set(data) == {"action", "device_id"}:
                device = data.get("device_id")
                if not isinstance(device, str) or not DEVICE_PATTERN.fullmatch(device):
                    return 400, {"error": "bad_biometric"}
                self._state["biometrics"].pop(device, None)
                self._persist()
                return 200, {"biometric_enrolled": bool(self._state["biometrics"])}
            if action == "change_pin" and set(data) in (
                {"action", "old_pin", "new_pin"}, {"action", "recovery", "new_pin"}):
                cooldown=self._cooldown()
                if cooldown:return cooldown
                pin = data.get("new_pin")
                if not _pin_valid(pin):
                    return 400, {"error": "weak_pin"}
                valid = self._verify_pin(data["old_pin"]) if "old_pin" in data else self._verify_recovery(data["recovery"])
                if not valid:
                    return self._failed()
                recovery = secrets.token_hex(16)
                self._state["pin_salt"] = secrets.token_hex(16)
                self._state["pin_hash"] = _secret_hash(pin, self._state["pin_salt"])
                self._state["recovery_salt"] = secrets.token_hex(16)
                self._state["recovery_hash"] = _secret_hash(recovery, self._state["recovery_salt"])
                self._state["biometrics"] = {}
                self._state["failed_count"] = 0
                self._state["blocked_until"] = 0.0
                self._failed_count = 0
                self._blocked_until = 0.0
                self._sessions.clear()
                self._persist()
                return 200, {"session": self._session(identity), "recovery_code": recovery,
                             "biometric_enrolled": False}
            if action == "disable" and set(data) in (
                {"action", "pin"}, {"action", "recovery"}):
                cooldown=self._cooldown()
                if cooldown:return cooldown
                valid = self._verify_pin(data["pin"]) if "pin" in data else self._verify_recovery(data["recovery"])
                if not valid:
                    return self._failed()
                if self.file.is_symlink():
                    raise RuntimeError("Refusing a symlinked security file")
                self.file.unlink()
                self._state = None
                self._sessions.clear()
                self._failed_count = 0
                self._blocked_until = 0
                return 200, {"enabled": False}
            return 400, {"error": "unknown_security_action"}
