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
        sid=core.corrected_shift(db,'2024-07-10','16:00','02:00',30)
        db.execute('UPDATE shifts SET note=? WHERE id=?',(json.dumps({'estimate_basis':'office_photo'}),sid))
        removed=core.corrected_shift(db,'2024-07-09','09:00','18:00',30)
        import miniapp_actions,actual_shift
        miniapp_actions.execute_action(db,77,{'action':'delete_shift','nonce':'b'*32,'shift_id':removed,'expected_revision':actual_shift.shift_revision(db,removed)})
        for i in range(38):
            core.log(db,'plan_change',details={'day':'2024-07-11','before':None,'after':{'status':'off'}})
        for i in range(3):
            core.log(db,'payment_received',details={'paid_on':'2024-07-12','amount':str(10+i),'note':'fictional-only-do-not-return'})

        db.close()
        server=miniapp_server.MiniServer(('127.0.0.1',args.port),miniapp_server.MiniAppHandler,'fictional-write-e2e-token','77',db_path=path)
        signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
        timer=threading.Timer(900,server.shutdown);timer.daemon=True;timer.start()
        print('WRITES_FIXTURE_READY',args.port,flush=True)
        try:server.serve_forever(poll_interval=.2)
        finally:timer.cancel();server.server_close()


if __name__=='__main__':main()
