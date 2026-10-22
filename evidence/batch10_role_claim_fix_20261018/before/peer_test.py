"""Independent adversarial role-context controls; fixtures are not source facts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from evidence.batch10_sec_20261018.build_review import load_inputs
from market_checker_app.collectors import sec_person_role_claim_review as service
from tests.test_sec_person_role_claim_review import changed, synthetic_inputs


NOW = datetime(2026,10,18,12,tzinfo=timezone.utc)


def inputs_for_case(kind):
    actual,_,_ = load_inputs()
    captures,selection = synthetic_inputs(actual['account_packet'])
    if kind == 'OTHER_PERSON_INSIDE_CAREER_TABLE':
        primary = captures['primary'];old = selection['role_context_quote']
        donor = 'Experience: Microsoft Corporation (2002-present) • Chief Financial Officer (2013-present)'
        boundary = b'<table><tr><td>Experience:'
        replacement = b'<table><tr><td>Amy Hood Age: 54 | Director Since: 2019 | Birthplace: USA</td></tr><tr><td>Experience:'
        raw = primary.body.replace(boundary,replacement,1).replace(old.encode(),donor.encode(),1)
        captures['primary'] = changed(primary,raw)
        index = captures['index']
        captures['index'] = changed(index,index.body.replace(('>'+str(primary.size_bytes)+'<').encode(),('>'+str(len(raw))+'<').encode()))
        selection.update(role_context_locator='table:2/row:2/cell:1',role_context_quote=donor)
    elif kind == 'HIDDEN_INDEX_FILING_DAY':
        index = captures['index']
        old = b'<div class="info">2025-10-21</div>'
        assert old in index.body
        captures['index'] = changed(index,index.body.replace(old,b'<div class="info" hidden="hidden">2025-10-21</div>',1))
    elif kind != 'COMPLETION_AFTER_EXPLICIT_CUTOFF':
        raise AssertionError('Unknown peer case')
    return captures,selection


def run_case(kind):
    captures,selection = inputs_for_case(kind)
    clocks = [NOW,NOW+timedelta(seconds=2)] if kind == 'COMPLETION_AFTER_EXPLICIT_CUTOFF' else [NOW,NOW]
    with patch.object(service,'_physical_clock',side_effect=clocks):
        return service.review_sec_person_role_claims(primary_capture=captures['primary'],
            index_capture=captures['index'],account_packet_capture=captures['account_packet'],
            selection=selection,knowledge_at=NOW if kind == 'COMPLETION_AFTER_EXPLICIT_CUTOFF' else None)


class RoleClaimPeerTests(unittest.TestCase):
    def assertSourceClaimsWithheld(self,kind):
        try:
            report = run_case(kind)
        except ValueError:
            return
        self.assertFalse(report.get('source_role_claims_supported',False),
            f'{kind}: unsupported subject/date/cutoff cannot produce source-supported person role claims')

    def test_different_named_subject_inside_adjacent_career_table_cannot_donate_roles(self):
        self.assertSourceClaimsWithheld('OTHER_PERSON_INSIDE_CAREER_TABLE')

    def test_hidden_index_filing_day_cannot_anchor_source_relative_present(self):
        self.assertSourceClaimsWithheld('HIDDEN_INDEX_FILING_DAY')

    def test_interpretation_completed_after_explicit_cutoff_cannot_publish_role_claims_at_that_cutoff(self):
        self.assertSourceClaimsWithheld('COMPLETION_AFTER_EXPLICIT_CUTOFF')


if __name__ == '__main__':unittest.main()
