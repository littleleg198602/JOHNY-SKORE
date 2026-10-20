"""Save an exclusive current-source CLI/UI integration qualification."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCES = ('market_checker_app/services/captured_identity_review_service.py',
 'market_checker_app/specialist_evidence_runner.py', 'market_checker_app/specialist_evidence_ui.py',
 'tests/test_batch11_coordinator_integration.py')

def run(name, selected):
    if not name.replace('_','').isalnum(): raise ValueError('Select a fresh report name')
    log, report = OUT/(name+'.log'), OUT/(name+'.json')
    if log.exists() or report.exists(): raise ValueError('Never overwrite a prior qualification')
    pins = {s: {'bytes': (ROOT/s).stat().st_size, 'sha256': hashlib.sha256((ROOT/s).read_bytes()).hexdigest()} for s in SOURCES}
    suite = unittest.defaultTestLoader.loadTestsFromNames(selected)
    count = suite.countTestCases()
    start = datetime.now(timezone.utc); timer = time.perf_counter(); output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output), patch('socket.socket.connect', side_effect=AssertionError('No source HTTP')), patch('sqlite3.connect', side_effect=AssertionError('No integration SQL')):
        result = unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    raw = output.getvalue().encode('utf-8')
    with log.open('xb') as stream: stream.write(raw)
    data = {'started_at':start.isoformat(), 'ended_at':datetime.now(timezone.utc).isoformat(),
      'selected':selected,'discovered':count,'executed':result.testsRun,'failures':len(result.failures),
      'errors':len(result.errors),'skipped':len(result.skipped),'expected_failures':len(result.expectedFailures),
      'unexpected_successes':len(result.unexpectedSuccesses),'successful':result.wasSuccessful(),
      'elapsed_seconds':time.perf_counter()-timer,'console_sha256':hashlib.sha256(raw).hexdigest(),
      'source_pins':pins,'source_http_requests':0,'db_sql_operations':0,'full_specialist_acceptance':False}
    assert pins == {s: {'bytes':(ROOT/s).stat().st_size,'sha256':hashlib.sha256((ROOT/s).read_bytes()).hexdigest()} for s in SOURCES}
    with report.open('x',encoding='utf-8',newline='\n') as stream: stream.write(json.dumps(data,indent=2)+'\n')
    print(json.dumps({k:data[k] for k in ('discovered','executed','failures','errors','skipped','successful','elapsed_seconds')}))
    if not result.wasSuccessful(): raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--name',required=True);parser.add_argument('selected',nargs='+')
    args=parser.parse_args();run(args.name,args.selected)
