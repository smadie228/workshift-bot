"""Opt-in app PIN/biometric security; fixtures never touch production data."""
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from security_gate import SecurityGate

OWNER = "test-telegram-init-data-hash"
OTHER = "different-telegram-launch"

class SecurityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.filename = Path(self.temp.name) / "miniapp-security.json"
        self.gate = SecurityGate(self.filename)

    def setup_security(self, pin="628315"):
        status, payload = self.gate.process({"action": "setup", "pin": pin}, OWNER, "")
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])
        return payload

    def test_absent_security_preserves_telegram_owner_only_access(self):
        status, payload = self.gate.process({"action": "status"}, OWNER, "")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"enabled": False, "unlocked": True, "biometric_enrolled": False})
        self.assertTrue(self.gate.authorized("", OWNER))
        self.assertFalse(self.filename.exists())

    def test_pin_setup_persists_salted_scrypt_and_recovery(self):
        for bad in ("123456", "000000", "12ab56", "12345", "987654"):
            with self.subTest(pin=bad):
                status, _ = self.gate.process({"action": "setup", "pin": bad}, OWNER, "")
                self.assertEqual(status, 400)
        result = self.setup_security()
        raw = self.filename.read_text("utf-8")
        self.assertNotIn("628315", raw)
        self.assertNotIn(result["recovery_code"], raw)
        self.assertEqual(len(result["recovery_code"]), 32)
        if os.name == "posix":
            self.assertEqual(self.filename.stat().st_mode & stat.S_IRWXO, 0)
        # Windows maps permissions to POSIX bits imperfectly. Deployment on
        # Linux separately verifies the file is owned by the service user (0600).
        self.assertEqual(SecurityGate(self.filename).process({"action": "status"}, OWNER, "")[1]["enabled"], True)
        self.assertEqual(self.gate.process({"action": "setup", "pin": "973421"}, OWNER, "")[0], 409)

    def test_authenticated_session_is_bound_to_telegram_launch_and_can_lock(self):
        initial = self.setup_security()
        token = initial["session"]
        self.assertTrue(self.gate.authorized(token, OWNER))
        self.assertFalse(self.gate.authorized(token, OTHER))
        self.assertFalse(self.gate.authorized("random invalid", OWNER))
        self.assertEqual(self.gate.process({"action": "lock"}, OWNER, token), (200, {"locked": True}))
        self.assertFalse(self.gate.authorized(token, OWNER))
        code, result = self.gate.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")
        self.assertEqual(code, 200)
        self.assertTrue(self.gate.authorized(result["session"], OWNER))
        other_session = result["session"]
        self.gate._sessions[next(iter(self.gate._sessions))] = (OWNER, time.monotonic()-1)
        self.assertFalse(self.gate.authorized(other_session, OWNER))

    def test_pin_failures_cooldown_and_no_plaintext_error(self):
        self.setup_security()
        for i in range(4):
            code, result = self.gate.process({"action": "unlock", "method": "pin", "pin": "000001"}, OWNER, "")
            self.assertEqual((code, result["error"]), (403, "invalid_credential"))
        code, result = self.gate.process({"action": "unlock", "method": "pin", "pin": "000001"}, OWNER, "")
        self.assertEqual(code, 429)
        self.assertGreater(result["retry_after"], 0)
        self.assertEqual(self.gate.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")[0], 429)
        self.gate._blocked_until = 0
        code, result = self.gate.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")
        self.assertEqual(code, 200)
        self.assertTrue(result["session"])

    def test_failed_pin_cooldown_survives_server_restart_and_blocks_pin_change(self):
        initial = self.setup_security()
        for i in range(5):
            self.gate.process({"action": "unlock", "method": "pin", "pin": "000001"}, OWNER, "")
        reloaded = SecurityGate(self.filename)
        self.assertEqual(reloaded.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")[0], 429)
        self.assertEqual(reloaded.process({"action": "change_pin", "old_pin": "628315",
                                           "new_pin": "712849"}, OWNER, initial["session"])[0], 423)
        # A freshly issued session is required after a process restart.
        reloaded._blocked_until = 0
        code, data = reloaded.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")
        self.assertEqual(code, 200)
        self.assertEqual(reloaded.process({"action": "change_pin", "old_pin": "000001",
                                           "new_pin": "712849"}, OWNER, data["session"])[0], 403)
        self.assertEqual(reloaded.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")[0], 200)

    def test_biometric_token_only_hashed_and_device_bound(self):
        initial = self.setup_security()
        token = initial["session"]
        device = "Samsung-SM_G780G"
        secret = "ab" * 32
        no_auth, _ = self.gate.process({"action": "biometric_add", "device_id": device, "token": secret}, OWNER, "")
        self.assertEqual(no_auth, 423)
        code, result = self.gate.process({"action": "biometric_add", "device_id": device, "token": secret}, OWNER, token)
        self.assertEqual(code, 200)
        self.assertTrue(result["biometric_enrolled"])
        self.assertNotIn(secret, self.filename.read_text("utf-8"))
        self.assertEqual(self.gate.process({"action": "unlock", "method": "biometric", "device_id": device, "token": secret}, OWNER, "")[0], 200)
        self.assertEqual(self.gate.process({"action": "unlock", "method": "biometric", "device_id": "OtherPhone", "token": secret}, OWNER, "")[0], 403)
        self.assertEqual(self.gate.process({"action": "biometric_remove", "device_id": device}, OWNER, token)[0], 200)
        self.assertEqual(self.gate.process({"action": "unlock", "method": "biometric", "device_id": device, "token": secret}, OWNER, "")[0], 403)

    def test_recovery_rotates_once_and_changes_pin(self):
        initial = self.setup_security()
        code, result = self.gate.process({"action": "unlock", "method": "recovery", "recovery": initial["recovery_code"]}, OWNER, "")
        self.assertEqual(code, 200)
        self.assertNotEqual(result["recovery_code"], initial["recovery_code"])
        self.assertEqual(self.gate.process({"action": "unlock", "method": "recovery", "recovery": initial["recovery_code"]}, OWNER, "")[0], 403)
        code, result = self.gate.process({"action": "change_pin", "old_pin": "628315", "new_pin": "712849"}, OWNER, result["session"])
        self.assertEqual(code, 200)
        self.assertEqual(self.gate.process({"action": "unlock", "method": "pin", "pin": "628315"}, OWNER, "")[0], 403)
        self.assertEqual(self.gate.process({"action": "unlock", "method": "pin", "pin": "712849"}, OWNER, "")[0], 200)

    def test_disable_requires_valid_pin_and_revokes_all_sessions(self):
        initial = self.setup_security()
        self.assertEqual(self.gate.process({"action": "disable", "pin": "000001"}, OWNER, initial["session"])[0], 403)
        code, result = self.gate.process({"action": "disable", "pin": "628315"}, OWNER, initial["session"])
        self.assertEqual((code, result), (200, {"enabled": False}))
        self.assertFalse(self.filename.exists())
        self.assertTrue(self.gate.authorized("", OWNER))

    def test_invalid_configuration_fails_closed(self):
        self.filename.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            SecurityGate(self.filename)

if __name__ == "__main__":
    unittest.main()
