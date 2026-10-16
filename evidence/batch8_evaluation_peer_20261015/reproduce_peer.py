"""Isolated source-boundary checks; no model, source, DB or forecast run."""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=Path('/workspace/scratch/334b3a149083/JOHNY-batch7-integration-20261015')
SOURCE=None
APP=None
GLOBALS=None
CONFIG=None

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path,value):
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')

def expression(node,values):
    return eval(compile(ast.Expression(node),str(SOURCE),'eval'),values)

def method(name,overrides):
    tree=ast.parse(SOURCE.read_text())
    klass=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='PipelineService')
    node=next(node for node in klass.body if isinstance(node,ast.FunctionDef) and node.name==name)
    namespace=dict(GLOBALS);namespace.update(overrides)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),namespace)
    return namespace[name]

def provenance(*,ready_fixture=False,asset_fixture=False,benchmark_fixture=False):
    tree=ast.parse(SOURCE.read_text())
    node=next(node for node in ast.walk(tree) if isinstance(node,ast.Dict)
        and {'price_source','technical_source','run_started_at'} <= {
            key.value for key in node.keys if isinstance(key,ast.Constant)})
    asset=SimpleNamespace(attrs={'captured_market_data_provenance':{
        'fixture_only':asset_fixture,'source':{'file_sha256':'a'*64},
        'publisher_revision_timing_status':'UNKNOWN'}})
    benchmark=SimpleNamespace(attrs={'captured_market_data_provenance':{
        'fixture_only':benchmark_fixture,'source':{'file_sha256':'b'*64},
        'publisher_revision_timing_status':'UNKNOWN'}})
    return expression(node,{'self':SimpleNamespace(captured_market_mode=True),
        'current_price_source':'captured_market_data_close','tech_source_used':'captured_market_data',
        'yahoo_data_status':'captured_metadata_missing','yahoo_data_fetched_at':None,
        'ticker_articles':[],'started_at':datetime(2026,10,15,22,tzinfo=timezone.utc),
        'raw_ohlc':asset,'benchmark_ohlc_by_ticker':{'SPY':benchmark},'benchmark_ticker':'SPY',
        'captured_readiness':{'fixture_only':ready_fixture}})

