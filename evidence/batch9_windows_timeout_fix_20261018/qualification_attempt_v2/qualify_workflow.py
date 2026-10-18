"""Exact workflow-only timeout qualification; no suite/HTTP/database execution."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import yaml


HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BASE='b0e866de120c8d6366b93c8fef26916f0db4aa0e'
WORKFLOW='.github/workflows/market-checker-test-agents.yml'
SOURCE_ROOT=Path(sys.argv[1]).resolve()
SOURCE=SOURCE_ROOT/'evidence/batch9_windows_cancelled_20261017'


class WorkflowLoader(yaml.SafeLoader):
    pass


# GitHub's YAML keys include `on`; YAML1.1 must not turn it into True.
WorkflowLoader.yaml_implicit_resolvers=deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for key,values in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[key]=[(tag,pattern) for tag,pattern in values
        if tag!='tag:yaml.org,2002:bool']
WorkflowLoader.add_implicit_resolver('tag:yaml.org,2002:bool',
    re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'),list('tTfF'))


def mapping(loader,node,deep=False):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if key in result:raise ValueError('Duplicate workflow mapping key')
        result[key]=loader.construct_object(value_node,deep=deep)
    return result


WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)


def sha(body):return hashlib.sha256(body).hexdigest()
def pin(path):
    body=path.read_bytes();return {'bytes':len(body),'sha256':sha(body)}
def write_json(path,record):
    with path.open('x',encoding='utf-8',newline='\n') as out:
        out.write(json.dumps(record,sort_keys=True,indent=2)+'\n')


def differences(before,after,path=()):
    if type(before) is not type(after):return [{'path':list(path),'before':before,'after':after}]
    if isinstance(before,dict):
        if before.keys()!=after.keys():raise ValueError('Workflow mapping structure changed')
        return [item for key in before for item in differences(before[key],after[key],(*path,key))]
    if isinstance(before,list):
        if len(before)!=len(after):raise ValueError('Workflow sequence length changed')
        return [item for index,(a,b) in enumerate(zip(before,after)) for item in differences(a,b,(*path,index))]
    return [] if before==after else [{'path':list(path),'before':before,'after':after}]


def main():
    before_bytes=subprocess.check_output(['git','show',BASE+':'+WORKFLOW],cwd=ROOT)
    after_bytes=(ROOT/WORKFLOW).read_bytes()
    if (HERE/'original_market-checker-test-agents.yml').read_bytes()!=before_bytes:
        raise ValueError('Original workflow copy differs from public Git blob')
    before=yaml.load(before_bytes,Loader=WorkflowLoader)
    after=yaml.load(after_bytes,Loader=WorkflowLoader)
    if 'on' not in before or 'on' not in after:raise ValueError('Workflow event key was not parsed faithfully')
    delta=differences(before,after)
    expected=[{'path':['jobs','windows-agent','timeout-minutes'],'before':40,'after':60}]
    if delta!=expected:raise ValueError('Unexpected workflow semantic change')
    expected_after=deepcopy(before);expected_after['jobs']['windows-agent']['timeout-minutes']=60
    if after!=expected_after:raise ValueError('Workflow semantics differ beyond Windows timeout')
    windows=after['jobs']['windows-agent']
    if windows['steps']!=before['jobs']['windows-agent']['steps']:raise ValueError('Windows steps changed')
    if windows['runs-on']!='windows-latest':raise ValueError('Windows platform changed')
    upload=next(step for step in windows['steps'] if step.get('name')=='Preserve Windows verification log')
    if upload['if']!='always()' or upload['with']['if-no-files-found']!='error':raise ValueError('Artifact retention gate changed')
    if after['jobs']['release-agent']!=before['jobs']['release-agent']:raise ValueError('Release gate changed')

    tree=subprocess.check_output(['git','ls-tree','-r','-z',BASE],cwd=ROOT)
    pins={};changed=[];counts=Counter();sizes=Counter()
    for entry in tree.split(b'\0'):
        if not entry:continue
        prefix,encoded_name=entry.split(b'\t',1);mode,kind,oid=prefix.decode().split()
        name=encoded_name.decode();path=ROOT/name
        if kind!='blob':raise ValueError('Unexpected non-file Git tree entry')
        if mode=='120000':body=path.readlink().as_posix().encode()
        else:
            if path.is_symlink():raise ValueError('Unexpected working-file symlink')
            body=path.read_bytes()
        actual_oid=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()
        equal=actual_oid==oid
        if not equal:changed.append(name)
        category=('production' if name.startswith('market_checker_app/') else
                  'tests' if name.startswith('tests/') else 'other_tracked_sources')
        counts[category]+=1;sizes[category]+=len(body)
        pins[name]={'bytes':len(body),'sha256':sha(body),'base_git_blob':oid,
            'current_git_blob':actual_oid,'byte_identical_to_public_base':equal,'category':category}
    if changed!=[WORKFLOW]:raise ValueError('Unexpected changes to tracked source/test/evidence bytes')
    budget_test='tests/test_687_ticker_contract.py'
    if (not pins[budget_test]['byte_identical_to_public_base']
            or b'self.assertLess(elapsed, 60.0)' not in (ROOT/budget_test).read_bytes()):
        raise ValueError('Canonical687 60-second budget changed')
    source_manifest={'public_base':BASE,'files':pins,'changed_tracked_paths':changed,
        'category_counts':dict(counts),'category_bytes':dict(sizes)}
    write_json(HERE/'source_files_sha256.json',source_manifest)

    content=json.loads((SOURCE/'content_verification.json').read_bytes())
    metadata=json.loads((SOURCE/'github_run_jobs_artifacts.json').read_bytes())
    expected_ci={
        'artifact_12117165429.zip':(58491,'7f0a915a75c3fa3a128e030a6923f535d7cea4e9a6817caba1de63a1a563edf9'),
        'windows-tests.log':(394171,'6463c4d1bb84c6d4bb763f47d1e58923301216445c82dd12877bbbf4755ec7a7'),
        'hosted_windows_job_decoded.log':(511607,'d1b28e62d8d5c21fd5e87a173e69d8a58522194db1720dc4f047fa0391d230b5')}
    ci_pins={name:pin(SOURCE/name) for name in (*expected_ci,'content_verification.json','github_run_jobs_artifacts.json')}
    for name,(length,digest) in expected_ci.items():
        if ci_pins[name]!={'bytes':length,'sha256':digest}:raise ValueError('Original CI artifact pin changed')
    with zipfile.ZipFile(SOURCE/'artifact_12117165429.zip') as archive:
        if archive.namelist()!=['windows-tests.log']:raise ValueError('Unexpected original Windows ZIP member')
        if archive.read('windows-tests.log')!=(SOURCE/'windows-tests.log').read_bytes():raise ValueError('Original Windows log differs from ZIP')
    if content['complete_full_suite_verified'] or not content['original_windows_report_JSON_absent']:
        raise ValueError('Original incomplete result was promoted')
    if (content['started_test_method_headings'],content['complete_original_ok_blocks'],content['batch9_new_methods_original_ok'])!=(1945,1944,194):
        raise ValueError('Original Windows partial execution counts changed')
    workflow_run=metadata['workflow_run']
    jobs={job['id']:job for job in metadata['jobs']}
    if (workflow_run['head_sha']!=BASE or workflow_run['id']!=39072646508
            or jobs[117513252470]['conclusion']!='cancelled' or jobs[117521376998]['conclusion']!='failure'):
        raise ValueError('Original cancelled Windows/release-gate metadata changed')
    lines=(SOURCE/'hosted_windows_job_decoded.log').read_text().splitlines()
    timestamps=[line.split(' ',1)[0] for line in lines if re.match(r'^\d{4}-\d\d-\d\dT',line)]
    cancelled=next(line for line in lines if '##[error]The operation was canceled.' in line)
    interrupted=next(line for line in lines if line.endswith('KeyboardInterrupt'))
    annotations=[line for line in lines if re.search(r'##\[(?:error|warning)\]',line)]
    timeout_annotations=[line for line in annotations if re.search(r'\b(?:timed out|timeout|time limit|exceeded.*minutes)\b',line,re.I)]
    def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
    elapsed=(stamp(cancelled.split(' ',1)[0])-stamp(timestamps[0])).total_seconds()
    report={'schema_version':'batch9_windows_timeout_only_qualification_v1',
        'recorded_at':datetime.now(timezone.utc).isoformat(),'public_base':BASE,
        'change':{'path':WORKFLOW,'Windows_job_timeout_minutes_before':40,'Windows_job_timeout_minutes_after':60},
        'yaml_parser':{'library':'PyYAML','version':yaml.__version__,'event_key_on_preserved':True,'duplicate_keys_refused':True},
        'semantic_differences':delta,'only_Windows_timeout_semantics_changed':True,
        'windows_steps_platform_Python_compile_checkpoint_full_suite_unchanged':True,
        'always_upload_behavior_unchanged':True,'release_gate_semantics_unchanged':True,
        'all_other_jobs_and_triggers_and_permissions_unchanged':True,
        'canonical687_60second_test_budget_unchanged':True,
        'workflow_before':{'bytes':len(before_bytes),'sha256':sha(before_bytes)},
        'workflow_after':{'bytes':len(after_bytes),'sha256':sha(after_bytes)},
        'tracked_files_hashed':len(pins),'unchanged_tracked_files':len(pins)-1,
        'source_category_counts':dict(counts),'source_category_bytes':dict(sizes),
        'source_hash_manifest':{'path':'source_files_sha256.json',**pin(HERE/'source_files_sha256.json')},
        'original_ci':{'run_id':39072646508,'head_sha':BASE,'windows_job_id':117513252470,
            'windows_conclusion':'cancelled','release_gate_id':117521376998,'release_gate_conclusion':'failure',
            'source_artifact_pins':ci_pins,'source_paths_relative_to_repository':'evidence/batch9_windows_cancelled_20261017/',
            'run_created_at':workflow_run['created_at'],'run_started_at':workflow_run['run_started_at'],
            'run_updated_at':workflow_run['updated_at'],'hosted_log_first_timestamp':timestamps[0],
            'hosted_log_last_timestamp':timestamps[-1],'KeyboardInterrupt_line':interrupted,
            'cancellation_annotation_line':cancelled,'seconds_from_first_hosted_timestamp_to_cancellation':elapsed,
            'explicit_timeout_annotations_in_available_hosted_log':timeout_annotations,
            'timeout_cause_status':'DURATION_BASED_INFERENCE_WITHOUT_EXPLICIT_TIMEOUT_ANNOTATION',
            'started_method_headings':1945,'complete_original_ok_blocks':1944,'new_batch9_methods_original_ok':194,
            'full_suite_complete':False,'original_result_JSON_absent':True,'plain_OK_footer_absent':True,
            'zero_final_failure_error_counts_claimed':False,'incomplete_test_heading':content['incomplete_heading']},
        'qualification_only':True,'new_head_Windows_verification_required':True,
        'unchanged2231_local_suite_repeated':False,'tests_filtered_or_skipped':False,
        'production_test_source_or_data_or_score_or_fitter_or_config_changed':False,
        'actual_HTTP_requests':0,'actual_DB_accesses':0,'quota_writes':0,'GitHub_writes':0,
        'new_windows_result_JSON_created':False,'complete_Windows_or_specialist_acceptance_claimed':False,
        'qualification_script':pin(Path(__file__)),
        'material_limits':['Only workflow YAML/static source preservation was qualified; no new-head hosted Windows run was executed by this lane.',
            'The cancelled original run has no complete result JSON or footer and remains non-acceptance evidence.',
            'The timeout cause is inferred from elapsed duration; the available log contains no explicit timeout annotation.']}
    if timeout_annotations:raise ValueError('Review required: explicit timeout annotation was found')
    write_json(HERE/'qualification.json',report)
    print(json.dumps({'semantic_differences':delta,'unchanged_tracked_files':len(pins)-1,
        'category_counts':dict(counts),'seconds_to_cancellation':elapsed,
        'new_head_Windows_verification_required':True,'qualification_sha256':pin(HERE/'qualification.json')['sha256']},sort_keys=True))


if __name__=='__main__':main()
