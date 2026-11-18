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

ROOT=Path(__file__).with_name("miniapp")
MAX_AUTH_AGE=3600
MAX_BODY=8192
LIMIT=60
RATE_WINDOW=60

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
        user=json.loads(values.get("user","{}"))
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
    y,m=month_parse(month)
    summary=totals(db,month)
    adjustments=adjustment_totals(db,month)
    received=payment_totals(db,month)
    schedule=[]
    for i in range(1,32):
        try:day=f"{month}-{i:02d}";datetime.strptime(day,"%Y-%m-%d")
        except ValueError:continue
        p=plan_day(db,day)
        if p:schedule.append(p)
    rows=[]
    for shift in shifts(db,month):
        start=read_time(shift["start_utc"])
        end=read_time(shift["end_utc"]) if shift["end_utc"] else None
        rows.append({"id":shift["id"],"day":start.strftime("%Y-%m-%d"),
            "start":start.strftime("%H:%M"),
            "end":end.strftime("%H:%M") if end else None,
            "next_day":bool(end and end.date()!=start.date()),
            "break_minutes":shift["break_minutes"],
            "hours":round(paid_hours(shift),3) if end else None,
            "earnings":round(shift_earnings(db,shift),2) if end else None,
            "source":"import" if shift["origin"]=="import_excel" else
                     "manual" if shift["origin"]=="manual" else "button"})
    office={r["day"]:r["hours"] for r in db.execute(
        "SELECT day,hours FROM office_days WHERE day>=? AND day<?",
        (month+"-01",f"{y+(m==12):04d}-{1 if m==12 else m+1:02d}-01"))}
    weekly=[]
    today=datetime.now(TZ).date()
    all_shifts=shifts(db)
    for i in range(5,-1,-1):
        monday=today-timedelta(days=today.weekday()+7*i)
        worked=[s for s in all_shifts if s["end_utc"]
            and monday.isoformat()<=local_day(s["start_utc"])<(monday+timedelta(days=7)).isoformat()]
        weekly.append({"label":monday.strftime("%d.%m"),
          "hours":round(sum(paid_hours(s) for s in worked),2)})
    active=open_shift(db)
    return {"month":month,"now":datetime.now(TZ).isoformat(),
        "summary":{"hours":round(summary["hours"],3),"shifts":summary["shifts"],
           "earned":round(summary["earnings"],2),
           "additional":adjustments,"received":round(sum(received.values()),2),
           "total":round(summary["earnings"]+adjustments["net_adjustment"],2)},
        "active":{"id":active["id"],"start":read_time(active["start_utc"]).isoformat()}
            if active else None,
        "break":break_status(db),
        "shifts":rows,"plan":schedule,"office":office,
        "weeks":weekly,"issues":issues(db)[:8],
        "capabilities":{"writes":os.environ.get("MINIAPP_ENABLE_WRITES","0")=="1",
                        "offline_submission":False},
        "timezone":"Europe/Moscow",
        "periods":[{"period":part,**totals(db,month,part)} for part in (1,2)],
        "office_status":{"confirmed_days":len(office),"complete":False}}

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
    "/mini/logo.svg":("logo.svg","image/svg+xml")}


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
            "default-src 'none'; script-src 'self' https://telegram.org; "
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
        if path not in ("/mini/api/state","/mini/api/action"):
            return self._reply(404,{"error":"not_found"})
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
            payload=json.loads(body)
            if not isinstance(payload,dict):raise ValueError("bad_body")
        except (ValueError,TypeError,UnicodeError,socket.timeout):
            return self._reply(400,{"error":"bad_request"})
        db=None
        try:
            if path=="/mini/api/state":
                month=payload.get("month",datetime.now(TZ).strftime("%Y-%m"))
                if not isinstance(month,str):raise ValueError("invalid_month")
                month_parse(month)
                db=read_database(self.server.db_path)
                return self._reply(200,summarize(db,month))
            if os.environ.get("MINIAPP_ENABLE_WRITES","0")!="1":
                return self._reply(403,{"error":"web_actions_disabled"})
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
