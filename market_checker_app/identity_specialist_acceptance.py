"""Portable identity-registry replay, coverage and persistence acceptance.

Runs on retained reviewed evidence. This does not test current provider access,
the user's broker, predictive returns, or complete worldwide group ownership.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import platform
from uuid import uuid4

from market_checker_app.agents import AgentContext,EntityRegistryAgent
from market_checker_app.agents.contracts import AgentExecution,OrchestrationReport
from market_checker_app.services.identity_holdout_service import evaluate_identity_holdout,DEFAULT_HOLDOUT,registry_digest
from market_checker_app.services.identity_scope_service import IdentityScope
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry,DEFAULT_COUNTERPARTY_IDENTITIES
from market_checker_app.services.healthcare_identity_service import HealthcareIdentityRegistry
from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.services.healthcare_coverage_service import SOURCE_PROFILES
from market_checker_app.storage.sqlite_store import SQLiteStore
from market_checker_app.services.sec13f_identity_bulk import atomic_json,guard_production_write
from market_checker_app.utils.ticker_universe import load_canonical_tickers


def build_report(*,as_of,database,holdout_path=DEFAULT_HOLDOUT):
    when=clock(as_of);generated=datetime.now(timezone.utc);tickers=load_canonical_tickers()
    scope=IdentityScope.load().report(tickers,as_of=when);agent=EntityRegistryAgent()
    run_id='identity-replay-'+uuid4().hex
    result=agent.run(AgentContext(run_id,tuple(tickers),when))
    included=[e for e in result.entities if e.ticker in scope['included']]
    verified=[e for e in included if e.cik and e.metadata.get('instrument_identity_verified')]
    registry=DatedCounterpartyIdentityRegistry.load();records=registry.records()
    known=[r for r in records if clock(r['known_at'])<=when]
    legal={r['issuer_cik'] for r in known if r['relationship_role']=='DIRECT_ISSUER'
           and r['effective_from']<=when.date().isoformat()
           and (not r.get('effective_to') or when.date().isoformat()<r['effective_to'])}
    healthcare=[r for r in HealthcareIdentityRegistry.load().records() if clock(r['known_at'])<=when]
    profiles=load_research_profiles();object_scope={}
    for source,applicable_profiles in SOURCE_PROFILES.items():
        applicable=[t for t in scope['included'] if profiles.for_ticker(t) and profiles.for_ticker(t).code in applicable_profiles]
        mapped=sorted({r['ticker'] for r in healthcare if r['source']==source and r['ticker'] in applicable})
        object_scope[source]={'applicable_issuers':len(applicable),'issuers_with_documented_objects':len(mapped),
            'mapped_tickers':mapped,'unmapped_tickers':[t for t in applicable if t not in mapped],
            'scope':'Documented objects only; not complete issuer portfolios or live source coverage'}
    database=Path(database);guard_production_write(database)
    if database.exists():raise ValueError('Use a new replay database; never overwrite history')
    execution=AgentExecution(agent.name,agent.version,True,(),generated,datetime.now(timezone.utc),0,len(tickers),result)
    replay=OrchestrationReport(run_id,when,datetime.now(timezone.utc),result.status,True,len(tickers),
        [execution],metadata={'identity_archived_replay':True,'generated_at':generated.isoformat()})
    store=SQLiteStore(database);store.save_orchestration_report(replay,pipeline_run_id=None)
    persisted=store.read_entities();versions=store.read_entity_identity_versions()
    heldout=evaluate_identity_holdout(holdout_path)
    history_path=Path(__file__).resolve().parent/'data/verified_13f_securities.json'
    history=json.loads(history_path.read_text(encoding='utf-8'));old_xom=[r for r in history if r['ticker']=='XOM']
    current_xom=next(e for e in included if e.ticker=='XOM')
    verified_windows=platform.system()=='Windows' and len(persisted)==len(tickers)
    return {'schema_version':1,'specialist':'identity','generated_at':generated.isoformat(),'as_of':when.isoformat(),
        'scope':'Archived issuer/class layer and explicitly documented legal-person/product relationships',
        'platform':platform.platform(),'system':platform.system(),'commit':os.getenv('GITHUB_SHA'),
        'canonical_tickers':len(tickers),'included_tickers':len(included),'excluded_tickers':scope['excluded'],
        'scope_sha256':scope['scope_sha256'],'verified_registered_classes':len(verified),
        'unresolved_included_tickers':[e.ticker for e in included if e not in verified],
        'registered_class_coverage_pct':100*len(verified)/len(included) if included else None,
        'independent_legal_issuers':len(legal),'subsidiary_observations':sum(r['relationship_role']=='SUBSIDIARY_OF_ISSUER' for r in known),
        'object_observations':dict(Counter(r['kind'] for r in healthcare)),'object_scope':object_scope,
        'registry_sha256':registry_digest(DEFAULT_COUNTERPARTY_IDENTITIES),'registry_hash_normalization':'UTF8_JSON_LF',
        'persistence':{'entities':len(persisted),'versions':len(versions),'database':str(database)},
        'historical_xom':{'preserved_Q2_ciks':[r['issuer_cik'] for r in old_xom],
            'current_cik':current_xom.cik,'historical_file_sha256':hashlib.sha256(history_path.read_bytes()).hexdigest(),
            'separate_issuers_preserved':bool(old_xom) and all(r['issuer_cik']!=current_xom.cik for r in old_xom)},
        'identity_holdout':heldout,'scoring_changed':False,
        'acceptance':{'included_issuer_class_layer_verified':len(verified)==len(included)==685,
            'archived_identity_persistence_verified':len(persisted)==len(versions)==len(tickers),
            'bounded_identity_holdout_verified':heldout['bounded_identity_holdout_verified'],
            'actual_windows_archived_identity_end_to_end_verified':verified_windows,
            'complete_subsidiary_brand_product_catalog_verified':False,
            'complete_specialist_verified':False}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of',help='Explicit replay knowledge cutoff; default: current UTC')
    parser.add_argument('--archived-cutoff',action='store_true',help='Use retained holdout capture clock, not today')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    args=parser.parse_args()
    if args.as_of and args.archived_cutoff:parser.error('Select one cutoff')
    if args.output.resolve()==args.database.resolve():parser.error('Separate JSON output from the replay database')
    if args.archived_cutoff:when=clock(json.loads(DEFAULT_HOLDOUT.read_text(encoding='utf-8'))['captured_at'])
    else:when=clock(args.as_of) if args.as_of else datetime.now(timezone.utc)
    report=build_report(as_of=when,database=args.database);atomic_json(args.output,report)
    checks=report['acceptance']
    passed=all(checks[k] for k in ['included_issuer_class_layer_verified','archived_identity_persistence_verified','bounded_identity_holdout_verified'])
    print(json.dumps({'status':'PASS' if passed else 'FAIL','platform':report['system'],
        'verified_classes':report['verified_registered_classes'],'excluded':list(report['excluded_tickers']),
        'complete_specialist_verified':False,'output':str(args.output)}))
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()
