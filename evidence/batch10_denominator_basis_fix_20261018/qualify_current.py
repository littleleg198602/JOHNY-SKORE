"""Changed denominator plus unchanged concentration and independent peer checks."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT/'evidence/batch10_denominator_basis_fix_20261018'
MODULES = ['tests.test_sec_supplier_revenue_denominator_review','tests.test_batch9_supplier_concentration']
PATHS = ['market_checker_app/collectors/sec_supplier_revenue_denominator_review.py',
    'market_checker_app/services/sec_supplier_concentration_review.py',
    'market_checker_app/services/sec_operational_metrics_review.py',
    'market_checker_app/services/sec_purchase_obligation_scope_review.py',
    'market_checker_app/services/sec_supply_chain_scope_review.py',
    'market_checker_app/services/sec_ir_disclosure_authority_review.py',
    'market_checker_app/services/sec_person_kind_review.py',
    *(name.replace('.','/')+'.py' for name in MODULES)]


if __name__ == '__main__':
    started = datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    peer_file = LANE/'before_peer_test.py'
    assert hashlib.sha256(peer_file.read_bytes()).hexdigest() == '5767ad8c86fc42753de7cce90c7af39b1aa5b80bcb8d047b1129b12775cc8f1d'
    spec = importlib.util.spec_from_file_location('unchanged_independent_denominator_peer',peer_file)
    peer = importlib.util.module_from_spec(spec);spec.loader.exec_module(peer)
    peer_suite = unittest.defaultTestLoader.loadTestsFromModule(peer);peer_count = peer_suite.countTestCases()
    suite.addTests(peer_suite)
    discovered = suite.countTestCases();stream = io.StringIO();before = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report = {'schema_version':1,'started_at':started,'completed_at':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':time.perf_counter()-before,'discovered':discovered,'executed':result.testsRun,'successful':result.wasSuccessful(),
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'modules':MODULES,'changed_denominator_methods':unittest.defaultTestLoader.loadTestsFromName(MODULES[0]).countTestCases(),
        'unchanged_independent_peer_methods':peer_count,'independent_peer_sha256':hashlib.sha256(peer_file.read_bytes()).hexdigest(),
        'producer_source_and_test_sha256':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in PATHS},
        'scope':'OFFLINE_CURRENT_DENOMINATOR_WITH_UNCHANGED_INDEPENDENT_PEER_AND_DIRECT_DEPENDENCY',
        'source_http_requests':0,'production_db_or_quota_operations':0,'registry_adoptions':0,'baseline_full_suite_repeated':False,
        'synthetic_fixtures_count_as_live':False,'user_windows_or_historical_oos_verified':False}
    with (LANE/'current_tests.log').open('x',encoding='utf-8') as f:f.write(stream.getvalue())
    with (LANE/'current_tests.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({key:report[key] for key in ('discovered','executed','changed_denominator_methods','unchanged_independent_peer_methods','successful','failures','errors','elapsed_seconds')}))
    raise SystemExit(not result.wasSuccessful())
