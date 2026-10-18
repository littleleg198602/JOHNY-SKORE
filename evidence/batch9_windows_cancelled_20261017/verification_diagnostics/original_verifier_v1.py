from pathlib import Path
import hashlib
import json
import re
import sys
import tty
import urllib.request
import zipfile

tty.setraw(sys.stdin.fileno())
p=json.loads(sys.stdin.buffer.read(int(sys.argv[1])))
root=Path(sys.argv[2]).resolve();root.mkdir(parents=True,exist_ok=True)
with urllib.request.urlopen(p['download_url'],timeout=55) as response:
    data=response.read(p['size_in_bytes']+1)
sha=hashlib.sha256(data).hexdigest()
assert len(data)==p['size_in_bytes'] and sha==p['digest'].removeprefix('sha256:')
archive=root/f"artifact_{p['artifact_id']}.zip"
with archive.open('xb') as stream:stream.write(data)
with zipfile.ZipFile(archive) as z:
    names=z.namelist();assert names==['windows-tests.log'],names
    files={n:z.read(n) for n in names}
for name,body in files.items():
    with (root/name).open('xb') as stream:stream.write(body)
body=files['windows-tests.log'];log=body.decode('utf-8')
assert 'KeyboardInterrupt' in log
assert not re.search(r'Ran 2231 tests in [\d.]+s\r?\n\r?\nOK',log)
starts=list(re.finditer(r'(?m)^test_[^\r\n]+\([^\r\n]+\) \.\.\. ',log))
ok=[];unfinished=[]
for i,m in enumerate(starts):
    end=starts[i+1].start() if i+1<len(starts) else len(log)
    block=log[m.start():end].strip()
    if re.search(r'(?:\.\.\. |\r?\n)ok\Z',block):ok.append(m.group(0).removesuffix(' ... '))
    else:unfinished.append(m.group(0).removesuffix(' ... '))
new_methods=[]
for row in p['methods']:
    heading=f"{row['method']} ({row['module']}.{row['class']}.{row['method']})"
    assert heading in ok,heading
    new_methods.append(heading)
decoded=p['decoded_job_log'].encode('utf-8')
assert b'The operation was canceled.' in decoded
with (root/'hosted_windows_job_decoded.log').open('xb') as stream:stream.write(decoded)
pins={n:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'CRLF_lines':b.count(b'\r\n')} for n,b in files.items()}
assert p['workflow_run']['head_sha']==p['head_sha']
assert p['workflow_run']['status']=='completed' and p['workflow_run']['conclusion']=='cancelled'
jobs=p['jobs'];win=next(j for j in jobs if j['id']==p['windows_job_id'])
assert win['conclusion']=='cancelled'
assert any(j['id']==117521376998 and j['conclusion']=='failure' for j in jobs)
proof={'head_sha':p['head_sha'],'tree_sha':p['tree_sha'],'run_id':p['run_id'],
       'windows_job_id':p['windows_job_id'],'artifact_id':p['artifact_id'],
       'zip_bytes':len(data),'zip_SHA256':sha,'published_digest_exact':True,
       'original_members':names,'files':pins,'original_windows_report_JSON_absent':True,
       'no_report_JSON_manufactured':True,'complete_full_suite_verified':False,
       'plain_OK_footer_absent':True,'KeyboardInterrupt_present':True,
       'started_test_method_headings':len(starts),'complete_original_ok_blocks':len(ok),
       'incomplete_or_non_ok_headings':unfinished,
       'batch9_new_methods_original_ok':len(new_methods),'batch9_methods':new_methods,
       'observed_unittest_failure_markers':len(re.findall(r'^FAIL: |^ERROR: ',log,re.M)),
       'zero_final_failure_error_counts_claimed':False,
       'hosted_decoded_log':{'bytes':len(decoded),'sha256':hashlib.sha256(decoded).hexdigest()},
       'full_specialist_acceptance':0,'user_Windows_end_to_end_verified':False,'historical_OOS_verified':False}
with (root/'content_verification.json').open('x',encoding='utf-8') as stream:stream.write(json.dumps(proof,indent=2)+'\n')
with (root/'github_run_jobs_artifacts.json').open('x',encoding='utf-8') as stream:
    stream.write(json.dumps({k:p[k] for k in ['workflow_run','jobs','artifact_metadata']},indent=2)+'\n')
print(json.dumps({k:v for k,v in proof.items() if k!='batch9_methods'}))
