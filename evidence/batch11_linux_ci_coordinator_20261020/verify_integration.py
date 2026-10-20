"""Read-only qualification of this exact CI-only repair and retained originals."""
from pathlib import Path
import argparse
import copy
import datetime
import hashlib
import json
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = 'b24a59563d8284daf8d38df20568fc76d4f03b60'
WORKER = 'cce0faafb82e398189db61169827c1fc9e184bfd'
PICK = '2395c81'
WORKFLOW = '.github/workflows/market-checker-test-agents.yml'
ALLOWED = {
    WORKFLOW,
    *('evidence/batch11_linux_timeout_fix_20261020/' + name for name in (
        'README.md', 'handoff.json', 'original_workflow.yml', 'qualification.json',
        'revised_workflow.yml', 'verify_workflow_change.py')),
    *('evidence/batch11_linux_cancelled_20261020/' + name for name in (
        'README.md', 'decoded.log', 'job.json', 'content_verification.json',
        'verify_original.py', 'offline_verifier_stdout.json')),
    'evidence/batch11_linux_ci_coordinator_20261020/qualification.json',
    'evidence/batch11_linux_ci_coordinator_20261020/verify_integration.py',
}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def inventory(ref):
    result = {}
    for item in git('ls-tree', '-r', '-z', ref).split(b'\0'):
        if item:
            desc, name = item.split(b'\t', 1)
            result[name.decode()] = desc.decode()
    return result


def pin(path):
    raw = (ROOT / path).read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def collect():
    old, current = inventory(BASE), inventory('HEAD')
    assert all(current.get(name) == entry for name, entry in old.items() if name != WORKFLOW)
    groups = {}
    for label, prefix, count in (('production', 'market_checker_app/', 230), ('tests', 'tests/', 209), ('evidence', 'evidence/', 2210)):
        names = [name for name in old if name.startswith(prefix)]
        assert len(names) == count
        groups[label] = {'files': count, 'all_original_Git_blobs_unchanged': True}
    proof_path = 'evidence/batch11_integration_20261019.json'
    assert pin(proof_path)['sha256'] == 'bfc5ef160081e924d03d1f4d6005fbf32da4fd454cbc3af9f82722035a517699'
    source_proof = json.loads((ROOT / proof_path).read_bytes())
    for name, expected in source_proof['combined_source_test_pins'].items():
        assert pin(name) == {'bytes': expected['bytes'], 'sha256': expected['sha256']}, name
    assert len(source_proof['combined_source_test_pins']) == 19
    folder = 'evidence/batch11_linux_timeout_fix_20261020/'
    handoff = json.loads((ROOT / folder / 'handoff.json').read_bytes())
    for name, expected in handoff['evidence_file_pins'].items():
        assert pin(folder + name) == expected, name
    before_raw = (ROOT / folder / 'original_workflow.yml').read_bytes()
    after_raw = (ROOT / WORKFLOW).read_bytes()
    assert before_raw == git('show', BASE + ':' + WORKFLOW)
    assert after_raw == (ROOT / folder / 'revised_workflow.yml').read_bytes()
    assert hashlib.sha256(after_raw).hexdigest() == 'c8d1f21085815a92923b6312f55e1bc4da176195cfb04c5b42f551678d08ebf4'
    before, after = yaml.safe_load(before_raw), yaml.safe_load(after_raw)
    assert before['jobs']['contract-agent']['timeout-minutes'] == 10
    assert after['jobs']['contract-agent']['timeout-minutes'] == 20
    expected = copy.deepcopy(before)
    expected['jobs']['contract-agent']['timeout-minutes'] = 20
    assert expected == after
    windows = [job for job in after['jobs'].values() if job.get('name') == 'Windows contracts, persistence and UI']
    assert len(windows) == 1 and windows[0]['timeout-minutes'] == 60
    cancelled = 'evidence/batch11_linux_cancelled_20261020/'
    subprocess.run(['python', str(ROOT / cancelled / 'verify_original.py')], cwd=ROOT, stdout=subprocess.DEVNULL, check=True)
    cancelled_pins = {name: pin(name) for name in sorted(ALLOWED) if name.startswith(cancelled)}
    return {
        'public_base': BASE, 'actual_worker_commit': WORKER, 'root_pick': PICK,
        'allowed_changed_paths': sorted(ALLOWED), 'baseline_groups': groups,
        'pre_hosted_full_code_proof': {'path': proof_path, **pin(proof_path)},
        'current_source_test_pins_unchanged': 19,
        'only_YAML_semantic_change': {'path': 'jobs.contract-agent.timeout-minutes', 'before': 10, 'after': 20},
        'workflow': pin(WORKFLOW), 'worker_handoff': pin(folder + 'handoff.json'),
        'worker_qualification': pin(folder + 'qualification.json'),
        'cancelled_originals': cancelled_pins,
        'Windows_timeout_unchanged': 60, 'commands_platforms_tests_upload_release_gate_unchanged': True,
        'local_full_suites_repeated': 0, 'production_test_status_MD_registry_model_score_fitter_changes': 0,
        'source_HTTP_SQL_quota_or_retained_database_actions': 0,
        'old_Windows_job_cancelled_or_restarted': False,
        'new_exact_head_hosted_CI': 'PENDING_AFTER_PUBLICATION',
        'full_specialist_acceptance': '0/21',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    target = Path(__file__).with_name('qualification.json')
    facts = collect()
    if args.record:
        with target.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps({'physical_recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), **facts}, indent=2) + '\n')
    else:
        saved = json.loads(target.read_bytes())
        assert all(saved[key] == value for key, value in facts.items())
        old, current = inventory(BASE), inventory('HEAD')
        changed = {name for name in set(old) | set(current) if old.get(name) != current.get(name)}
        assert changed == ALLOWED, sorted(changed ^ ALLOWED)
        assert not git('status', '--porcelain=v1'), 'Final committed working tree must be clean'
    print(json.dumps({'recorded': args.record, 'public_base': BASE, 'exact_allowed_paths': len(ALLOWED), 'source_test_pins_unchanged': 19, 'only_semantic_change': facts['only_YAML_semantic_change'], 'Windows_timeout': 60, 'full_suites_repeated': 0, 'qualification': pin(str(target.relative_to(ROOT)))}))


if __name__ == '__main__':
    main()
