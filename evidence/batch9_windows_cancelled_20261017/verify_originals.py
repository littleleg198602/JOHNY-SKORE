"""Verify the cancelled b0e Windows artifact and completed sibling logs offline."""
from pathlib import Path
import hashlib,json,re,zipfile

ROOT=Path(__file__).resolve().parent
PINS={
 'artifact_12117165429.zip':'7f0a915a75c3fa3a128e030a6923f535d7cea4e9a6817caba1de63a1a563edf9',
 'windows-tests.log':'6463c4d1bb84c6d4bb763f47d1e58923301216445c82dd12877bbbf4755ec7a7',
 'hosted_windows_job_decoded.log':'d1b28e62d8d5c21fd5e87a173e69d8a58522194db1720dc4f047fa0391d230b5',
 'other_jobs/job_117513252465_decoded.log':'0b02bb20b7e14e67af7373cb5487b891df9fddf0f6cc963d7ef2d0fa73c3c812',
 'other_jobs/job_117513252423_decoded.log':'816666350c1d2238044e1c5d8805f77ec53693958e4923321c0e268647af8889',
 'other_jobs/job_117513252298_decoded.log':'632a7f1a7591459918b9101586ebad8429a941f32ccab7ca0e2dc6f72443d7c1',
}

def verify():
    raw={name:(ROOT/name).read_bytes() for name in PINS}
    assert all(hashlib.sha256(raw[n]).hexdigest()==h for n,h in PINS.items())
    with zipfile.ZipFile(ROOT/'artifact_12117165429.zip') as archive:
        assert archive.namelist()==['windows-tests.log']
        assert archive.read('windows-tests.log')==raw['windows-tests.log']
    assert not (ROOT/'windows-tests.json').exists()
    report=json.loads((ROOT/'content_verification.json').read_bytes())
    assert report['started_test_method_headings']==1945
    assert report['complete_original_ok_blocks']==1944
    assert report['batch9_new_methods_original_ok']==194
    assert not report['complete_full_suite_verified'] and report['plain_OK_footer_absent']
    log=raw['windows-tests.log'].decode('utf-8')
    starts=list(re.finditer(r'(?m)^test_[^\r\n]+\([^\r\n]+\) \.\.\. ',log))
    assert len(starts)==1945
    ok=[]
    for i,match in enumerate(starts):
        end=starts[i+1].start() if i+1<len(starts) else len(log)
        if re.search(r'(?:\.\.\. |\r?\n)ok\Z',log[match.start():end].strip()):ok.append(match.group(0).removesuffix(' ... '))
    assert len(ok)==1944
    assert all(name in ok for name in report['batch9_methods'])
    assert log.endswith('test_687_tickers_rotate_with_daily_limit_and_offline_catchup (test_sec_scout_service.SecScoutServiceTests.test_687_tickers_rotate_with_daily_limit_and_offline_catchup) ... ')
    assert 'KeyboardInterrupt' not in log
    assert b'KeyboardInterrupt' in raw['hosted_windows_job_decoded.log']
    assert b'The operation was canceled.' in raw['hosted_windows_job_decoded.log']
    assert re.search(r'Ran 2231 tests in [\d.]+s\r?\n\r?\nOK',log) is None
    github=json.loads((ROOT/'github_run_jobs_artifacts.json').read_bytes())
    run=github['workflow_run']
    assert run['id']==39072646508 and run['head_sha']=='b0e866de120c8d6366b93c8fef26916f0db4aa0e'
    assert run['conclusion']=='cancelled'
    assert github['artifact_metadata']['digest']=='sha256:'+PINS['artifact_12117165429.zip']
    jobs=github['jobs'];assert len(jobs)==5
    assert next(j for j in jobs if j['id']==117513252470)['conclusion']=='cancelled'
    assert next(j for j in jobs if j['id']==117521376998)['conclusion']=='failure'
    for job,count,elapsed in [(117513252465,2231,'435.234'),(117513252423,8,'15.472'),(117513252298,5,'22.108')]:
        text=re.sub(r'^\d{4}-\d\d-\d\dT[\d:.]+Z ?','',raw[f'other_jobs/job_{job}_decoded.log'].decode(),flags=re.M)
        assert re.search(r'^Ran '+str(count)+r' tests in '+re.escape(elapsed)+r's\r?\n\r?\nOK\r?$',text,re.M)
    return {'original_zip_and_member_verified':True,'Windows_cancelled_not_acceptance':True,
            'actual_completed_ok_blocks':1944,'actual_started_headings':1945,'original_Windows_report_absent':True,
            'new_batch9_methods_ok':194,'other_completed_Linux_UI_scale_logs_verified':True,
            'full_specialist_acceptance':0}

if __name__=='__main__':print(json.dumps(verify()))
