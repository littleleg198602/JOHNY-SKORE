"""Verify exact public baseline bytes, current/historical pins and real offline outcomes."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BASE='6ba4f44bc3e1ca240615c91dbec5bf52c93f3846'
ALLOWED={'market_checker_app/data/specialist_status.json','market_checker_app/services/captured_identity_review_service.py',
 'market_checker_app/specialist_evidence_runner.py','market_checker_app/specialist_evidence_ui.py'}
WITHHELD={'84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3','deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1',
 '5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1','27f03d6bb63f0590a82369556419c4eb8f5fb9ab3b3da44a9c8695d18d7b7532'}
DB=ROOT.parent/'JOHNY-batch4-integration-20261013/outputs/market_checker_history.db'

def pin(path):
 raw=path.read_bytes();return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def git(*args):return subprocess.check_output(['git','-C',str(ROOT),*args])

def main(output):
 baseline=json.loads((ROOT/'evidence/batch11_coordinator_20261018/public_6ba_baseline_pins.json').read_text())
 assert baseline['public_head']==BASE
 groups={};changes=[];exceptions=[]
 for row in baseline['records']:
  actual=pin(ROOT/row['path']);expected={k:row[k] for k in ('bytes','sha256')}
  if row['original_checkout_CRLF']:
   assert actual==row['checkout_pin'];exceptions.append(row)
  elif actual!=expected:
   assert row['path'] in ALLOWED,row['path'];changes.append({'path':row['path'],'before':expected,'after':actual})
  group=groups.setdefault(row['group'],{'files':0,'git_blob_bytes':0,'unchanged_files':0})
  group['files']+=1;group['git_blob_bytes']+=row['bytes'];group['unchanged_files']+=int(actual==expected or row['original_checkout_CRLF'])
 assert {row['path'] for row in changes}==ALLOWED
 protected=json.loads((ROOT/'evidence/batch9_coordinator_20261016/protected_inputs_baseline.json').read_text())
 for name,p in protected.items():assert pin(ROOT/name)=={k:p[k] for k in ('bytes','sha256')},name
 names=git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0')
 known={name:pin(ROOT/name) for name in names if name and (ROOT/name).is_file()}
 assert not WITHHELD.intersection(p['sha256'] for p in known.values())
 frozen={}
 for name,p in known.items():
  if name.startswith('evidence/batch11_'):frozen.setdefault((p['sha256'],p['bytes']),[]).append(name)
 verified=[];historical=[]
 def check(name,expected,origin):
  p={k:expected[k] for k in ('bytes','sha256')}
  candidates=[name,(Path(origin).parent/name).as_posix()]
  for candidate in candidates:
   if known.get(candidate)==p:
    verified.append({'handoff':origin,'path':candidate,**p});return
  if name.startswith(('market_checker_app/','tests/')):
   copies=frozen.get((p['sha256'],p['bytes']),[]);assert copies,(origin,name,'HISTORICAL_PRODUCER_BYTES_ABSENT')
   historical.append({'handoff':origin,'original_producer':name,'scope':'HISTORICAL_VERSION_ONLY','whole_copies':copies,**p});return
  raise AssertionError((origin,name,'PIN_BYTES_MISSING_OR_CHANGED'))
 def walk(value,origin):
  if isinstance(value,list):
   for item in value:walk(item,origin)
  elif isinstance(value,dict):
   if isinstance(value.get('path'),str) and isinstance(value.get('sha256'),str) and type(value.get('bytes')) is int:
    name=value['path']
    if name.startswith(('evidence/','market_checker_app/','tests/')) or (ROOT/Path(origin).parent/name).is_file():check(name,value,origin)
   for key,item in value.items():
    if isinstance(item,dict) and isinstance(item.get('sha256'),str) and type(item.get('bytes')) is int and isinstance(key,str) and '/' in key:
     if key.startswith(('evidence/','market_checker_app/','tests/')) or (ROOT/Path(origin).parent/key).is_file():check(key,item,origin)
    walk(item,origin)
 handoffs=sorted(p for p in ROOT.glob('evidence/batch11_*/**/handoff*.json') if 'baseline_ci' not in str(p))
 for path in handoffs:walk(json.loads(path.read_text()),path.relative_to(ROOT).as_posix())
 status=json.loads((ROOT/'market_checker_app/data/specialist_status.json').read_text());old=json.loads(git('show',BASE+':market_checker_app/data/specialist_status.json'))
 assert len(status['specialists'])==21
 assert [(s['id'],s['code'],s['live']) for s in status['specialists']]==[(s['id'],s['code'],s['live']) for s in old['specialists']]
 runtime={}
 for phase,expected_count in [('source',10),('native',2)]:
  name='evidence/batch11_coordinator_20261018/runtime/'+phase+'_capture.json';r=json.loads((ROOT/name).read_text())
  assert len(r['actions'])==expected_count and all(v==0 for v in r['counters'].values())
  assert r['SQL_opened'] is False and r['journal_replayed_or_removed'] is False
  assert r['genuine_forecasts_registrations_labels_created']==0 and r['full_specialist_acceptance']=='0/21'
  for suffix,p in r['retained_database_before_after'].items():
   db=Path(str(DB)+suffix)
   assert (pin(db) if db.is_file() else None)==p
  runtime[phase]={'pin':pin(ROOT/name),'actions':expected_count,'counters':r['counters']}
 before=pin(DB);assert before['sha256']=='6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af'
 assert not git('diff',BASE,'--','.github/workflows')
 record={'schema_version':'batch11_content_audit_v1','physical_recorded_at':datetime.now(timezone.utc).isoformat(),
  'public_base':BASE,'HEAD':git('rev-parse','HEAD').decode().strip(),'baseline_groups':groups,'approved_existing_changes':changes,
  'original_checkout_CRLF_exceptions':exceptions,'protected_inputs':{'files':len(protected),'all_byte_identical':True},
  'current_or_immutable_handoff_pins':verified,'preserved_historical_producer_pins':historical,'audited_handoffs':[p.relative_to(ROOT).as_posix() for p in handoffs],
  'runtime':runtime,'all_21_code_live_unchanged':True,'source_HTTP_search_collector_calls':0,'genuine_forecasts_registrations_labels_created':0,
  'synthetic_fixture_DB_runs_are_genuine_forecasts':False,'retained_database':{**before,'SQL_opened':False,'table_and_forecast_counts':None,'counts_status':'UNOBSERVED'},
  'whole_withheld_bodies_present':0,'registry_adoptions':0,'default_modelconfig_hash':'e8e0a4b5e010122a7d7fa2f8d41eb9174e25a6a1a57e206cb315b1a552d0d5f2',
  'workflow_or_687_60s_budget_changed':False,'full_specialist_acceptance':'0/21','user_windows_end_to_end_verified':False,'historical_OOS_verified':False}
 raw=(json.dumps(record,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
 with output.open('xb') as stream:stream.write(raw)
 print(json.dumps({'status':'CONTENT_VERIFIED','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'current_pins':len(verified),'historical_pins':len(historical),'baseline_groups':groups}))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);main(parser.parse_args().output)
