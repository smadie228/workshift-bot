"""Private Telegram Mini App backend.

Strict Telegram initData HMAC verification and owner-only API; static assets contain
only demo content. Binds to 127.0.0.1 by default. Do not publish without TLS proxy,
security review and a dedicated hostname. Writes are disabled unless explicitly enabled.
"""
import hashlib,hmac,json,os,re,threading,time,sqlite3,socket
from datetime import datetime,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl,urlsplit
from core import connect,config,shifts,totals,local_day,read_time,paid_hours,open_shift,TZ,toggle
from finance import shift_earnings,payment_totals
from workflows import adjustment_totals,plan_day,break_status,issues,start_break,stop_break
from calendar_views import month_parse
from core import DB_PATH
from miniapp_actions import execute_action
from security_gate import SecurityGate

ROOT=Path(__file__).with_name("miniapp")
MAX_AUTH_AGE=3600
MAX_BODY=8192
LIMIT=60
RATE_WINDOW=60

def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError("duplicate_json_key")
            result[key]=value
        return result
    def constant(value):raise ValueError("nonfinite_json")
    def finite_float(value):
        import math
        result=float(value)
        if not math.isfinite(result):raise ValueError("nonfinite_json")
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant,parse_float=finite_float)
    except RecursionError as exc:raise ValueError("json_too_deep") from exc


def validate_init_data(raw,token,owner_id,current_time=None):
    """Verify Telegram's first-party HMAC, expiry and exact integer owner ID."""
    if not isinstance(raw,str) or not 0<len(raw)<=4096:return False
    if not isinstance(token,str) or not token:return False
    try:
        pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True,max_num_fields=32)
        if len(pairs)!=len({k for k,v in pairs}):return False
        values=dict(pairs)
        digest=values.get("hash","")
        if not re.fullmatch(r"[a-fA-F0-9]{64}",digest):return False
        verify="\n".join(f"{k}={v}" for k,v in sorted(pairs) if k!="hash")
        secret=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,verify.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,digest.lower()):return False
        current=int(time.time() if current_time is None else current_time)
        issued=int(values.get("auth_date","0"))
        user=strict_json(values.get("user","{}"))
        if not isinstance(user,dict):return False
        uid=user.get("id")
        if type(uid) is not int or uid<=0 or not owner_id or uid!=int(owner_id):return False
        return current-MAX_AUTH_AGE<=issued<=current+60
    except (ValueError,TypeError,UnicodeError,OverflowError):
        return False


