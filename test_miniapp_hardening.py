"""Regression tests with temporary databases and fictional Telegram signatures."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlencode
import core
import miniapp_actions
import miniapp_server
import workflows
import test_miniapp
from test_miniapp import signed


def sign_fields(user, at=None, extra=None):
    values={"auth_date":str(int(time.time()) if at is None else at),"user":json.dumps(user,separators=(",",":"))}
    values.update(extra or {})
    secret=hmac.new(b"WebAppData",b"fictional-token",hashlib.sha256).digest()
    message="\n".join(k+"="+v for k,v in sorted(values.items()))
    values["hash"]=hmac.new(secret,message.encode(),hashlib.sha256).hexdigest()
    return urlencode(values)


class AuthEdgeTests(unittest.TestCase):
    def test_signed_non_object_user_is_rejected(self):
        for user in ([],None,"text",77):
            with self.subTest(user=user):
                self.assertFalse(miniapp_server.validate_init_data(sign_fields(user),"fictional-token","77"))

    def test_only_exact_integer_user_ids_are_accepted(self):
        for uid in ("77",77.0,True,False,None,-77):
            with self.subTest(uid=uid):
                self.assertFalse(miniapp_server.validate_init_data(sign_fields({"id":uid}),"fictional-token","77"))

    def test_auth_time_boundaries(self):
        at=int(time.time())
        for delta,valid in ((-3601,False),(-3600,True),(60,True),(61,False)):
            self.assertEqual(miniapp_server.validate_init_data(signed(at=at+delta),"fictional-token","77",at),valid)

    def test_hmac_includes_optional_signature_field(self):
        raw=sign_fields({"id":77},extra={"signature":"fictional-extra-signature"})
        self.assertTrue(miniapp_server.validate_init_data(raw,"fictional-token","77"))
        self.assertFalse(miniapp_server.validate_init_data(raw.replace("fictional-extra-signature","different"),"fictional-token","77"))

    def test_limiter_bounds_and_expiry(self):
        limiter=miniapp_server.RateLimiter()
        with patch("miniapp_server.time.monotonic",return_value=1):
            for i in range(256):self.assertTrue(limiter.allow(str(i)))
            self.assertFalse(limiter.allow("overflow"))
        with patch("miniapp_server.time.monotonic",return_value=100):
            self.assertTrue(limiter.allow("new"))
            self.assertEqual(len(limiter._events),1)


class HttpHardeningTests(unittest.TestCase):
    setUp=test_miniapp.MiniAppTests.setUp
    tearDown=test_miniapp.MiniAppTests.tearDown
    request=test_miniapp.MiniAppTests.request

    def raw(self,body,headers=None):
        conn=HTTPConnection("127.0.0.1",self.port,timeout=5)
        all_headers={"Content-Type":"application/json","X-Telegram-Init-Data":signed()}
        all_headers.update(headers or {})
        conn.request("POST","/mini/api/state",body,all_headers)
        reply=conn.getresponse();code=reply.status;data=reply.read();conn.close()
        return code,data

    def test_cross_origin_and_null_origin_denied(self):
        with patch.dict(os.environ,{"MINIAPP_PUBLIC_ORIGIN":"https://example.com"}):
            for origin in ("https://other.example.com","null","http://example.com"):
                self.assertEqual(self.raw(b"{}",{"Origin":origin})[0],403)
            self.assertEqual(self.raw(b"{}",{"Origin":"https://example.com"})[0],200)
            self.assertEqual(self.raw(b"{}",{"Sec-Fetch-Site":"cross-site"})[0],403)

    def test_json_type_and_size_limits(self):
        self.assertEqual(self.raw(b"{}",{"Content-Type":"text/plain"})[0],415)
        self.assertEqual(self.raw(b"x"*(miniapp_server.MAX_BODY+1))[0],400)
        for data in (b"[]",b"null",b"\xff",b"{bad",b""):
            self.assertEqual(self.raw(data)[0],400)

    def test_ambiguous_framing_is_rejected(self):
        conn=HTTPConnection("127.0.0.1",self.port,timeout=5)
        conn.putrequest("POST","/mini/api/state")
        conn.putheader("Content-Type","application/json")
        conn.putheader("Content-Length","2")
        conn.putheader("Content-Length","3")
        conn.endheaders(b"{}")
        response=conn.getresponse();self.assertEqual(response.status,400);response.read();conn.close()
        self.assertEqual(self.raw(b"{}",{"Transfer-Encoding":"chunked"})[0],400)

    def test_invalid_request_flood_does_not_lock_out_owner(self):
        for _ in range(miniapp_server.LIMIT):
            self.assertEqual(self.request("/mini/api/state",{})[0],403)
        code,_,headers=self.request("/mini/api/state",{})
        self.assertEqual(code,429);self.assertIn("Retry-After",headers)
        self.assertEqual(self.request("/mini/api/state",{},signed())[0],200)

    def test_signed_user_array_returns_denial_not_exception(self):
        self.assertEqual(self.request("/mini/api/state",{},sign_fields([]))[0],403)

    def test_read_api_leaves_every_table_unchanged(self):
        db=core.connect(self.dbpath)
        before="\n".join(db.iterdump());db.close()
        code,state,_=self.request("/mini/api/state",{"month":"2024-07"},signed())
        self.assertEqual(code,200);self.assertEqual(len(state["periods"]),2)
        db=core.connect(self.dbpath)
        self.assertEqual("\n".join(db.iterdump()),before);db.close()
        db=miniapp_server.read_database(self.dbpath)
        with self.assertRaises(sqlite3.OperationalError):db.execute("DELETE FROM shifts")
        db.close()

    def test_static_allowlist_never_serves_private_paths(self):
        for path in ("/.env","/mini/../core.py","/mini/%2e%2e/core.py","/mini/shifts.sqlite3","/mini/api/state","/mini/unknown.js"):
            self.assertEqual(self.request(path)[0],404)

    def test_headers_allow_telegram_frame_without_general_framing(self):
        code,_,headers=self.request("/mini/")
        self.assertEqual(code,200)
        self.assertNotIn("X-Frame-Options",headers)
        self.assertIn("frame-ancestors 'self' https://web.telegram.org",headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline",headers["Content-Security-Policy"])
        self.assertNotIn("Python",headers["Server"])

    def test_http_write_replay_returns_identical_result(self):
        with patch.dict(os.environ,{"MINIAPP_ENABLE_WRITES":"1"}):
            payload={"action":"in","nonce":"a"*32}
            first=self.request("/mini/api/action",payload,signed())
            second=self.request("/mini/api/action",payload,signed())
            self.assertEqual(first[0],200);self.assertEqual(second[1],first[1])
        db=core.connect(self.dbpath)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],1);db.close()


class AtomicActionTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory();self.path=Path(self.folder.name)/"test.sqlite3"
        self.db=core.connect(self.path)
        self.at=datetime(2024,7,10,9,tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close();self.folder.cleanup()

    def action(self,action,nonce,shift=None,brk=None,minutes=0):
        payload={"action":action,"nonce":nonce*32,"shift_id":shift,"break_id":brk}
        instant=self.at+timedelta(minutes=minutes)
        with patch("core.now",return_value=instant),patch("workflows.now",return_value=instant),patch("miniapp_actions.now",return_value=instant):
            return miniapp_actions.execute_action(self.db,77,payload)

    def test_clock_in_and_out_replays_do_not_toggle_again(self):
        first=self.action("in","a")
        self.assertEqual(self.action("in","a",minutes=1),first)
        out=self.action("out","b",first["id"],minutes=60)
        self.assertEqual(self.action("out","b",first["id"],minutes=61),out)
        self.assertIsNone(core.open_shift(self.db))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],1)

    def test_nonce_reuse_with_different_request_is_rejected(self):
        first=self.action("in","a")
        with self.assertRaisesRegex(ValueError,"nonce_conflict"):
            self.action("out","a",first["id"],minutes=60)
        self.assertIsNotNone(core.open_shift(self.db))

    def test_stale_end_cannot_close_new_shift(self):
        first=self.action("in","a")
        self.action("out","b",first["id"],minutes=60)
        second=self.action("in","c",minutes=120)
        with self.assertRaises(ValueError):self.action("out","d",first["id"],minutes=180)
        self.assertEqual(core.open_shift(self.db)["id"],second["id"])

    def test_break_replays_preserve_new_break(self):
        sid=self.action("in","a")["id"]
        first=self.action("pause_start","b",sid,minutes=30)
        self.assertEqual(self.action("pause_start","b",sid,minutes=31),first)
        bid=workflows.active_break(self.db)["id"]
        end=self.action("pause_end","c",sid,bid,minutes=40)
        self.action("pause_start","d",sid,minutes=50)
        new_id=workflows.active_break(self.db)["id"]
        self.assertEqual(self.action("pause_end","c",sid,bid,minutes=55),end)
        self.assertEqual(workflows.active_break(self.db)["id"],new_id)
        with self.assertRaises(ValueError):self.action("pause_end","e",sid,bid,minutes=56)
        self.assertEqual(workflows.active_break(self.db)["id"],new_id)

    def test_business_event_and_nonce_roll_back_together(self):
        with patch("miniapp_actions.log",side_effect=ValueError("audit_failure")):
            with self.assertRaisesRegex(ValueError,"audit_failure"):self.action("in","a")
        for table in ("shifts","processed_updates","events","miniapp_operations"):
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0)
        self.assertTrue(self.action("in","a")["success"])

    def test_required_expected_ids_are_strict(self):
        sid=self.action("in","a")["id"]
        for value in (None,str(sid),True,-1):
            with self.assertRaises(ValueError):self.action("out","b",value,minutes=1)
        with self.assertRaises(ValueError):self.action("pause_end","c",sid,None,minutes=1)
        self.assertIsNotNone(core.open_shift(self.db))

    def test_nested_helpers_require_an_outer_transaction(self):
        for operation in (lambda:core.toggle(self.db,1,manage_transaction=False),
                          lambda:workflows.start_break(self.db,manage_transaction=False),
                          lambda:workflows.stop_break(self.db,manage_transaction=False)):
            with self.assertRaises(RuntimeError):operation()

    def concurrent(self,nonces):
        barrier=Barrier(len(nonces))
        def run(nonce):
            db=core.connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:return miniapp_actions.execute_action(db,77,{"action":"in","nonce":nonce*32})
                except ValueError:return {"rejected":True}
            finally:db.close()
        with ThreadPoolExecutor(max_workers=len(nonces)) as pool:return list(pool.map(run,nonces))

    def test_concurrent_distinct_arrivals_cannot_create_departure(self):
        results=self.concurrent(["a","b"])
        self.assertEqual(sum(bool(x.get("success")) for x in results),1)
        self.assertIsNotNone(core.open_shift(self.db))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],1)

    def test_concurrent_replay_is_exactly_once(self):
        results=self.concurrent(["a","a"])
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM miniapp_operations").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],1)