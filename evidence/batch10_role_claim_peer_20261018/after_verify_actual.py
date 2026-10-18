"""Actual original-role replay after owner fix; no synthetic identity acceptance."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from evidence.batch10_sec_20261018.build_review import load_inputs
from market_checker_app.collectors import sec_person_role_claim_review as service


LANE = Path(__file__).resolve().parent


if __name__ == '__main__':
    source = Path(service.__file__)
    before = source.read_bytes()
    captures,selection,_ = load_inputs()
    report = service.review_sec_person_role_claims(primary_capture=captures['primary'],index_capture=captures['index'],
        account_packet_capture=captures['account_packet'],selection=selection)
    assert before == source.read_bytes(), 'Producer changed during actual replay'
    assert report['source_role_claims_supported'] is True
    assert report['role_claim_count'] == 7 and report['career_assertion_count'] == 8
    for role in report['role_claims']:
        assert role['person_name_literal'] == 'Satya Nadella'
        assert role['start_assertion']['precision'] == 'YEAR'
        assert role['start_assertion']['exact_day'] is None
        assert role['legal_interval_verified'] is False and role['continuous_role_verified'] is False
        assert role['exact_appointment_or_cessation_day_verified'] is False
        end = role['end_assertion']
        if end and end['literal'] == 'present':
            assert end['precision'] == 'SOURCE_RELATIVE_PRESENT'
            assert end['anchor_filing_day'] == '2025-10-21'
            assert end['exact_end_day'] is None and end['continues_through_capture_or_current_form4'] is False
        elif end:
            assert end['precision'] == 'YEAR' and end['exact_day'] is None
    for key in ('natural_person_account_binding_verified','approved_name_alias','legal_role_interval_verified',
                'continuous_role_verified','instrument_identity_verified','cusip_class_validity_verified',
                'historical_machine_availability_inferred','issuer_attribution_allowed','registry_modified',
                'scoring_applied','trading_allowed','quota_or_lease_modified'):
        assert report[key] is False, key
    comparison = report['source_comparison']
    assert comparison['named_person_disclosure_count'] == 1
    assert comparison['underlying_disclosure_count'] == 2
    assert comparison['distinct_disclosures_are_independent_legal_identity_proof'] is False
    assert comparison['independent_natural_person_account_binding'] is False
    assert report['source_document']['filing_day'] == '2025-10-21'
    assert report['source_document']['publication_timezone'] == 'UNKNOWN'
    assert report['source_document']['captured_primary_bytes']-report['source_document']['index_declared_primary_bytes'] == 110
    assert report['comparison_account_facts']['period_day'] == '2026-08-31'
    assert report['network_requests_performed'] == 0
    assert datetime.fromisoformat(report['review_available_at']) >= datetime.fromisoformat(report['review_ingested_at'])
    owner_root = source.parents[2]
    artifact = {'schema_version':1,'verified_at':datetime.now(timezone.utc).isoformat(),
        'candidate_path':str(source),'candidate_sha256':hashlib.sha256(before).hexdigest(),'candidate_bytes':len(before),
        'owner_head_at_verification':subprocess.check_output(['git','-C',str(owner_root),'rev-parse','HEAD'],text=True).strip(),
        'owner_source_status_at_verification':subprocess.check_output(['git','-C',str(owner_root),'status','--short','--',str(source)],text=True).strip(),
        'review':report,'original_source_pins':{key:{'sha256':capture.sha256,'bytes':capture.size_bytes,'source_url':capture.source_url} for key,capture in captures.items()},
        'source_http_requests':0,'db_or_quota_operations':0,'registry_adoptions':0,
        'independent_natural_person_identity_or_continuity_claimed':False,'new_historical_oos_or_user_windows_acceptance_claimed':False,
        'actual_retained_role_interpretation_preserved':True}
    with (LANE/'after_actual_review.json').open('x',encoding='utf-8') as f:
        json.dump(artifact,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n')
    print(json.dumps({'candidate_sha256':artifact['candidate_sha256'],'actual_roles':report['role_claim_count'],
        'career_assertions':report['career_assertion_count'],'present_anchor':'2025-10-21','source_http_requests':0}))
