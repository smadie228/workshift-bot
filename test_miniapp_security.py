"""End-to-end local HTTP tests: PIN and biometrics gate real API routes.

Uses a temporary shift database, fake Telegram HMAC and isolated security file.
"""
import json
import os
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

import core
import miniapp_server
from test_miniapp import signed

class HttpSecurityGateTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MINIAPP_PUBLIC_ORIGIN": "https://fixture.example", "MINIAPP_ENABLE_WRITES": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dbfile = Path(self.folder.name) / "private.sqlite3"
        db = core.connect(self.dbfile)
        core.put_config(db, "owner_id", "77")
        db.close()
        self.initdata = signed()
        self.server = miniapp_server.MiniServer(("127.0.0.1", 0), miniapp_server.MiniAppHandler,
                                                "fictional-token", "77", db_path=self.dbfile)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self, path, data, session="", auth=None, origin="https://fixture.example"):
        # Production reverse proxy already permits the authenticated workspace route;
        # second-factor operations are dispatched by its reserved "security" view.
        if path == "/mini/api/security":
            path = "/mini/api/workspace"
            data = {"view": "security", **data}
        conn = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {
            "Content-Type": "application/json", "Origin": origin,
            "X-Telegram-Init-Data": self.initdata if auth is None else auth,
        }
        if session:
            headers["X-Workshift-Unlock"] = session
        conn.request("POST", path, json.dumps(data).encode(), headers)
        response = conn.getresponse()
        status, raw, info = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return status, json.loads(raw), info

    def test_full_pin_lifecycle_protects_read_and_workspace_and_action(self):
        base, initial, _ = self.post("/mini/api/state", {"month": "2026-11"})
        self.assertEqual(base, 200)
        code, response, headers = self.post("/mini/api/security", {"action": "status"})
        self.assertEqual(code, 200)
        self.assertEqual(response["enabled"], False)
        self.assertEqual(headers["Cache-Control"], "no-store")
        code, response, _ = self.post("/mini/api/security", {"action": "setup", "pin": "628315"})
        self.assertEqual(code, 200)
        self.assertEqual(len(response["recovery_code"]), 32)
        session = response["session"]
        secure_file = self.dbfile.parent / "miniapp-security.json"
        self.assertTrue(secure_file.is_file())
        self.assertNotIn("628315", secure_file.read_text())
        self.assertNotIn(response["recovery_code"], secure_file.read_text())
        self.assertEqual(self.post("/mini/api/state", {"month": "2026-11"})[0], 423)
        self.assertEqual(self.post("/mini/api/workspace", {"month": "2026-11"})[0], 423)
        self.assertEqual(self.post("/mini/api/action", {"action": "in", "nonce": "a"*32})[0], 423)
        self.assertEqual(self.post("/mini/api/state", {"month": "2026-11"}, auth="bad-token", session=session)[0], 403)
        code, data, _ = self.post("/mini/api/state", {"month": "2026-11"}, session=session)
        self.assertEqual(code, 200)
        self.assertEqual(data["summary"], initial["summary"])
        self.assertEqual(self.post("/mini/api/action", {"action": "in", "nonce": "a"*32},session=session)[0], 403)
        self.assertEqual(self.post("/mini/api/security", {"action": "lock"},session=session)[0], 200)
        self.assertEqual(self.post("/mini/api/state", {"month": "2026-11"},session=session)[0], 423)
        self.assertEqual(self.post("/mini/api/security", {"action": "unlock", "method": "pin", "pin": "000000"})[0], 403)
        code, answer, _ = self.post("/mini/api/security", {"action": "unlock", "method": "pin", "pin": "628315"})
        self.assertEqual(code, 200)
        session=answer["session"]
        self.assertEqual(self.post("/mini/api/state", {"month": "2026-11"},session=session)[0], 200)
        code, answer, _ = self.post("/mini/api/security", {"action": "disable", "pin": "628315"}, session=session)
        self.assertEqual(code, 200)
        self.assertFalse(answer["enabled"])
        self.assertFalse(secure_file.exists())
        self.assertEqual(self.post("/mini/api/state", {"month": "2026-11"})[0], 200)

    def test_biometric_token_is_tied_to_device_and_recovery_is_single_use(self):
        _, setup, _ = self.post("/mini/api/security", {"action": "setup", "pin": "628315"})
        session = setup["session"]
        device = "a"*64
        token = "bb"*32
        self.assertEqual(self.post("/mini/api/security",
            {"action": "biometric_add", "device_id": device, "token": token})[0], 423)
        self.assertEqual(self.post("/mini/api/security",
            {"action": "biometric_add", "device_id": device, "token": token},session=session)[0], 200)
        code, answer, _ = self.post("/mini/api/security",
            {"action": "unlock", "method": "biometric", "device_id": device, "token": token})
        self.assertEqual(code, 200)
        self.assertTrue(answer["session"])
        self.assertEqual(self.post("/mini/api/security",
            {"action": "unlock", "method": "biometric", "device_id": "c"*64, "token": token})[0], 403)
        self.assertEqual(self.post("/mini/api/security",
            {"action": "unlock", "method": "recovery", "recovery": setup["recovery_code"]})[0], 200)
        self.assertEqual(self.post("/mini/api/security",
            {"action": "unlock", "method": "recovery", "recovery": setup["recovery_code"]})[0], 403)
        self.assertEqual(self.post("/mini/api/security",
            {"action": "disable", "pin": "628315"},session=session)[0], 200)

    def test_pin_security_does_not_modify_shifts_database(self):
        db = core.connect(self.dbfile)
        before = "\n".join(db.iterdump())
        db.close()
        _, setup, _ = self.post("/mini/api/security", {"action": "setup", "pin": "628315"})
        self.post("/mini/api/security", {"action": "unlock", "method": "pin", "pin": "628315"})
        self.post("/mini/api/security", {"action": "disable", "pin": "628315"},session=setup["session"])
        db=core.connect(self.dbfile)
        after="\n".join(db.iterdump())
        db.close()
        self.assertEqual(before, after)

if __name__ == "__main__":
    unittest.main()
