"""Persist an immutable pre-hosted proof from the exact completed new suite."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BASE='6ba4f44bc3e1ca240615c91dbec5bf52c93f3846'
JSON='evidence/batch11_linux_20261020.json'
CONSOLE='evidence/batch11_linux_console_20261020.log'
RUNNER='evidence/batch11_linux_runner_20261020.log'

def git(*args):return subprocess.check_output(['git','-C',str(ROOT),*args])
def pin(name):
 raw=(ROOT/name).read_bytes();return {'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(name,value):
 with (ROOT/name).open('x',encoding='utf-8',newline='\n') as stream:stream.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def main(code):
 assert git('rev-parse','HEAD').decode().strip()==code
 full=json.loads((ROOT/JSON).read_text());count=full['tests_run']
 assert full['successful'] is True and count==full['discovered_tests']
 assert all(full[k]==0 for k in ('failures','errors','skipped','expected_failures','unexpected_successes'))
 text=(ROOT/CONSOLE).read_bytes().decode('utf-8')
 footer=re.search(r'(?m)^Ran (\d+) tests in ([\d.]+)s\n\nOK\n?\Z',text)
 assert footer and int(footer[1])==count
 names=git('diff','--name-only','-z',BASE,code,'--','market_checker_app','tests').decode().split('\0')
 sources={}
 for name in filter(None,names):
  assert (ROOT/name).read_bytes()==git('show',code+':'+name),name
  sources[name]=pin(name)
 modules={Path(n).stem for n in sources if n.startswith('tests/test_') and n.endswith('.py')}
 starts=list(re.finditer(r'(?m)^(test_[^\r\n ]+) \(([^\r\n]+)\) \.\.\. ',text));assert len(starts)==count
 methods=[]
 for i,match in enumerate(starts):
  method,qualified=match.group(1,2);module,cls,method_id=qualified.rsplit('.',2);assert method==method_id
  if module not in modules:continue
  end=starts[i+1].start() if i+1<len(starts) else text.index('\n----------------------------------------------------------------------',match.start())
  block=text[match.start():end].strip();assert re.search(r'(?:\.\.\. |\r?\n)ok\Z',block),qualified
  methods.append({'module':module,'class':cls,'method':method,'original_ok_block_sha256':hashlib.sha256(block.encode()).hexdigest()})
 assert len(methods)==count-2476
 assert len({(m['module'],m['class'],m['method']) for m in methods})==len(methods)
 method_name='evidence/batch11_linux_methods_20261020.json'
 save(method_name,{'tested_code_snapshot':code,'full_discovered_and_executed':count,'new_exact_method_count':len(methods),
  'methods':methods,'console_pin':pin(CONSOLE),'strict_test_boundary_blocks_used':True,'original_log_rewritten':False})
 audit_name='evidence/batch11_coordinator_20261018/content_audit_final.json'
 subprocess.run(['/workspace/scratch/334b3a149083/johny-tests-20261011/bin/python',
  str(ROOT/'evidence/batch11_coordinator_20261018/audit_content.py'),'--output',str(ROOT/audit_name)],check=True)
 audit=json.loads((ROOT/audit_name).read_text())
 runtime={name:pin(name) for name in ('evidence/batch11_coordinator_20261018/runtime/source_capture.json','evidence/batch11_coordinator_20261018/runtime/native_capture.json')}
 workers={'identity_bulk':'7438744d353f8583b51c67bd3d85e9cd13230905','healthcare':'5a529b940899942e958a732bc12eaef06143e7ac',
  'regulatory':'01fa091b6a274eb7e1a63c98453d5121602b370f','contracts':'cc23d43adc44839a16d33464a7d8bc56a3700e8d',
  'sec':'a3bc814a5b07f5268a6fc16743968dcfe3e0fba4'}
 evaluation=json.loads((ROOT/'evidence/batch11_coordinator_20261018/worker_commits.json').read_text());workers.update(evaluation['remaining_worker_commits'])
 proof={'schema_version':'batch11_combined_pre_hosted_qualification_v1','physical_recorded_at':datetime.now(timezone.utc).isoformat(),
  'repository':'littleleg198602/JOHNY-SKORE','pull_request':162,'public_base_head':BASE,'public_base_tree':'a50b9fc6b315b7aae33e052a1b8e31680880566b',
  'main_restored':'ebb63a4b666908ce176e92ea7dffd6e74808caa9','actual_main_refresh':pin('evidence/batch11_coordinator_20261018/public_refresh_20261020.json'),'tested_code_snapshot':code,'combined_source_test_pins':sources,
  'actual_isolated_development_commits':workers,'actual_independent_peer_commits':evaluation['peer_commits'],
  'real_usage_interruption_preserved':True,'saved_completed_qualifications_not_repeated_on_resumption':True,
  'original_failures_and_historical_producers_preserved':True,
  'incomplete_prior_local_full':pin('evidence/batch11_interrupted_local_20261020/interruption.json'),
  'source_handoff_audit_completed_by_coordinator_after_real_agent_limit':True,
  'source_handoff_audit':pin('evidence/batch11_source_handoff_peer_20261019/content_verification.json'),
  'historical_document_recovery':pin('evidence/batch11_coordinator_20261018/historical_pin_resolution.json'),'local_full_suite':{**full,'plain_OK':True,
   'seconds_from_original_footer':float(footer[2]),'JSON':pin(JSON),'console':pin(CONSOLE),'runner':pin(RUNNER)},
  'new_exact_methods':pin(method_name),'content_audit':pin(audit_name),'actual_runtime_dispatch':runtime,
  'new_source_HTTP_search_collector_calls':0,'retained_database':audit['retained_database'],
  'original_public_baseline':audit['baseline_groups'],'protected_inputs':audit['protected_inputs'],
  'current_handoff_pins':len(audit['current_or_immutable_handoff_pins']),'historical_producer_pins':len(audit['preserved_historical_producer_pins']),
  'baseline_hosted_CI':{'qualified_head_only':BASE,'run_id':39137308800,'required_checks':'ALL_FIVE_SUCCESS_CONTENT_VERIFIED',
   'full_test_count':2476,'actual_windows_artifact_id':12140789098,'originals':'evidence/batch11_baseline_ci_20261018/'},
  'candidate_hosted_CI':'UNVERIFIED_NOT_YET_PUBLISHED','later_hosted_qualification_location':'PR162 plus actual exact-new-head GitHub artifact; do not rewrite this pre-CI proof',
  'default_modelconfig_hash':'e8e0a4b5e010122a7d7fa2f8d41eb9174e25a6a1a57e206cb315b1a552d0d5f2',
  'default_model_fitter_score_or_687_60s_budget_changed':False,'automatic_trading_allowed':False,'canonical_tickers':687,
  'historical_Q2_instrument_coverage':150,'current_Q3_instrument_coverage':0,'healthcare_identities':10,'NHTSA_model_identities':3,
  'legal_identities':5,'registry_adoptions':0,'all_21_code_live_statuses_unchanged':True,'genuine_forecasts_registrations_labels_created':0,
  'current_database_table_forecast_counts':None,'counts_status':'UNOBSERVED_ORIGINAL_JOURNAL_UNTOUCHED',
  'NHTSA_live_approval_blocked':True,'prior_failed_market_plan_consumed_no_retry':True,'whole_withheld_bodies_absent':4,
  'full_specialist_acceptance':'0/21','user_windows_end_to_end_verified':False,'historical_OOS_verified':False,
  'public_parent_only_exact_safe_tree_required':True,'merge_authorized':False}
 save('evidence/batch11_integration_20261019.json',proof)
 print(json.dumps({'tested_code_snapshot':code,'full_count':count,'new_exact_methods':len(methods),'proof':pin('evidence/batch11_integration_20261019.json')}))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--code',required=True);main(parser.parse_args().code)
