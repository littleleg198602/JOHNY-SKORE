"""Read-only verification of the retained incomplete Linux decoded console."""
from pathlib import Path
import hashlib
import json
import re


def main():
    folder = Path(__file__).resolve().parent
    root = folder.parents[1]
    raw = (folder / 'decoded.log').read_bytes()
    assert len(raw) == 641826
    assert hashlib.sha256(raw).hexdigest() == '118ff7acae6dff100f0450a73b643490062d9e35aa410640400a7bc0cdbbfc87'
    job = json.loads((folder / 'job.json').read_bytes())
    assert job['head_sha'] == 'b24a59563d8284daf8d38df20568fc76d4f03b60'
    assert job['run_id'] == 39391828283 and job['id'] == 118524938257
    assert job['status'] == 'completed' and job['conclusion'] == 'cancelled'
    text = re.sub(r'^\d{4}-\d\d-\d\dT[\d:.]+Z ?', '', raw.decode('utf-8'), flags=re.M)
    assert not re.search(r'(?m)^Ran \d+ tests in ', text)
    starts = list(re.finditer(r'(?m)^test_[^\r\n]+\([^\r\n]+\) \.\.\. ', text))
    blocks = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else text.index('##[error]The operation was canceled.', match.start())
        block = text[match.start():end].strip()
        blocks[match.group(0)] = bool(re.search(r'(?:\.\.\. |\r?\n)ok\Z', block))
    selection = json.loads((root / 'evidence/batch11_linux_methods_20261020.json').read_bytes())['methods']
    observed = []
    missing = []
    for row in selection:
        heading = f"{row['method']} ({row['module']}.{row['class']}.{row['method']}) ... "
        if blocks.get(heading):
            observed.append(row)
        else:
            assert heading not in blocks, 'A present but unqualified heading must be distinguished from an absent heading'
            missing.append(row)
    proof = json.loads((folder / 'content_verification.json').read_bytes())
    assert len(starts) == proof['started_headings_observed'] == 2576
    assert sum(blocks.values()) == proof['original_ok_blocks_observed'] == 2572
    assert observed == proof['new_methods_with_original_ok'] and len(observed) == 205
    assert missing == proof['new_methods_not_observed'] and len(missing) == 7
    assert all(value is None for value in proof['final_full_test_counts'].values())
    assert proof['final_counts_status'] == 'UNKNOWN_INCOMPLETE_CANCELLED_LOG'
    assert proof['full_plain_OK_footer_present'] is False
    print(json.dumps({'head_sha': job['head_sha'], 'run_id': job['run_id'], 'job_id': job['id'], 'originals_verified': True, 'started_headings': len(starts), 'strict_original_ok_blocks': sum(blocks.values()), 'new_exact_original_ok': len(observed), 'new_headings_not_observed': len(missing), 'final_counts': 'UNKNOWN', 'log_rewritten': False}))


if __name__ == '__main__':
    main()
