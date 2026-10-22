"""Add a truthful post-attempt no-retry claim; no collector/HTTP/DB calls.

Producer v1 preceded the exclusive-claim fix. Its whole original report and
producer stay unchanged. This marker does not claim it existed before the GET.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from market_checker_app.prediction_contract import canonical_hash
from market_checker_app.services.captured_market_input_acquisition import _encoded,_write_once

def main():
    summary=json.loads((HERE/'actual_attempt_summary.json').read_bytes())
    report=summary['report'];archive=HERE/'actual_acquisition'
    if (summary['source_http_requests']!=1 or summary['raw_captures']!=0 or summary['usable_count']!=0
            or hashlib.sha256((HERE/'actual_acquisition_producer_v1.py').read_bytes()).hexdigest()!=summary['producer_sha256']
            or report['acquisition_id']!=canonical_hash({'plan_id':report['plan_id'],'started_at':report['started_at']})):
        raise ValueError('Original actual attempt pins do not agree')
    report_path=report['archive_directory']+'/acquisition_report.json'
    if json.loads((archive/report_path).read_bytes())!=report:
        raise ValueError('Original immutable report changed')
    plan=json.loads((archive/report['archive_directory']/'plan.json').read_bytes())
    if plan['plan_id']!=report['plan_id'] or plan['plan_id']!=canonical_hash({k:v for k,v in plan.items() if k!='plan_id'}):
        raise ValueError('Original immutable plan changed')
    claim={'plan_id':report['plan_id'],'started_at':report['started_at'],
        'acquisition_id':report['acquisition_id'],'fixture_only':False,'report_path':report_path,
        'claim_recorded_at':datetime.now(timezone.utc).isoformat(),
        'claim_created_after_attempt':True,'http_requests_performed_by_this_recovery':0,
        'reason':'Protect original producer-v1 completed transport failure against any same-plan retry'}
    path=archive/'plan_claims'/('claim_'+report['plan_id']+'.json')
    _write_once(path,_encoded(claim),exclusive=True)
    print(json.dumps({'claim':str(path.relative_to(ROOT)),'claim_created_after_attempt':True,
        'source_http_requests':0,'original_report_preserved':True},sort_keys=True))

if __name__=='__main__':main()
