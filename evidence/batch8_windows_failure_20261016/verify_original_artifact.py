import hashlib
import json
from pathlib import Path
import re
import sys
import tty
import urllib.request
import zipfile

tty.setraw(sys.stdin.fileno())
p = json.loads(sys.stdin.buffer.read(int(sys.argv[1])))
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(p['download_url'], timeout=55) as response:
    raw = response.read(2_000_001)
assert len(raw) == p['size_in_bytes']
assert hashlib.sha256(raw).hexdigest() == p['digest'].removeprefix('sha256:')
archive_path = out / f"artifact_{p['artifact_id']}.zip"
assert not archive_path.exists()
archive_path.write_bytes(raw)
with zipfile.ZipFile(archive_path) as z:
    assert set(z.namelist()) == {'windows-tests.json', 'windows-tests.log'}
    bodies = {name: z.read(name) for name in z.namelist()}
for name, body in bodies.items():
    assert not (out / name).exists()
    (out / name).write_bytes(body)
report = json.loads(bodies['windows-tests.json'])
assert report['discovered_tests'] == report['tests_run'] == 2036
assert report['failures'] == 1 and report['errors'] == 0 and report['successful'] is False
assert all(report[k] == 0 for k in ('skipped', 'expected_failures', 'unexpected_successes'))
assert report['platform'].startswith('Windows')
log = bodies['windows-tests.log']
expected = b'test_explicit_paths_refuse_escape_missing_and_parent_symlink_without_discovery'
headers = re.findall(rb'^(FAIL|ERROR): ([^ \r\n]+)', log, re.M)
assert headers == [(b'FAIL', expected)]
assert re.search(rb'Ran 2036 tests in 2241\.765s\r?\n\r?\nFAILED \(failures=1\)\r?\n\Z', log)
assert b'AssertionError:' in log and b'[66 rows x 5 columns] is not None' in log
decoded = p['decoded_job_log'].encode('utf-8')
assert re.search(r'Ran 63 tests in 29\.937s', p['decoded_job_log'])
assert re.search(r'Ran 2036 tests in 2241\.765s', p['decoded_job_log'])
assert 'FAILED (failures=1)' in p['decoded_job_log']
(out / 'job_116843520437_decoded.log').write_bytes(decoded)
failed_block = log[log.index(b'FAIL: '):]
(out / 'original_failure_and_terminal_summary.log').write_bytes(failed_block)
proof = {
    'head_sha': p['head_sha'], 'tree_sha': p['tree_sha'], 'run_id': p['run_id'],
    'windows_job_id': 116843520437, 'release_gate_job_id': 116855563520,
    'artifact_id': p['artifact_id'], 'artifact_bytes': len(raw),
    'artifact_SHA256': hashlib.sha256(raw).hexdigest(), 'published_digest_matches': True,
    'both_original_zip_members_read': True, 'original_members': {
        name: {'bytes': len(body), 'SHA256': hashlib.sha256(body).hexdigest(), 'CRLF_lines': body.count(b'\r\n')}
        for name, body in bodies.items()
    }, 'original_windows_report': report,
    'decoded_job_log_SHA256': hashlib.sha256(decoded).hexdigest(),
    'focused_checkpoint_tests': 63, 'focused_checkpoint_seconds': 29.937,
    'full_test_count': 2036, 'elapsed_seconds': 2241.765,
    'terminal_plain_OK': False, 'terminal_FAILED_failures_1_verified': True,
    'exact_failed_method': expected.decode(),
    'failure_is_acceptance': False, 'full_specialist_acceptance': 0,
    'source_HTTP_or_database_operations': 0,
}
(out / 'content_verification.json').write_text(json.dumps(proof, indent=2) + '\n')
print(json.dumps(proof))
