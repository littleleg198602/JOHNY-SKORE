from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from market_checker_app.services.identity_holdout_service import DEFAULT_HOLDOUT,evaluate_identity_holdout
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.identity_specialist_acceptance import build_report
from market_checker_app.services.sec_counterparty_identity import DEFAULT_COUNTERPARTY_IDENTITIES


class IdentityHoldoutAcceptanceTests(unittest.TestCase):
    def test_independent_primary_holdout_retains_abstentions_and_no_false_attribution(self):
        report=evaluate_identity_holdout()
        self.assertEqual((10,5,5),(report['cases'],report['correct_identifications'],report['abstentions']))
        self.assertEqual((0,0,0),(report['incorrect_attributions'],report['preknowledge_leakage'],report['financial_attributions']))
        self.assertTrue(report['bounded_identity_holdout_verified'])

    def test_changed_training_corrupt_bytes_future_freeze_and_wrong_labels_fail(self):
        original=json.loads(DEFAULT_HOLDOUT.read_text())
        with TemporaryDirectory() as temp:
            path=Path(temp)/'holdout.json'
            for mutation in ['training','checksum','label','freeze']:
                data=deepcopy(original)
                if mutation=='training':data['training_registry_sha256']='0'*64
                elif mutation=='checksum':data['records'][0]['source']['sha256']='0'*64
                elif mutation=='label':data['records'][0]['expected_cik']='0000000001'
                else:data['training_frozen_at']='2099-01-01T00:00:00+00:00'
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):evaluate_identity_holdout(path)

    def test_windows_checkout_line_endings_do_not_change_training_identity(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'registry.json'
            path.write_bytes(DEFAULT_COUNTERPARTY_IDENTITIES.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
            report=evaluate_identity_holdout(registry_path=path)
            self.assertTrue(report['bounded_identity_holdout_verified'])

    def test_archived_end_to_end_persists_the_687_history_and_measures_685_scope(self):
        data=json.loads(DEFAULT_HOLDOUT.read_text())
        with TemporaryDirectory() as temp:
            db=Path(temp)/'replay.db'
            report=build_report(as_of=clock(data['captured_at']),database=db)
            self.assertEqual((687,685,685),(report['canonical_tickers'],report['included_tickers'],report['verified_registered_classes']))
            self.assertEqual({'OZK','LEG'},set(report['excluded_tickers']))
            self.assertEqual({'entities':687,'versions':687},{k:report['persistence'][k] for k in ['entities','versions']})
            self.assertEqual(683,report['independent_legal_issuers'])
            self.assertEqual(2,report['subsidiary_observations'])
            self.assertTrue(report['historical_xom']['separate_issuers_preserved'])
            self.assertFalse(report['acceptance']['complete_specialist_verified'])
            self.assertFalse(report['acceptance']['complete_subsidiary_brand_product_catalog_verified'])
            self.assertFalse(report['scoring_changed'])
            with self.assertRaises(ValueError):build_report(as_of=clock(data['captured_at']),database=db)
