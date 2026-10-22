"""Run only peer methods against an explicitly supplied worktree PYTHONPATH."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import unittest


LANE = Path(__file__).resolve().parent
ROOT = LANE.parents[1]
TEST = ROOT/'tests/test_batch10_role_claim_peer.py'


if __name__ == '__main__':
    phase = sys.argv[1]
    if phase not in {'before','after'}:raise SystemExit('before or after')
    spec = importlib.util.spec_from_file_location('independent_role_peer',TEST)
    peer = importlib.util.module_from_spec(spec);spec.loader.exec_module(peer)
    module = Path(peer.service.__file__)
    candidate = module.read_bytes()
    diagnostics = []
    for kind in ('OTHER_PERSON_INSIDE_CAREER_TABLE','HIDDEN_INDEX_FILING_DAY','COMPLETION_AFTER_EXPLICIT_CUTOFF'):
        try:
            r = peer.run_case(kind)
            diagnostics.append({'kind':kind,'rejected':False,'source_role_claims_supported':r['source_role_claims_supported'],
                'named_person':r.get('named_person_clause',{}).get('quote'),
                'roles':[{'person':x['person_name_literal'],'title':x['title_literal']} for x in r.get('role_claims',[])],
                'source_present_anchor':next((x['end_assertion'].get('anchor_filing_day') for x in r.get('role_claims',[]) if x.get('end_assertion',{} ) and x['end_assertion'].get('literal')=='present'),None),
                'requested_knowledge_at':r['requested_knowledge_at'],'review_available_at':r['review_available_at'],
                'natural_person_account_binding_verified':r['natural_person_account_binding_verified'],
                'continuous_role_verified':r['continuous_role_verified']})
        except ValueError as error:
            diagnostics.append({'kind':kind,'rejected':True,'error_type':type(error).__name__,'reason':str(error)})
    stream = io.StringIO();suite = unittest.defaultTestLoader.loadTestsFromTestCase(peer.RoleClaimPeerTests)
    discovered = suite.countTestCases();before = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report = {'schema_version':1,'phase':phase,'recorded_at':datetime.now(timezone.utc).isoformat(),
        'candidate_path':str(module),'candidate_sha256':hashlib.sha256(candidate).hexdigest(),'candidate_bytes':len(candidate),
        'peer_test_sha256':hashlib.sha256(TEST.read_bytes()).hexdigest(),'diagnostics':diagnostics,
        'discovered':discovered,'executed':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'successful':result.wasSuccessful(),'elapsed_seconds':time.perf_counter()-before,
        'synthetic_only':True,'source_http_requests':0,'db_operations':0,'registry_adoptions':0,'candidate_modified_by_peer':False}
    with (LANE/(phase+'_tests.log')).open('x',encoding='utf-8') as f:f.write(stream.getvalue())
    with (LANE/(phase+'_diagnostic.json')).open('x',encoding='utf-8') as f:json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({key:report[key] for key in ('phase','candidate_sha256','discovered','executed','failures','errors','successful')}))
    raise SystemExit(not result.wasSuccessful())
