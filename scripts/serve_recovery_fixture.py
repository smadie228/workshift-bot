"""Short-lived loopback test server. Fictional DB and token; never reads production.
Run from the source tree, optionally through an SSH loopback tunnel.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import signal
import threading
import time
from urllib.parse import urlencode


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18766)
    parser.add_argument('--origin',default='http://127.0.0.1:19866');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='workshift-write-e2e-') as folder:
        os.environ['WORKSHIFTS_DATA']=folder
        os.environ['MINIAPP_ENABLE_WRITES']='1'
        os.environ['MINIAPP_PUBLIC_ORIGIN']=args.origin
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        import core,miniapp_server
        path=Path(folder)/'fixture.sqlite3';db=core.connect(path);core.put_config(db,'owner_id','77')
        core.corrected_shift(db,'2024-07-10','16:00','02:00',30)
        import office_photo
        db.execute('INSERT INTO office_image_days VALUES(?,?,?,?)',('2024-07-10','fictional-photo',9.23,office_photo.STATUS))
        db.close()
        server=miniapp_server.MiniServer(('127.0.0.1',args.port),miniapp_server.MiniAppHandler,'fictional-write-e2e-token','77',db_path=path)
        signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
        timer=threading.Timer(900,server.shutdown);timer.daemon=True;timer.start()
        print('WRITES_FIXTURE_READY',args.port,flush=True)
        try:server.serve_forever(poll_interval=.2)
        finally:timer.cancel();server.server_close()


if __name__=='__main__':main()
