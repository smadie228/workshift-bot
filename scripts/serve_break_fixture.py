"""Short-lived loopback fixture: fictional data and credential, never live data."""
import argparse,os,signal,sys,tempfile,threading
from datetime import timedelta
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18860);parser.add_argument('--origin',default='http://127.0.0.1:19860');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='workshift-break-fixture-') as folder:
        os.environ['WORKSHIFTS_DATA']=folder;os.environ['MINIAPP_ENABLE_WRITES']='1';os.environ['MINIAPP_PUBLIC_ORIGIN']=args.origin
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        import core,workflows,miniapp_server
        path=Path(folder)/'fictional.sqlite3';db=core.connect(path);core.put_config(db,'owner_id','77')
        core.corrected_shift(db,'2024-07-10','16:00','02:00',30)
        now=core.now();db.execute('INSERT INTO shifts(start_utc,break_minutes,created_utc,updated_utc) VALUES(?,?,?,?)',(core.iso(now-timedelta(hours=3)),30,core.iso(now),core.iso(now)));workflows.start_break(db,at=now-timedelta(hours=1));db.close()
        server=miniapp_server.MiniServer(('127.0.0.1',args.port),miniapp_server.MiniAppHandler,'fictional-write-e2e-token','77',db_path=path)
        signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
        timer=threading.Timer(900,server.shutdown);timer.daemon=True;timer.start();print('BREAK_FIXTURE_READY',args.port,flush=True)
        try:server.serve_forever(poll_interval=.2)
        finally:timer.cancel();server.server_close()

if __name__=='__main__':main()
