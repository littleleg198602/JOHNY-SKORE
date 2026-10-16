from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tests.test_sec_counterparty_identity import identity_record,resolve
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry


class CounterpartyRegistryCacheTests(unittest.TestCase):
    def test_same_size_same_mtime_edit_invalidates_the_validated_snapshot(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'registry.json'
            first={'schema_version':1,'records':[identity_record()]}
            path.write_text(json.dumps(first));stamp=path.stat()
            original=DatedCounterpartyIdentityRegistry.load(path)
            changed=json.dumps(first).replace('0000001234','0000001235').replace('/1234/','/1235/')
            self.assertEqual(len(path.read_text()),len(changed))
            path.write_text(changed);os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            updated=DatedCounterpartyIdentityRegistry.load(path)
            self.assertEqual('0000001234',resolve(original)['counterparty_identity_evidence']['issuer_cik'])
            self.assertEqual('0000001235',resolve(updated)['counterparty_identity_evidence']['issuer_cik'])

    def test_detached_records_and_invalid_replacement_cannot_poison_a_cached_registry(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'registry.json';data={'schema_version':1,'records':[identity_record()]}
            path.write_text(json.dumps(data));first=DatedCounterpartyIdentityRegistry.load(path)
            detached=first.records();detached[0]['legal_name']='Poisoned'
            self.assertEqual('Acme Components Inc.',DatedCounterpartyIdentityRegistry.load(path).records()[0]['legal_name'])
            invalid=deepcopy(data);invalid['records'][0]['relationship_role']='INVALID';path.write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):DatedCounterpartyIdentityRegistry.load(path)
            path.write_text(json.dumps(data))
            self.assertEqual('Acme Components Inc.',DatedCounterpartyIdentityRegistry.load(path).records()[0]['legal_name'])

    def test_changed_independent_alias_context_is_rechecked_on_a_cache_hit(self):
        DatedCounterpartyIdentityRegistry.load()
        with patch('market_checker_app.services.reviewed_identity_service.ReviewedIdentityRegistry.decision',return_value=None):
            with self.assertRaisesRegex(ValueError,'Unreviewed canonical'):
                DatedCounterpartyIdentityRegistry.load()
