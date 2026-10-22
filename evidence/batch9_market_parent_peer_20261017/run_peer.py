"""Versioned raw-parent persistence boundary qualification; fixture-only."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1];sys.path.insert(0,str(ROOT))
directory=HERE/sys.argv[1];directory.mkdir(exist_ok=False)
paths=('tests/test_batch9_market_parent_peer.py','market_checker_app/services/captured_market_data_provider.py',
    'market_checker_app/services/captured_market_input_acquisition.py','market_checker_app/services/pipeline_service.py',
    'market_checker_app/services/progress_service.py')
before={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths}
from tests import test_batch9_market_parent_peer as test_module
test_module.OBSERVATIONS.clear()
suite=unittest.defaultTestLoader.loadTestsFromModule(test_module);discovered=suite.countTestCases();started=time.perf_counter()
with (directory/'tests.log').open('x',encoding='utf-8',newline='\n') as out:
    result=unittest.TextTestRunner(stream=out,verbosity=2).run(suite)
report={'observed_at':datetime.now(timezone.utc).isoformat(),'fixture_only':True,'actual_source_http_requests':0,
    'actual_database_initializations':0,'genuine_forecasts':0,'labels':0,
    'discovered':discovered,'executed':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
    'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),
    'unexpected_successes':len(result.unexpectedSuccesses),'successful':result.wasSuccessful(),
    'elapsed_seconds':round(time.perf_counter()-started,6),'observations':test_module.OBSERVATIONS,
    'source_sha256':before,'source_unchanged_during_tests':before=={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths},
    'log_sha256':hashlib.sha256((directory/'tests.log').read_bytes()).hexdigest()}
with (directory/'tests.json').open('x',encoding='utf-8',newline='\n') as out:out.write(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps(report,sort_keys=True))
raise SystemExit(0 if result.wasSuccessful() else 1)
