"""Security and HTTP smoke tests for the loopback-only Mini App; no real token or data."""
from datetime import datetime,timezone
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode
import hashlib,hmac,json,os,time,unittest
import core,miniapp_server

def signed(user_id=77,at=None,token="fictional-token"):
    when=int(time.time()) if at is None else at
    parts=[("auth_date",str(when)),("user",json.dumps({"id":user_id},separators=(",",":")))]
    secret=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
    message="\n".join(f"{key}={value}" for key,value in sorted(parts))
    hashed=hmac.new(secret,message.encode(),hashlib.sha256).hexdigest()
    return urlencode(parts+[("hash",hashed)])

class MiniAppTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory()
        self.dbpath=Path(self.folder.name)/"private.sqlite3"
        db=core.connect(self.dbpath)
        core.put_config(db,"owner_id","77")
        db.close()
        self.server=miniapp_server.MiniServer(
            ("127.0.0.1",0),miniapp_server.MiniAppHandler,
             "fictional-token","77",db_path=self.dbpath)
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.port=self.server.server_address[1]
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.folder.cleanup()
    def request(self,path,content=None,authorization=None):
        conn=HTTPConnection("127.0.0.1",self.port,timeout=5)
        headers={"Content-Type":"application/json"}
        if authorization:headers["X-Telegram-Init-Data"]=authorization
        method="POST" if content is not None else "GET"
        conn.request(method,path,json.dumps(content).encode() if content is not None else None,headers)
        response=conn.getresponse()
        code=response.status
        data=response.read()
        hdr=dict(response.getheaders())
        conn.close()
        try:data=json.loads(data)
        except (ValueError,UnicodeDecodeError):pass
        return code,data,hdr

    def test_head_request_serves_static_metadata_only(self):
        conn=HTTPConnection("127.0.0.1",self.port,timeout=5)
        conn.request("HEAD","/mini/")
        response=conn.getresponse()
        self.assertEqual(response.status,200)
        self.assertEqual(response.read(),b"")
        conn.close()

    def test_auth_signature_and_owner_required(self):
        raw=signed()
        self.assertTrue(miniapp_server.validate_init_data(raw,"fictional-token","77"))
        self.assertFalse(miniapp_server.validate_init_data(raw,"wrong-token","77"))
        self.assertFalse(miniapp_server.validate_init_data(raw,"fictional-token","999"))
        self.assertFalse(miniapp_server.validate_init_data(raw+"&user=duplicate","fictional-token","77"))
        self.assertFalse(miniapp_server.validate_init_data(raw.replace("77","78"),"fictional-token","77"))
        self.assertFalse(miniapp_server.validate_init_data(signed(at=int(time.time())-4000),"fictional-token","77"))
        self.assertFalse(miniapp_server.validate_init_data("bad%ZZ", "fictional-token","77"))

    def test_assets_public_but_data_forbidden(self):
        code,raw,headers=self.request("/mini/")
        self.assertEqual(code,200)
        self.assertIn("Мои смены".encode("utf-8"),raw)
        self.assertIn("no-store",headers["Cache-Control"])
        code,raw,_=self.request("/mini/api/state",{"month":"2024-07"})
        self.assertEqual(code,403)
        self.assertEqual(raw["error"],"access_denied")

    def test_owner_can_read_without_modifying_state(self):
        db=core.connect(self.dbpath)
        core.corrected_shift(db,"2024-07-10","16:00","02:00",30)
        pre=db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        db.close()
        code,content,headers=self.request("/mini/api/state",{"month":"2024-07"},signed())
        self.assertEqual(code,200)
        self.assertEqual(content["summary"]["shifts"],1)
        self.assertEqual(content["month"],"2024-07")
        self.assertEqual(len(content["shifts"]),1)
        self.assertEqual(content["shifts"][0]["start"],"16:00")
        self.assertFalse(content["capabilities"]["writes"])
        db=core.connect(self.dbpath)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],pre)
        db.close()

    def test_writes_disabled_by_default(self):
        original=os.environ.pop("MINIAPP_ENABLE_WRITES",None)
        try:
            code,content,_=self.request("/mini/api/action",
                {"action":"in","nonce":"a"*32},signed())
            self.assertEqual(code,403)
            self.assertEqual(content["error"],"web_actions_disabled")
            db=core.connect(self.dbpath)
            self.assertIsNone(core.open_shift(db))
            db.close()
        finally:
            if original is not None:os.environ["MINIAPP_ENABLE_WRITES"]=original

    def test_invalid_month_and_route(self):
        code,content,_=self.request("/mini/api/state",{"month":"2024-99"},signed())
        self.assertEqual(code,400)
        code,content,_=self.request("/mini/api/private-data",{"x":1},signed())
        self.assertEqual(code,404)