def read_database(path=None):
    """Read endpoints cannot create a database, run migrations or change rows."""
    target=Path(path or DB_PATH).resolve()
    db=sqlite3.connect(target.as_uri()+"?mode=ro",uri=True,timeout=10,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def summarize(db,month):
    from ledger import month_ledger, available_months
    from calendar_plan import revision
    from batch_plan import combine_revision
    from agenda import daily_agenda
    import actual_shift, attendance_recovery, office_photo
    username=os.environ.get("MINIAPP_BOT_USERNAME","")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}",username):username=""
    data=month_ledger(db,month)
    revisions={day["day"]:revision(db,day["day"]) for day in data["days"]}
    versions=actual_shift.month_versions(db,month)
    actual_revisions=versions["days"]
    estimated_ids = {r["id"] for r in db.execute("SELECT id,note FROM shifts") if '"estimate_basis": "office_photo"' in r["note"]}
    for entry in data["days"]:
        for record in entry["records"]:
            record["office_estimated"] = record["id"] in estimated_ids
            record["revision"]=versions["shifts"][record["id"]]
            record["tracked_breaks"]=versions["tracked"][record["id"]]
    enabled=os.environ.get("MINIAPP_ENABLE_WRITES","0")=="1"
    active=open_shift(db)
    rows=sorted((rec for day in data["days"] for rec in day["records"]),key=lambda rec:rec["start_iso"],reverse=True)
    problems=[item for item in issues(db) if item["day"].startswith(month)]
    for day in data["days"]:
        if day["comparison"]=="difference":
            problems.append({"level":"review","day":day["day"],"text":"Личные часы отличаются от подтверждённого табеля."})
        if day["status"]=="missing" and not any(i["day"]==day["day"] for i in problems):
            problems.append({"level":"review","day":day["day"],"text":"В плане была смена, фактическая запись отсутствует."})
    return {"agenda":daily_agenda(db,datetime.fromisoformat(data["generated_at"])),"month":month,"now":data["generated_at"],"summary":data["summary"],
        "active":{"id":active["id"],"start":read_time(active["start_utc"]).isoformat()} if active else None,
        "break":break_status(db),"shifts":rows,
        "plan":[day["plan"] for day in data["days"] if day["plan"]],
        "office":{day["day"]:day["office_hours"] for day in data["days"] if day["office_hours"] is not None},
        "weeks":data["weeks"],"issues":problems[:8],
        "capabilities":{"writes":enabled,"actual_edits":enabled,"actual_days_off":enabled,"workspace":True,"attendance_recovery":enabled,"break_edits":enabled,"offline_submission":False},
        "attendance_revision":actual_shift.attendance_revision(db),
        "attendance_hints":attendance_recovery.hints(db),
        "deleted_shifts":attendance_recovery.deleted_list(db,month),
        "office_photo":office_photo.month_proposals(db,month),
        "actual_editor":{"revisions":actual_revisions,"default_break_minutes":int(config(db,"default_break_minutes","30"))},
        "timezone":"Europe/Moscow","periods":data["periods"],
        "office_status":data["reconciliation"],"ledger":data["days"],
        "rates":data["rates"],"forecast":data["forecast"],"months":available_months(db),
        "plan_editor":{"mode":"chat_confirmation","bot_username":username,
                       "revisions":revisions,"direct_writes":os.environ.get("MINIAPP_ENABLE_WRITES","0")=="1","supports_past_plan":True,"supports_exact_correction":True,"supports_unset":True,"supports_batch":True,"batch_revision":combine_revision(revisions)}}

class RateLimiter:
    def __init__(self):
        self._lock=threading.Lock()
        self._events={}
    def allow(self,identity):
        stamp=time.monotonic()
        with self._lock:
            self._events={key:[v for v in values if stamp-v<RATE_WINDOW]
                          for key,values in self._events.items()
                          if values and stamp-values[-1]<RATE_WINDOW}
            if identity not in self._events and len(self._events)>=256:return False
            recent=self._events.setdefault(identity,[])
            if len(recent)>=LIMIT:return False
            recent.append(stamp)
            return True


ASSETS={
    "/mini/":("index.html","text/html; charset=utf-8"),
    "/mini/app.css":("app.css","text/css; charset=utf-8"),
    "/mini/app.js":("app.js","application/javascript; charset=utf-8"),
    "/mini/logo.svg":("logo.svg","image/svg+xml"),
    "/mini/echarts.min.js":("vendor/echarts.min.js","application/javascript; charset=utf-8"),
    "/mini/telegram-web-app.js":("vendor/telegram-web-app.js","application/javascript; charset=utf-8")}


