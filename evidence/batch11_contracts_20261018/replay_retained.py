"""Read exact retained report bytes; never manufacture absent prime pages."""
import hashlib
import json
from pathlib import Path

from market_checker_app.services.captured_identity_review_service import retained_file
from market_checker_app.services.usaspending_prime_scope_review import review_usaspending_prime_scope


ROOT=Path(__file__).resolve().parents[2]
LANE=ROOT/'evidence/batch11_contracts_20261018'


def read_reference(ref):
    path=retained_file(ROOT,ref['path'])
    with path.open('rb') as f:body=f.read(4_000_001)
    if len(body)>4_000_000 or len(body)!=ref['bytes'] or hashlib.sha256(body).hexdigest()!=ref['sha256']:
        raise ValueError('Retained whole report pin changed')
    return body


def load_inputs():
    plan=json.loads((LANE/'actual_review_plan.json').read_bytes())
    return plan['request'],{key:(item['metadata'],read_reference(item['body_ref'])) for key,item in plan['captures'].items()}


def run():
    request,captures=load_inputs()
    return review_usaspending_prime_scope(request,captures)


if __name__=='__main__':
    report=run()
    with (LANE/'actual_review.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n')
    print(json.dumps({'status':report['status'],'reason':report['reason'],'knowledge_at':report['knowledge_at'],
        'missing_evidence':len(report['missing_evidence']),'source_http_requests':report['source_http_requests']}))
