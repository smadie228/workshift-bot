"""Private Telegram Mini App backend.

Strict Telegram initData HMAC verification and owner-only API; static assets contain
only demo content. Binds to 127.0.0.1 by default. Do not publish without TLS proxy,
security review and a dedicated hostname. Writes are disabled unless explicitly enabled.
"""
import hashlib,hmac,json,os,re,threading,time
from datetime import datetime,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl,urlsplit
from core import connect,config,shifts,totals,local_day,read_time,paid_hours,open_shift,TZ,toggle
from finance import shift_earnings,payment_totals
from workflows import adjustment_totals,plan_day,break_status,issues,start_break,stop_break
from calendar_views import month_parse
from secrets import token_hex

ROOT=Path(__file__).with_name("miniapp")
MAX_AUTH_AGE=3600
MAX_BODY=8192
LIMIT=60
RATE_WINDOW=60

def validate_init_data(raw,token,owner_id,current_time=None):
    """Telegram first-party HMAC validation plus age and owner checks."""
    if not isinstance(raw,str) or not 0<len(raw)<=4096:return False
    try:
        pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True,max_num_fields=32)
    except ValueError:
        return False
    if len(pairs)!=len({k for k,v in pairs}):return False
    values=dict(pairs)
    digest=values.get("hash","")
    if not re.fullmatch(r"[a-fA-F0-9]{64}",digest):return False
    verify="\n".join(f"{k}={v}" for k,v in sorted(pairs) if k!="hash")
    secret=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
    expected=hmac.new(secret,verify.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,digest.lower()):return False
    current=int(time.time() if current_time is None else current_time)
    try:
        issued=int(values.get("auth_date","0"))
        user=json.loads(values.get("user","{}"))
        uid=int(user.get("id",-1))
    except (ValueError,TypeError,json.JSONDecodeError):
        return False
    if not owner_id or uid!=int(owner_id):return False
    if issued>current+60 or issued<current-MAX_AUTH_AGE:return False
    return True

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
        "timezone":"Europe/Moscow"}

class RateLimiter:
    def __init__(self):
        self._lock=threading.Lock()
        self._events={}
    def allow(self,ip):
        now_ts=time.monotonic()
        with self._lock:
            recent=[i for i in self._events.get(ip,[]) if now_ts-i<RATE_WINDOW]
            if len(recent)>=LIMIT:
                self._events[ip]=recent
                return False
            recent.append(now_ts)
            self._events[ip]=recent
            return True

class MiniAppHandler(BaseHTTPRequestHandler):
    server_version="WorkShift"
    def log_message(self,format,*args):
        # Do not leak tokens, initData or request bodies into logs.
        return
    def _header(self,code,mimetype="application/json",n=None):
        self.send_response(code)
        self.send_header("Content-Type",mimetype)
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("Cache-Control","no-store")
        self.send_header("Permissions-Policy","camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
           "default-src 'none'; script-src 'self' https://telegram.org; "
           "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
           "base-uri 'none'; form-action 'none'")
        if n is not None:self.send_header("Content-Length",str(n))
        self.end_headers()
    def _reply(self,code,body):
        raw=json.dumps(body,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self._header(code,"application/json; charset=utf-8",len(raw))
        self.wfile.write(raw)
    def do_HEAD(self):
        path=urlsplit(self.path).path
        kinds={
           "/mini/":("index.html","text/html; charset=utf-8"),
           "/mini/app.css":("app.css","text/css; charset=utf-8"),
           "/mini/app.js":("app.js","application/javascript; charset=utf-8"),
           "/mini/logo.svg":("logo.svg","image/svg+xml")
        }
        if path not in kinds:
            return self._header(404,"application/json",0)
        local=ROOT/kinds[path][0]
        if not local.is_file():
            return self._header(404,"application/json",0)
        self._header(200,kinds[path][1],local.stat().st_size)
    def do_GET(self):
        path=urlsplit(self.path).path
        mapping={
           "/mini/":("index.html","text/html; charset=utf-8"),
           "/mini/app.css":("app.css","text/css; charset=utf-8"),
           "/mini/app.js":("app.js","application/javascript; charset=utf-8"),
           "/mini/logo.svg":("logo.svg","image/svg+xml")
        }
        if path not in mapping:
            return self._reply(404,{"error":"not_found"})
        file=ROOT/mapping[path][0]
        if not file.is_file():
            return self._reply(404,{"error":"missing_asset"})
        data=file.read_bytes()
        self._header(200,mapping[path][1],len(data))
        self.wfile.write(data)
    def do_POST(self):
        path=urlsplit(self.path).path
        if path not in ("/mini/api/state","/mini/api/action"):
            return self._reply(404,{"error":"not_found"})
        if not self.server.limiter.allow(self.client_address[0]):
            return self._reply(429,{"error":"too_many_requests"})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<0 or length>MAX_BODY:raise ValueError("bad_size")
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):raise ValueError("bad_body")
        except (ValueError,json.JSONDecodeError,TypeError):
            return self._reply(400,{"error":"bad_request"})
        init=self.headers.get("X-Telegram-Init-Data","")
        if not validate_init_data(init,self.server.token,self.server.owner_id):
            return self._reply(403,{"error":"access_denied"})
        db=connect(self.server.db_path)
        try:
            if path=="/mini/api/state":
                month=payload.get("month",datetime.now(TZ).strftime("%Y-%m"))
                if not isinstance(month,str):raise ValueError("invalid_month")
                result=summarize(db,month)
                return self._reply(200,result)
            if os.environ.get("MINIAPP_ENABLE_WRITES","0")!="1":
                return self._reply(403,{"error":"web_actions_disabled"})
            action=payload.get("action")
            nonce=payload.get("nonce")
            if not isinstance(nonce,str) or not re.fullmatch(r"[a-f0-9]{32}",nonce):
                raise ValueError("invalid_nonce")
            if action not in ("in","out","pause_start","pause_end"):
                raise ValueError("unknown_action")
            current=open_shift(db)
            if action=="in":
                if current:raise ValueError("Смена уже начата.")
                synthetic=-int(hashlib.sha256(("mini-"+nonce).encode()).hexdigest()[:15],16)
                kind,s=toggle(db,synthetic)
                result={"event":kind,"id":s["id"] if s else None}
            elif action=="out":
                if not current:raise ValueError("Нет начатой смены.")
                if break_status(db)["on_break"]:
                    raise ValueError("Заверши перерыв перед уходом.")
                synthetic=-int(hashlib.sha256(("mini-"+nonce).encode()).hexdigest()[:15],16)
                kind,s=toggle(db,synthetic)
                result={"event":kind,"id":s["id"] if s else None}
            elif action=="pause_start":
                result={"event":"break_start","id":start_break(db)}
            else:
                result={"event":"break_end",**stop_break(db)}
            return self._reply(200,{"success":True,**result})
        except ValueError as exc:
            return self._reply(400,{"error":str(exc)})
        except Exception:
            return self._reply(500,{"error":"server_error"})
        finally:
            db.close()

class MiniServer(ThreadingHTTPServer):
    allow_reuse_address=True
    daemon_threads=True
    def __init__(self,address,handler,token,owner_id,db_path=None):
        self.db_path=db_path
        self.token=token
        self.owner_id=owner_id
        self.limiter=RateLimiter()
        super().__init__(address,handler)

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
