"""Run only the independent peer regressions against an explicit source root."""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import time
import unittest


OWN_ROOT = Path(__file__).resolve().parents[2]
LANE = Path(__file__).resolve().parent


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--phase',choices=('prefixed','prefixed_confirmed','postfixed'),required=True)
    parser.add_argument('--require-pins',type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    if args.require_pins:
        expected = json.loads(args.require_pins.read_bytes())
        for row in expected['sources']:
            if hashlib.sha256((root/row['source_path']).read_bytes()).hexdigest()!=row['source_sha256']:
                raise ValueError('Source changed before peer reproduction')
    names = ('market_checker_app.services.pipeline_service','market_checker_app.weekly_shadow_runner',
        'market_checker_app.config','market_checker_app.release_manifest','market_checker_app.services.captured_market_data_provider')
    before = {name:hashlib.sha256((root/(name.replace('.','/')+'.py')).read_bytes()).hexdigest() for name in names}
    sys.path.insert(0,str(root))
    tests = importlib.import_module('tests'); tests.__path__ = [str(OWN_ROOT/'tests'), *list(tests.__path__)]
    module = importlib.import_module('tests.test_batch8_captured_runtime_peer')
    started = datetime.now(timezone.utc).isoformat();clock=time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromModule(module);discovered=suite.countTestCases()
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    actual = {name:{'path':str(Path(importlib.import_module(name).__file__).relative_to(root)),
                   'sha256':hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()} for name in names}
    if any(actual[name]['sha256']!=before[name] for name in names):
        raise ValueError('Source changed during peer reproduction')
    report={'schema_version':1,'phase':args.phase,'started_at':started,'completed_at':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':time.perf_counter()-clock,'discovered':discovered,'executed':result.testsRun,
        'successful':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'diagnostics':module.DIAGNOSTICS,'actual_imported_source_sha256':actual,
        'test_source_sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        'http_attempts':0,'production_database_reads_or_writes':0,'fixture_databases_temporary_and_handles_closed':True,
        'source_pins_sha256':hashlib.sha256(args.require_pins.read_bytes()).hexdigest() if args.require_pins else None}
    with (LANE/(args.phase+'_tests.log')).open('x',encoding='utf-8') as handle:handle.write(stream.getvalue())
    with (LANE/(args.phase+'_tests.json')).open('x',encoding='utf-8') as handle:
        json.dump(report,handle,sort_keys=True,indent=2);handle.write('\n')
    print(stream.getvalue())
    print(json.dumps({key:report[key] for key in ('phase','discovered','executed','successful','failures','errors','skips','diagnostics')},indent=2))
    raise SystemExit(not result.wasSuccessful())