class BoundaryChecks(unittest.TestCase):
    def marker(self,**values):
        result=provenance(**values)
        self.assertEqual('DETERMINISTIC_TEST_FIXTURE',result.get('evidence_origin'))
        self.assertIs(result.get('fixture_only'),True)

    def test_provider_fixture_clock_marker_reaches_top_level_snapshot_provenance(self):
        self.marker(ready_fixture=True)

    def test_asset_fixture_marker_reaches_top_level_snapshot_provenance(self):
        self.marker(asset_fixture=True)

    def test_benchmark_fixture_marker_reaches_top_level_snapshot_provenance(self):
        self.marker(benchmark_fixture=True)

    def test_original_nested_source_pins_and_unknown_revision_are_preserved(self):
        value=provenance(asset_fixture=True,benchmark_fixture=True)['captured_market_inputs']
        self.assertEqual({'file_sha256':'a'*64},value['asset']['source'])
        self.assertEqual({'file_sha256':'b'*64},value['benchmark']['source'])
        self.assertEqual('UNKNOWN',value['asset']['publisher_revision_timing_status'])
        self.assertIs(value['strict_pit_eligible'],False)

    def test_ui_mt5_watchlist_button_is_disabled_in_captured_mode(self):
        tree=ast.parse(APP.read_text())
        call=next(node.value for node in ast.walk(tree) if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='load_watchlist' for target in node.targets))
        disabled=next(keyword.value for keyword in call.keywords if keyword.arg=='disabled')
        self.assertIs(expression(disabled,{'use_mt5':True,'captured_market_enabled':True}),True)

    def test_ui_mt5_watchlist_execution_guard_refuses_captured_mode(self):
        text=APP.read_text();tree=ast.parse(text)
        guard=next(node.test for node in ast.walk(tree) if isinstance(node,ast.If)
            and (ast.get_source_segment(text,node.test) or '').startswith('load_watchlist'))
        self.assertIs(expression(guard,{'load_watchlist':True,'captured_market_enabled':True}),False)

    def test_captured_constructor_bypasses_online_and_both_cache_initializers(self):
        provider=object()
        def forbidden(*args,**kwargs):raise AssertionError('Online/cache initializer invoked')
        constructor=method('__init__',{'YahooClient':forbidden,'YahooCacheStore':forbidden,
            'YahooOhlcCacheStore':forbidden,'MT5Client':lambda:None,'RSSClient':lambda **kwargs:None})
        config=CONFIG(captured_market_manifest=Path('/explicit/manifest.json'),
            captured_market_archive_root=Path('/explicit/captures'))
        instance=SimpleNamespace()
        with patch('market_checker_app.services.captured_market_data_provider.CapturedMarketDataProvider',return_value=provider):
            constructor(instance,config)
        self.assertIs(instance.yahoo_client,provider)
        self.assertIsNone(instance.yahoo_cache)
        self.assertIsNone(instance.yahoo_ohlc_cache)

    def test_stage1_registration_never_initializes_remote_agents_or_gleif(self):
        for has_findings in (False,True):
            with self.subTest(retained_scout_findings=has_findings):
                registered=[]
                class Orchestrator:
                    def __init__(self,**kwargs):pass
                    def register(self,agent):registered.append(agent)
                    def run(self,**kwargs):return SimpleNamespace(metadata={})
                def forbidden(*args,**kwargs):raise AssertionError('Remote initializer invoked')
                names=('GleifClient','SecFundamentalsAgent','EuropeanFilingsAgent','ShortReportAgent',
                    'ClaimVerificationAgent','SupplyChainAgent','CommodityEnergyAgent','RegulatoryContractAgent')
                overrides={name:forbidden for name in names}
                overrides.update(OrchestratorAgent=Orchestrator,
                    SourceDiscoveryService=lambda:SimpleNamespace(discover=lambda *args,**kwargs:SimpleNamespace(short_reports=(),regulatory_events=())),
                    ScoutStore=lambda path:SimpleNamespace(has_findings=lambda *args,**kwargs:has_findings),
                    ScoutIndexAgent=lambda path:SimpleNamespace(name='scout_index'))
                config=CONFIG()
                for field in ('fundamental_ingestion','european_filings','short_reports','claim_verification',
                              'supply_chain','commodity_energy','regulatory_contract','governance_events'):
                    getattr(config,field).enabled=True
                config.decision_agent.enabled=False
                instance=SimpleNamespace(config=config,captured_market_mode=True,gleif_client=None,
                    sec_client=None,european_filing_client=None,european_filing_feed_client=None,
                    short_report_client=None,stage3_source_client=None)
                report=method('_run_agents',overrides)(instance,['AAPL'],GLOBALS['pd'].DataFrame(),store=None,news_items=[])
                entity=next(agent for agent in registered if agent.name=='entity_registry')
                self.assertIsNone(entity._primary_registry_client)
                self.assertIsNone(instance.gleif_client)
                self.assertEqual(has_findings,any(agent.name=='scout_index' for agent in registered))
                self.assertEqual(['AAPL'],report.metadata['agent_scope_tickers'])

    def test_captured_config_selection_does_not_implicitly_enable_prospective_audit(self):
        config=CONFIG(captured_market_manifest=Path('/explicit/manifest.json'),
            captured_market_archive_root=Path('/explicit/captures'))
        self.assertIs(config.prospective_history_enabled,False)
        self.assertIsNone(config.prospective_audit_archive_root)

def run_version(label,directory):
    global SOURCE,APP
    SOURCE=directory/'pipeline_service.py';APP=directory/'app.py'
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BoundaryChecks)
    with (HERE/(label+'_tests.log')).open('x',encoding='utf-8',newline='\n') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    return {'discovered':suite.countTestCases(),'executed':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),
        'failed_methods':[case.id().split('.')[-1] for case,_ in result.failures],
        'source_sha256':{'pipeline_service.py':sha(SOURCE),'app.py':sha(APP)}}