class MiniAppHandler(BaseHTTPRequestHandler):
    server_version="WorkShift"
    sys_version=""
    def version_string(self):return self.server_version
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
    def log_message(self,format,*args):
        # Never log request URLs, headers, initData or personal data.
        return
    def _header(self,code,mimetype="application/json",n=None):
        self.send_response(code)
        self.send_header("Content-Type",mimetype)
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Cache-Control","no-store")
        self.send_header("Permissions-Policy","camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
            "default-src 'none'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; object-src 'none'; "
            "frame-ancestors 'self' https://web.telegram.org https://*.web.telegram.org")
        if code==429:self.send_header("Retry-After",str(RATE_WINDOW))
        if n is not None:self.send_header("Content-Length",str(n))
        self.end_headers()
    def _reply(self,code,body):
        raw=json.dumps(body,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
        try:
            self._header(code,"application/json; charset=utf-8",len(raw))
            self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,socket.timeout):pass
    def _asset(self,head=False):
        path=urlsplit(self.path).path
        if path not in ASSETS:return self._header(404,"application/json",0) if head else self._reply(404,{"error":"not_found"})
        name,mime=ASSETS[path]
        file=ROOT/name
        if not file.is_file():return self._header(404,"application/json",0)
        data=file.read_bytes()
        self._header(200,mime,len(data))
        if not head:self.wfile.write(data)
    def do_HEAD(self):self._asset(head=True)
    def do_GET(self):self._asset()
    def do_POST(self):
        path=urlsplit(self.path).path
        if path not in ("/mini/api/state","/mini/api/action","/mini/api/analytics","/mini/api/workspace","/mini/api/preview","/mini/api/report","/mini/api/security"):
            return self._reply(404,{"error":"not_found"})
        for name in ("Origin","X-Telegram-Init-Data","X-Workshift-Unlock","Sec-Fetch-Site","Content-Type"):
            if len(self.headers.get_all(name,[]))>1:
                return self._reply(400,{"error":"ambiguous_headers"})
        origin=self.headers.get("Origin")
        expected=os.environ.get("MINIAPP_PUBLIC_ORIGIN","").rstrip("/")
        if (origin and origin!=expected) or self.headers.get("Sec-Fetch-Site")=="cross-site":
            return self._reply(403,{"error":"origin_denied"})
        lengths=self.headers.get_all("Content-Length",[])
        if self.headers.get("Transfer-Encoding") or len(lengths)!=1:
            return self._reply(400,{"error":"bad_framing"})
        if self.headers.get_content_type()!="application/json":
            return self._reply(415,{"error":"json_required"})
        try:
            length=int(lengths[0])
            if not 0<length<=MAX_BODY:raise ValueError("bad_size")
        except (ValueError,TypeError):
            return self._reply(400,{"error":"bad_request"})
        init=self.headers.get("X-Telegram-Init-Data","")
        valid=validate_init_data(init,self.server.token,self.server.owner_id)
        # Invalid signatures cannot exhaust the authenticated owner's allowance.
        identity="owner:"+str(self.server.owner_id) if valid else "ip:"+self.client_address[0]
        if not self.server.limiter.allow(identity):
            return self._reply(429,{"error":"too_many_requests"})
        if not valid:return self._reply(403,{"error":"access_denied"})
        try:
            body=self.rfile.read(length)
            if len(body)!=length:raise ValueError("short_body")
            payload=strict_json(body)
            if not isinstance(payload,dict):raise ValueError("bad_body")
        except (ValueError,TypeError,UnicodeError,socket.timeout):
            return self._reply(400,{"error":"bad_request"})
        db=None
        try:
            # A user-enabled PIN/biometric lock must be enforced by the server,
            # not only by hiding HTML. An authenticated Telegram launch remains
            # necessary even after a valid second-factor unlock.
            security=self.server.security_gate
            launch_hash=hashlib.sha256(init.encode("utf-8")).hexdigest()
            unlock=self.headers.get("X-Workshift-Unlock","")
            if path=="/mini/api/security" or (path=="/mini/api/workspace" and payload.get("view")=="security"):
                # Reuse the existing reverse-proxy-allowed owner workspace route.
                # PIN and biometric actions never access or mutate business SQLite.
                if expected and origin!=expected:return self._reply(403,{"error":"origin_required"})
                details={key:value for key,value in payload.items() if key!="view"}
                status,result=security.process(details,launch_hash,unlock)
                return self._reply(status,result)
            if security.enabled and not security.authorized(unlock,launch_hash):
                return self._reply(423,{"error":"security_locked"})
            if path in ("/mini/api/workspace", "/mini/api/preview", "/mini/api/report"):
                import web_workspace
                db=read_database(self.server.db_path)
                if path=="/mini/api/preview":
                    if payload.get("kind")=="break_change":
                        import web_breaks
                        return self._reply(200,web_breaks.preview(db,payload))
                    return self._reply(200,web_workspace.preview(db,payload))
                if path=="/mini/api/workspace":
                    if payload.get("view")=="payment_history":
                        if set(payload)!={"view","limit"}:raise ValueError("unknown_fields")
                        return self._reply(200,web_workspace.payment_history(db,payload["limit"]))
                    if payload.get("view")=="breaks":
                        import web_breaks
                        return self._reply(200,web_breaks.details(db,payload))
                    if payload.get("view")=="activity":
                        import web_activity
                        return self._reply(200,web_activity.page(db,payload))
                    if set(payload)!={"month"}:raise ValueError("unknown_fields")
                    return self._reply(200,web_workspace.list_data(db,payload["month"]))
                if expected and origin!=expected:return self._reply(403,{"error":"origin_required"})
                if not self.server._report_slot.acquire(blocking=False):return self._reply(429,{"error":"report_busy"})
                try:
                    import web_report
                    return self._reply(200,web_report.build(db,payload))
                finally:self.server._report_slot.release()
            if path=="/mini/api/analytics":
                if set(payload)-{"year"}:raise ValueError("unknown_fields")
                from analytics import year_analytics
                year=payload.get("year")
                db=read_database(self.server.db_path)
                return self._reply(200,year_analytics(db,year))
            if path=="/mini/api/state":
                if set(payload)-{"month"}:raise ValueError("unknown_fields")
                month=payload.get("month",datetime.now(TZ).strftime("%Y-%m"))
                if not isinstance(month,str):raise ValueError("invalid_month")
                month_parse(month)
                db=read_database(self.server.db_path)
                return self._reply(200,summarize(db,month))
            if os.environ.get("MINIAPP_ENABLE_WRITES","0")!="1":
                return self._reply(403,{"error":"web_actions_disabled"})
            if expected and origin != expected:
                return self._reply(403,{"error":"origin_required"})
            db=connect(self.server.db_path)
            return self._reply(200,execute_action(db,self.server.owner_id,payload))
        except ValueError as exc:
            return self._reply(400,{"error":str(exc)})
        except Exception:
            return self._reply(500,{"error":"server_error"})
        finally:
            if db is not None:db.close()


