"""Pin the unchanged peer regression and independently qualified coordinator fix."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
qualification=HERE/sys.argv[1]
tested=json.loads((qualification/'tests.json').read_bytes())
if (not tested['successful'] or tested['discovered']!=tested['executed'] or tested['executed']!=7
        or any(tested[name] for name in ('failures','errors','skips','expected_failures','unexpected_successes'))
        or not tested['source_unchanged_during_tests']):
    raise ValueError('The repaired source has no successful exact focused qualification')
original=json.loads((HERE/'original/source_pins.json').read_bytes())
test_pin=json.loads((HERE/'original/test_source_pin.json').read_bytes())
def pin(path):
    body=path.read_bytes();return {'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
current={name:pin(ROOT/name) for name in tested['source_sha256']}
if any(current[name]['sha256']!=digest for name,digest in tested['source_sha256'].items()):
    raise ValueError('Qualified source has changed since qualification')
if current[test_pin['path']]['sha256']!=test_pin['sha256']:
    raise ValueError('The original independent regression was changed')
production_changes=[name for name,source in original['source_files'].items() if current[name]['sha256']!=source['sha256']]
if production_changes!=['market_checker_app/services/pipeline_service.py']:
    raise ValueError('Unexpected shared-source change beyond the coordinator pipeline repair')
late=[item for item in tested['observations'] if item['scenario']=='save_progress_callback']
if len(late)!=6 or any(item['synthetic_in_memory_save_calls'] or not item['raised_WAIT_DATA'] for item in late):
    raise ValueError('A late raw-parent mutation still crosses the persistence boundary')
report={'schema_version':'batch9_market_parent_peer_handoff_v1','recorded_at':datetime.now(timezone.utc).isoformat(),
    'original_base':original['base'],'qualified_dependency_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    'coordinator_fix_commit':'00b912f7dafab841b5fe6ac0f368d7d8d09ad8be',
    'owned_paths':['tests/test_batch9_market_parent_peer.py','evidence/batch9_market_parent_peer_20261017/'],
    'independent_test_bytes_unchanged_from_failed_reproduction':True,'qualified_source_files':current,
    'coordinator_owned_production_changes':production_changes,'original_failed_qualification':'before_fix_v1/tests.json',
    'repaired_qualification':qualification.relative_to(HERE).as_posix()+'/tests.json',
    'focused_tests':{key:tested[key] for key in ('discovered','executed','failures','errors','skips','expected_failures','unexpected_successes','elapsed_seconds')},
    'late_mutation_cases':late,'actual_source_http_requests':0,'actual_database_initializations':0,
    'genuine_forecasts':0,'labels':0,'fixture_only':True,'publisher_revision_timing_status':'UNKNOWN',
    'strict_pit_eligible':False,'hosted_windows_acceptance_claimed':False,'full_specialist_acceptance':False,
    'prior38_acquisition_tests_repeated':False,'full_baseline_repeated':False,'shared_production_files_edited_by_this_lane':False,
    'github_writes':0,'score_or_fitter_or_budget_changes':False,
    'artifact_pins':{path.relative_to(HERE).as_posix():pin(path) for path in sorted(HERE.rglob('*')) if path.is_file() and path.name!='handoff.json'}}
with (HERE/'handoff.json').open('x',encoding='utf-8',newline='\n') as out:out.write(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps({'tests':report['focused_tests'],'handoff_sha256':pin(HERE/'handoff.json')['sha256'],'coordinator_production_changes':production_changes},sort_keys=True))
