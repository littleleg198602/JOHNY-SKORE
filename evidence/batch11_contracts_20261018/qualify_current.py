"""Preserved final console/review producer after adding cursor consistency."""
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest

from evidence.batch11_contracts_20261018.qualify import ROOT,LANE,MODULE,PATHS


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromName(MODULE);discovered=suite.countTestCases()
    stream=io.StringIO();start=datetime.now(timezone.utc).isoformat();before=time.perf_counter()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'schema_version':1,'started_at':start,'completed_at':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':time.perf_counter()-before,'module':MODULE,'discovered':discovered,'executed':result.testsRun,
        'successful':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'source_and_test_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in PATHS},
        'scope':'FINAL_OFFLINE_REVIEW_WITH_ADDED_LAST_RECORD_CURSOR_CONSISTENCY','previous34_case_proof_preserved':True,
        'source_http_requests':0,'db_operations':0,'quota_operations':0,'registry_adoptions':0,'full_baseline_repeated':False,
        'synthetic_controls_count_as_live':False,'historical_oos_or_user_windows_acceptance_claimed':False}
    with (LANE/'tests_current.log').open('x') as f:f.write(stream.getvalue())
    with (LANE/'tests_current.json').open('x') as f:json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({key:report[key] for key in ('discovered','executed','successful','failures','errors','elapsed_seconds')}))
    if not result.wasSuccessful():raise SystemExit(1)
    from evidence.batch11_contracts_20261018.replay_retained import run
    actual=run()
    with (LANE/'actual_current_review.json').open('x') as f:json.dump(actual,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({'actual_status':actual['status'],'knowledge_at':actual['knowledge_at'],'missing_original_artifacts':len(actual['missing_evidence'])}))
