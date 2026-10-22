"""Run the unchanged original test with a real lexical POSIX temp-root alias."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
METHOD='test_explicit_paths_refuse_escape_missing_and_parent_symlink_without_discovery'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(output_directory):
    output_directory=Path(output_directory)
    output_directory.mkdir(parents=True,exist_ok=True)
    original=HERE/'original/test_module.py'
    namespace=ModuleType('preserved_original_captured_provider_tests')
    namespace.__file__=str(original)
    exec(compile(original.read_bytes(),str(original),'exec'),namespace.__dict__)
    observations={}
    native_temporary_directory=tempfile.TemporaryDirectory
    def aliased_temporary_directory(*args,**kwargs):
        temporary=native_temporary_directory(*args,**kwargs)
        physical=Path(temporary.name)
        alias=physical/'..'/physical.name
        observations.update(native_temp_root=str(physical), supplied_temp_alias=str(alias),
            alias_resolves_to_native=alias.resolve()==physical.resolve())
        # The native cleanup retains its exact original directory and lifetime.
        return SimpleNamespace(name=str(alias),cleanup=temporary.cleanup)
    class OriginalMethodWithAliasedTemporaryDirectory(namespace.CapturedMarketDataProviderTests):
        def setUp(self):
            super().setUp()
            observations.update(fixture_root=str(self.root),fixture_source_parent=str(self.path.parent),
                canonical_source_parent=str(self.path.parent.resolve()))
        def provider(self,**kwargs):
            provider=super().provider(**kwargs)
            canonical_target=provider.archive_root/'selected'
            observations.update(provider_archive_root=str(provider.archive_root),
                provider_guard_target=str(canonical_target),
                original_mock_target_matches_canonical_guard=canonical_target==self.path.parent)
            return provider
    case=OriginalMethodWithAliasedTemporaryDirectory(METHOD)
    suite=unittest.TestSuite([case])
    with patch('socket.socket.connect',side_effect=AssertionError('No HTTP')), \
         patch('sqlite3.connect',side_effect=AssertionError('No DB')), \
         patch.object(tempfile,'TemporaryDirectory',side_effect=aliased_temporary_directory), \
         (output_directory/'original_alias_failure.log').open('x',encoding='utf-8',newline='\n') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    tree=ast.parse(original.read_bytes())
    method=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name==METHOD)
    report={'observed_at':datetime.now(timezone.utc).isoformat(),'platform':sys.platform,
        'original_test_sha256':sha(original),
        'original_method_ast_sha256':hashlib.sha256(ast.dump(method,include_attributes=False).encode()).hexdigest(),
        'method_overridden':False,'only_input_variation':'TemporaryDirectory.name is native-root/../same-root-name',
        'observations':observations,'tests':{'discovered':1,'executed':result.testsRun,
            'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
            'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses)},
        'failure_log_sha256':sha(output_directory/'original_alias_failure.log'),
        'current_acceptance':False,'actual_windows_executed':False,
        'source_http_requests':0,'actual_db_accesses':0,'production_code_modified':False,
        'hosted_windows_failure_reference':'38861160925'}
    with (output_directory/'original_alias_failure.json').open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(report,sort_keys=True,indent=2)+'\n')
    print(json.dumps(report,sort_keys=True))
    return 0 if result.testsRun==1 and len(result.failures)==1 and not result.errors else 1

if __name__=='__main__':
    raise SystemExit(run(HERE/'before_repair'))
