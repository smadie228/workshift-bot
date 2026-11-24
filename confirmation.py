"""Bind a confirmation button to the exact values that were shown, for ten minutes."""
import hashlib,hmac,json,secrets,time

def fingerprint(flow):
    visible={k:v for k,v in flow.items() if not k.startswith('_confirmation')}
    return hashlib.sha256(json.dumps(visible,sort_keys=True,ensure_ascii=False,default=str,separators=(',',':')).encode()).hexdigest()

def issue(flow):
    digest=fingerprint(flow)
    if flow.get('_confirmation_digest')!=digest or time.time()-flow.get('_confirmation_at',0)>600:
        flow['_confirmation_token']=secrets.token_hex(8)
        flow['_confirmation_at']=time.time();flow['_confirmation_digest']=digest
    return flow['_confirmation_token']

def valid(flow,supplied):
    return bool(flow and isinstance(supplied,str) and flow.get('_confirmation_token')
                and hmac.compare_digest(flow['_confirmation_token'],supplied)
                and 0<=time.time()-flow.get('_confirmation_at',0)<=600
                and flow.get('_confirmation_digest')==fingerprint(flow))