"""New pure-review focused suite only; no database-using recovery tests."""
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest


ROOT=Path(__file__).resolve().parents[2]
LANE=ROOT/'evidence/batch11_contracts_20261018'
MODULE='tests.test_usaspending_prime_scope_review'
PATHS=['market_checker_app/services/usaspending_prime_scope_review.py',
    'market_checker_app/services/usaspending_prime_archive_recovery.py',
    'market_checker_app/services/usaspending_scout_service.py',
    'market_checker_app/services/usaspending_request_budget.py',
    'tests/test_usaspending_prime_scope_review.py']


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromName(MODULE);discovered=suite.countTestCases()
    started=datetime.now(timezone.utc).isoformat();stream=io.StringIO();before=time.perf_counter()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'schema_version':1,'started_at':started,'completed_at':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':time.perf_counter()-before,'module':MODULE,'discovered':discovered,'executed':result.testsRun,
        'successful':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'source_and_test_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in PATHS},
        'scope':'NEW_READ_ONLY_IN_MEMORY_REVIEW_AND_RETAINED_INCOMPLETE_REPORT','source_http_requests':0,'db_operations':0,
        'quota_operations':0,'registry_adoptions':0,'full_baseline_repeated':False,'synthetic_controls_count_as_live':False,
        'historical_oos_or_user_windows_acceptance_claimed':False,
        'development_checks':[{'initial_new_methods':29,'successful':True,'qualification':'initial mechanisms before added retained-clock and byte/page-bound controls; final hashes qualify the current run only'}]}
    with (LANE/'tests.log').open('x',encoding='utf-8') as f:f.write(stream.getvalue())
    with (LANE/'tests.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({key:report[key] for key in ('discovered','executed','successful','failures','errors','skips','elapsed_seconds')}))
    raise SystemExit(not result.wasSuccessful())
