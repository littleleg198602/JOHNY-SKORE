"""New physical review of the same immutable originals after the basis fix."""
import json
from pathlib import Path

from evidence.batch10_contracts_20261018.replay_retained import run


if __name__ == '__main__':
    report = run()
    lane = Path(__file__).resolve().parent
    with (lane/'current_actual_review.json').open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n')
    print(json.dumps({'status':report['status'],'reason':report['reason'],'physical_ingested_at':report['ingested_at'],
        'concentration_join_status':report.get('claim',{}).get('concentration_join_status'),'source_http_requests':0}))
