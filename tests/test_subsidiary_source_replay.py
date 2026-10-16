from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_checker_app.services.subsidiary_source_replay import DEFAULT_SOURCES, replay_subsidiary_sources
from market_checker_app.services.specialist_identity_config import configured_counterparty_identities, DEFAULT_SUBSIDIARY_IDENTITIES
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.identity_specialist_acceptance import completion_requirements


class SubsidiarySourceReplayTests(unittest.TestCase):
    def test_primary_replay_reproduces_seven_distinct_legal_children(self):
        rows = replay_subsidiary_sources()
        self.assertEqual(list(rows), json.loads(DEFAULT_SUBSIDIARY_IDENTITIES.read_text())['records'])
        self.assertEqual(7, len({r['entity_identifier']['value'] for r in rows}))
        registry, status = configured_counterparty_identities({})
        self.assertEqual('LOADED', status)
        self.assertEqual(9, sum(r['relationship_role'] == 'SUBSIDIARY_OF_ISSUER' for r in registry.records()))
        child = rows[0]
        args = dict(disclosed_name=child['legal_name'], published_at=clock(child['effective_from']+'T00:00:00+00:00'),
                    as_of=clock(child['known_at']), exposure_source_url='https://www.sec.gov/Archives/edgar/data/1/exposure.htm')
        self.assertEqual('IDENTIFIED', registry.resolve(**args)['counterparty_identity_status'])
        self.assertFalse(registry.resolve(**args)['counterparty_financials_attribution_allowed'])
        self.assertEqual('NOT_YET_KNOWN', registry.resolve(**{**args, 'as_of':clock(child['known_at'])-timedelta(microseconds=1)})['counterparty_identity_resolution'])
        self.assertEqual('OUTSIDE_EFFECTIVE_PERIOD', registry.resolve(**{**args, 'published_at':clock(child['known_at'])})['counterparty_identity_resolution'])

    def test_tampered_source_parent_row_date_and_knowledge_are_rejected(self):
        original = json.loads(DEFAULT_SOURCES.read_text())
        with TemporaryDirectory() as temp:
            path = Path(temp)/'archive.json'
            for kind in ['hash', 'parent', 'row', 'date', 'future_knowledge', 'joint', 'document_type', 'longer_legal_name']:
                data = deepcopy(original)
                selection = data['selections'][0]
                if kind == 'longer_legal_name':
                    selection = data['selections'][-1]
                    selection['row_text'] = 'Commonwealth Edison Company of Indiana, Inc. Indiana'
                elif kind == 'hash':data['sources'][selection['child_source_id']]['source']['sha256'] = '0'*64
                elif kind == 'parent':selection['parent_cik'] = '0000320193'
                elif kind == 'row':selection['row_text'] += ' fabricated'
                elif kind == 'date':selection['as_reported_date'] = '2020-01-01'
                elif kind == 'future_knowledge':selection['known_at'] = '2020-01-01T00:00:00+00:00'
                elif kind == 'document_type':selection['document_type'] = 'EX-21.99'
                else:selection['heading'] += ' and joint ventures'
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError, msg=kind):replay_subsidiary_sources(path)

    def test_invalid_supplement_does_not_silently_fall_back_to_partial_registry(self):
        with patch('market_checker_app.services.specialist_identity_config.DEFAULT_SUBSIDIARY_IDENTITIES', Path('/missing/manifest.json')):
            registry, status = configured_counterparty_identities({})
        self.assertEqual('INVALID_IDENTITY_MANIFEST', status)
        self.assertEqual((), registry.records())

    def test_prepared_windows_workflow_does_not_satisfy_completion_requirements(self):
        requirements = completion_requirements()
        self.assertFalse(requirements['all_required_evidence_verified'])
        self.assertIn('windows_run_evidence', requirements['unverified_requirements'])
        self.assertIn('coverage_evidence', requirements['unverified_requirements'])
