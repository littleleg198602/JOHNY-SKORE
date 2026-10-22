"""Focused fixture qualification and original-method/assertion preservation."""
import ast
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
TEST='tests/test_batch8_captured_market_data_provider.py'
TARGET='test_explicit_paths_refuse_escape_missing_and_parent_symlink_without_discovery'
BASE='df6648b4cd4d856629eed5cd5db1321e53da3bed'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def encoded(node):return ast.dump(node,include_attributes=False)
def methods(tree):
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='CapturedMarketDataProviderTests')
    return {node.name:node for node in cls.body if isinstance(node,ast.FunctionDef)}
def assertions(node):
    return Counter(encoded(item) for item in ast.walk(node) if isinstance(item,ast.Assert)
        or isinstance(item,ast.Call) and isinstance(item.func,ast.Attribute) and item.func.attr.startswith('assert'))

def audit():
    old_tree=ast.parse((HERE/'original/test_module.py').read_bytes())
    new_tree=ast.parse((ROOT/TEST).read_bytes())
    old,new=methods(old_tree),methods(new_tree)
    original_tests=[name for name in old if name.startswith('test_')]
    removed=[name for name in original_tests if name not in new]
    changed=[name for name in old if name in new and encoded(old[name])!=encoded(new[name])]
    missing_assertions={name:sum((assertions(old[name])-assertions(new[name])).values())
        for name in old if name in new and assertions(old[name])-assertions(new[name])}
    helpers={}
    for name in ('fixture_frame','capture','write_manifest'):
        before=next(node for node in old_tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
        after=next(node for node in new_tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
        helpers[name]=encoded(before)==encoded(after)
    setup=new['setUp'].body
    canonical_index=next(index for index,node in enumerate(setup) if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Attribute) and target.attr=='root' for target in node.targets)
        and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='resolve')
    capture_index=next(index for index,node in enumerate(setup) if isinstance(node,ast.Assign)
        and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='capture')
    guard_assert=any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
        and node.func.attr=='assertTrue' and node.args and isinstance(node.args[0],ast.Name)
        and node.args[0].id=='guard_hits' for node in ast.walk(new[TARGET]))
    result={'original_test_method_count':len(original_tests),'current_test_method_count':sum(name.startswith('test_') for name in new),
        'removed_original_test_methods':removed,'original_class_methods_with_ast_changes':changed,
        'unchanged_original_test_method_asts':sum(encoded(old[name])==encoded(new[name]) for name in original_tests),
        'missing_original_assertions_by_method':missing_assertions,
        'original_assertion_call_count':sum(sum(assertions(node).values()) for node in old.values()),
        'all_original_assertions_preserved':not missing_assertions,
        'original_data_capture_manifest_helpers_ast_unchanged':helpers,
        'canonicalization_precedes_first_capture':canonical_index<capture_index,
        'original_symlink_test_requires_nonempty_mock_guard_hits':guard_assert,
        'new_alias_regression_methods':[name for name in new if name.startswith('test_') and name not in old],
        'all_original_methods_retained':not removed,
        'only_allowed_original_ast_changes':set(changed)=={'setUp',TARGET}}
    return result

def main():
    for name in ('focused_tests.log','handoff.json'):
        if (HERE/name).exists():raise FileExistsError('Existing qualification is preserved')
    original_pins=json.loads((HERE/'original/source_pins.json').read_text())
    source_paths=[*original_pins['source_files'],str((HERE/'qualify_repair.py').relative_to(ROOT)),
        str((HERE/'reproduce_original_alias.py').relative_to(ROOT))]
    before={name:sha(ROOT/name) for name in source_paths}
    preservation=audit()
    with patch('socket.socket.connect',side_effect=AssertionError('Strictly no HTTP')), \
         patch('sqlite3.connect',side_effect=AssertionError('Strictly no DB')):
        suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_batch8_captured_market_data_provider')
        discovered=suite.countTestCases();started=time.perf_counter()
        with (HERE/'focused_tests.log').open('x',encoding='utf-8',newline='\n') as log:
            tested=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
        elapsed=time.perf_counter()-started
    after={name:sha(ROOT/name) for name in source_paths}
    unchanged={name:sha(ROOT/name)==pin['sha256'] for name,pin in original_pins['source_files'].items() if name!=TEST}
    changed=subprocess.check_output(['git','diff','--name-only',BASE],cwd=ROOT,text=True).splitlines()
    old=json.loads((HERE/'before_repair/original_alias_failure.json').read_text())
    report={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'base':BASE,
        'scope':'Windows temp-path fixture canonicalization only; production provider and guard unchanged',
        'hosted_windows_failure_reference':'38861160925','actual_windows_execution_performed':False,
        'local_platform':platform.platform(),'python_version':platform.python_version(),
        'before_repair_reproduction':old,
        'repair':'Resolve TemporaryDirectory root before capture/path construction; assert that the original symlink mock actually hit; add one real root/../root alias regression.',
        'ast_preservation':preservation,'production_provider_and_guard_byte_identical':unchanged,
        'tracked_changed_paths':changed,'tracked_ownership_respected':set(changed)=={TEST},
        'tests':{'discovered':discovered,'executed':tested.testsRun,'failures':len(tested.failures),'errors':len(tested.errors),
            'skips':len(tested.skipped),'expected_failures':len(tested.expectedFailures),'unexpected_successes':len(tested.unexpectedSuccesses),
            'elapsed_seconds':round(elapsed,6),'successful':tested.wasSuccessful(),
            'alias_regression_also_executes_original_symlink_case_and_asserts_its_success':True},
        'source_sha256':after,'qualified_files_unchanged_during_tests':before==after,
        'artifact_sha256':{name:sha(HERE/name) for name in (
            'original/provider.py','original/test_module.py','original/guard.py','original/source_pins.json',
            'before_repair/original_alias_failure.log','before_repair/original_alias_failure.json',
            'focused_tests.log','reproduce_original_alias.py','qualify_repair.py')},
        'claims':{'http_requests':0,'actual_db_accesses':0,'quota_writes':0,'new_forecasts_or_labels':0,
            'production_source_changes':False,'source_fixture_values_or_clocks_changed':False,
            'original_assertions_relaxed_or_removed':False,'skips_or_cleanup_workarounds_added':False,
            'body_normalization_or_synthetic_source_adoption':False,'full2036_suite_repeated':False,
            'hosted_windows_acceptance_claimed':False,'github_write_performed':False}}
    with (HERE/'handoff.json').open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(report,sort_keys=True,indent=2)+'\n')
    okay=(tested.wasSuccessful() and discovered==tested.testsRun==36 and before==after
        and all(unchanged.values()) and report['tracked_ownership_respected']
        and preservation['original_test_method_count']==35 and not preservation['removed_original_test_methods']
        and preservation['all_original_assertions_preserved'] and preservation['only_allowed_original_ast_changes']
        and preservation['canonicalization_precedes_first_capture'] and preservation['original_symlink_test_requires_nonempty_mock_guard_hits']
        and all(preservation['original_data_capture_manifest_helpers_ast_unchanged'].values()))
    print(json.dumps({'tests':report['tests'],'ast_preservation':preservation,
        'production_unchanged':unchanged,'handoff_sha256':sha(HERE/'handoff.json')},sort_keys=True))
    return 0 if okay else 1

if __name__=='__main__':raise SystemExit(main())
