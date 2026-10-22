"""Versioned focused fixture qualification; never runs collectors or forecasts."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
version=sys.argv[1]
directory=HERE/version;directory.mkdir(exist_ok=False)
files=('market_checker_app/services/captured_market_input_acquisition.py','tests/test_batch9_market_input_acquisition.py')
before={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_batch9_market_input_acquisition')
discovered=suite.countTestCases();started=time.perf_counter()
with (directory/'tests.log').open('x',encoding='utf-8',newline='\n') as out:
    result=unittest.TextTestRunner(stream=out,verbosity=2).run(suite)
report={'observed_at':datetime.now(timezone.utc).isoformat(),'fixture_only':True,'source_http_requests':0,
    'actual_db_accesses':0,'forecasts_created':0,'labels_created':0,'discovered':discovered,
    'executed':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
    'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),
    'unexpected_successes':len(result.unexpectedSuccesses),'elapsed_seconds':round(time.perf_counter()-started,6),
    'successful':result.wasSuccessful(),'source_sha256':before,
    'source_unchanged_during_tests':before=={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files},
    'log_sha256':hashlib.sha256((directory/'tests.log').read_bytes()).hexdigest()}
with (directory/'tests.json').open('x',encoding='utf-8',newline='\n') as out:out.write(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps(report,sort_keys=True))
raise SystemExit(0 if result.wasSuccessful() else 1)