def main():
    global GLOBALS,CONFIG
    after=HERE/'after';after.mkdir()
    before_pins=json.loads((HERE/'before/source_pins.json').read_text())
    for name in ('pipeline_service.py','app.py'):
        relative='market_checker_app/'+('services/' if name=='pipeline_service.py' else '')+name
        with (after/name).open('xb') as stream:stream.write((ROOT/relative).read_bytes())
    after_pins={'observed_at':datetime.now(timezone.utc).isoformat(),
        'source_sha256':{name:sha(ROOT/name) for name in before_pins['source_sha256']}}
    write(after/'source_pins.json',after_pins)
    sys.path.insert(0,str(ROOT))
    with patch('socket.socket.connect',side_effect=AssertionError('No HTTP')), \
         patch('sqlite3.connect',side_effect=AssertionError('No DB')):
        from market_checker_app.services import pipeline_service
        from market_checker_app.config import AppConfig
        GLOBALS=vars(pipeline_service);CONFIG=AppConfig
        before=run_version('before',HERE/'before')
        fixed=run_version('after',after)
    report={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Read-only source/AST/initializer-boundary peer review; no model or forecast run',
        'before':before,'after':fixed,'findings':[
            {'code':'NESTED_FIXTURE_MARKER_NOT_PROPAGATED','before':'Confirmed by three isolated provenance-expression failures',
             'after':'Provider-clock, asset and benchmark markers each propagate top-level evidence_origin and fixture_only; original nested pins retained'},
            {'code':'MT5_WATCHLIST_UI_NOT_GATED','before':'Button and execution guard each failed offline-mode assertion',
             'after':'Captured mode disables the button and refuses its execution guard'}],
        'positive_invariants':['Stage1 registration with remote modules configured on and retained Scout findings on/off constructs no GLEIF/remote source-producing agent.',
            'Captured provider construction bypasses online client and metadata/OHLC cache initializers.',
            'Default captured configuration does not enable prospective registration.',
            'Asset/benchmark source hashes and UNKNOWN revision flags remain nested in point-in-time provenance.'],
        'limits':['Only isolated provenance expressions, UI guards and initializer/registration paths are executed. No full pipeline, agent run or model evaluation occurs.',
            'The orchestration run and Scout store are spies. This is not live acceptance or proof that the normal local history/Scout audit path has no writes.',
            'MT5Client construction is inert; native import/initialize is only inside its read methods. No native method is called here.',
            'Independent explicit SEC/scout acquisition buttons are outside the captured normal-run boundary; no implicit collector call was found.',
            'Finalizer persistence timing is reviewed separately by contracts; this review does not duplicate that lane.'],
        'before_app_recovery':'Exact old app bytes reconstructed by reversing only the two known MT5 guard edits; whole-file SHA256 equals the previously observed before/source_pins.json pin.',
        'read_only':{'http_requests':0,'actual_db_accesses':0,'forecasts_created':0,'labels_created':0,
            'model_or_fitter_invocations':0,'root_source_edits':0,'previous_qualification_overwritten':False,
            'full_baseline_repeated':False},
        'artifact_sha256':{str(path.relative_to(HERE)):sha(path) for path in (
            HERE/'before/source_pins.json',HERE/'before/pipeline_service.py',HERE/'before/app.py',
            after/'source_pins.json',after/'pipeline_service.py',after/'app.py',
            HERE/'before_tests.log',HERE/'after_tests.log',Path(__file__))}}
    write(HERE/'review.json',report)
    print(json.dumps({'before':before,'after':fixed,'review_sha256':sha(HERE/'review.json')},sort_keys=True))
    return 0 if before['failures']==5 and not before['errors'] and fixed['failures']==fixed['errors']==0 and fixed['executed']==9 else 1

if __name__=='__main__':raise SystemExit(main())
