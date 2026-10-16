"""One-off reserved primary SEC GET; no redirects, retries, or quota resets."""
from datetime import datetime, timezone, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from market_checker_app.collectors.sec_edgar_client import SecEdgarClient, _allowed_sec_url
from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parent
DB = Path('/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db')
UA = 'JOHNY-SKORE primary-evidence-research/1.0 (public project contact: github.com/littleleg198602/JOHNY-SKORE)'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def capture(url, name, cap, justification=None):
    _allowed_sec_url(url)
    if not DB.is_file():
        raise ValueError('Original retained SEC lease database is missing; never create a substitute')
    if not 1 <= cap <= 12_000_000 or cap > 4_000_000 and not justification:
        raise ValueError('Larger complete-body cap requires an explicit pre-request justification')
    ROOT.mkdir(exist_ok=True)
    reservations = sorted(ROOT.glob('attempt-*-reserved.json'))
    if len(reservations) >= 4 or not name.endswith(('.html', '.txt', '.htm')):
        raise ValueError('Bounded primary research packet is full or name unsupported')
    store = ScoutStore(DB)
    physical = datetime.now(timezone.utc)
    token = store.claim_provider('sec', as_of=physical, seconds=120)
    if token is None:
        raise RuntimeError('Existing SEC lease/cooldown blocks research; no request made')
    try:
        sequence = len(reservations)+1
        reservation = {'sequence':sequence,'url':url,'reserved_at':physical.isoformat(),
            'maximum_original_body_bytes':cap,'pre_request_cap_justification':justification,
            'user_agent':UA,'original_retained_sec_lease_used':True,'application_daily_quota_claim':False,
            'daily_history_reset_or_reconstructed':False,'redirects_or_retries_allowed':False}
        with (ROOT/f'attempt-{sequence:02d}-reserved.json').open('x',encoding='utf8',newline='\n') as out:
            out.write(json.dumps(reservation,indent=2,sort_keys=True)+'\n')
        pace=SecEdgarClient(user_agent=UA,max_attempts=1,min_request_interval_seconds=.125)
        pace._wait_for_rate_slot()
        response_clock=time.perf_counter()
        try:
            response=build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':UA,'Accept-Encoding':'identity'}),timeout=20)
        except HTTPError as error:
            response=error
        with response:
            chunks=[]; size=0
            while True:
                remaining=20-(time.perf_counter()-response_clock)
                if remaining<=0: raise TimeoutError('Primary SEC complete-body deadline exhausted')
                wire=response.fp if isinstance(response,HTTPError) else response
                sock=getattr(getattr(getattr(wire,'fp',None),'raw',None),'_sock',None)
                if sock is not None: sock.settimeout(remaining)
                chunk=getattr(response,'read1',response.read)(min(65536,cap+1-size))
                if not chunk: break
                size+=len(chunk)
                if size>cap: raise ValueError('Primary SEC complete body exceeds reserved cap')
                chunks.append(chunk)
            body=b''.join(chunks)
            length=response.headers.get('Content-Length','')
            if length.isdigit() and int(length)!=len(body): raise ValueError('Incomplete declared SEC body')
            result={**reservation,'observed_at':datetime.now(timezone.utc).isoformat(),'http_status':response.code,
              'content_type':response.headers.get('Content-Type'),'http_date':response.headers.get('Date'),
              'last_modified':response.headers.get('Last-Modified'),'sha256':hashlib.sha256(body).hexdigest(),'size_bytes':len(body)}
            encoded=response.headers.get('Content-Encoding','identity').lower() not in {'','identity'}
        sensitive=_has_sensitive_body(body)
        result['retention']='WITHHELD_SENSITIVE_BODY' if sensitive else ('WITHHELD_ENCODED_BODY' if encoded else 'ORIGINAL_BYTES')
        if not sensitive and not encoded:
            (ROOT/'primary').mkdir(exist_ok=True)
            with (ROOT/'primary'/name).open('xb') as out: out.write(body)
            result['path']='primary/'+name
        if result['http_status'] in {401,403,429}:
            store.defer_provider('sec',token,as_of=datetime.now(timezone.utc),retry_after=timedelta(hours=1 if result['http_status']==429 else 24),reason=f"SEC HTTP {result['http_status']}")
        with (ROOT/f'attempt-{sequence:02d}-result.json').open('x',encoding='utf8',newline='\n') as out:
            out.write(json.dumps(result,indent=2,sort_keys=True)+'\n')
        print(json.dumps({k:result[k] for k in ('sequence','http_status','size_bytes','sha256','retention')}))
    finally:
        store.release_provider('sec',token)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True); parser.add_argument('--name',required=True)
    parser.add_argument('--cap',type=int,default=4_000_000); parser.add_argument('--justification')
    args=parser.parse_args(); capture(args.url,args.name,args.cap,args.justification)
