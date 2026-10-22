"""One explicit <=13 GET attempt; no forecasts, labels, DB or implicit audit."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from market_checker_app.services.captured_market_input_acquisition import CapturedMarketInputAcquisition

if (HERE/'actual_attempt_summary.json').exists():
    raise SystemExit('The original actual acquisition is preserved; do not repeat HTTP')
source=ROOT/'market_checker_app/services/captured_market_input_acquisition.py'
with (HERE/'actual_acquisition_producer_v1.py').open('xb') as out:
    out.write(source.read_bytes())
service=CapturedMarketInputAcquisition(archive_root=HERE/'actual_acquisition',enabled=True)
plan=service.plan()
report=service.acquire(plan,execute=True)
summary={'observed_at':datetime.now(timezone.utc).isoformat(),'actual_attempt':True,
    'producer_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'source_http_requests':report['source_http_requests'],'status':report['status'],
    'raw_captures':report['raw_captures'],'usable_count':report['usable_count'],
    'manifest_path':report['manifest_path'],'fixture_only':report['fixture_only'],
    'rows':[{'ticker':row['ticker'],'status':row['status'],'reason':row['reason'],
             'http_status':row.get('http_status'),'error_type':row.get('error_type')} for row in report['rows']],
    'report':report,'source_purchase_or_license_change':False,'forecasts_created':0,
    'labels_created':0,'db_writes':0,'strict_pit_eligible':False,
    'publisher_revision_timing_status':'UNKNOWN'}
with (HERE/'actual_attempt_summary.json').open('x',encoding='utf-8',newline='\n') as out:
    out.write(json.dumps(summary,sort_keys=True,indent=2)+'\n')
print(json.dumps({key:summary[key] for key in ('status','source_http_requests','raw_captures','usable_count','manifest_path','rows')},sort_keys=True))