class MiniServer(ThreadingHTTPServer):
    allow_reuse_address=True
    daemon_threads=True
    request_queue_size=32
    def __init__(self,address,handler,token,owner_id,db_path=None):
        self.db_path=db_path
        self.token=token
        self.owner_id=owner_id
        self.limiter=RateLimiter()
        from core import DB_PATH
        default_security_file=Path(db_path or DB_PATH).parent/"miniapp-security.json"
        self.security_gate=SecurityGate(os.getenv("WORKSHIFT_SECURITY_FILE",str(default_security_file)))
        self._report_slot=threading.BoundedSemaphore(1)
        self._slots=threading.BoundedSemaphore(16)
        super().__init__(address,handler)
    def process_request(self,request,client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:super().process_request(request,client_address)
        except Exception:
            self._slots.release()
            raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self._slots.release()
    def handle_error(self,request,client_address):
        # No tracebacks containing request or credential material.
        return


def main():
    token=os.environ.get("TELEGRAM_BOT_TOKEN","")
    if not token:raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    db=connect()
    try:owner=config(db,"owner_id")
    finally:db.close()
    if not owner:raise RuntimeError("Owner is not paired")
    port=int(os.environ.get("MINIAPP_PORT","8765"))
    if not 1024<=port<=65535:raise ValueError("invalid_port")
    server=MiniServer(("127.0.0.1",port),MiniAppHandler,token,owner)
    print("MINIAPP_LOOPBACK_READY",port,flush=True)
    server.serve_forever(poll_interval=0.5)

if __name__=="__main__":
    main()
