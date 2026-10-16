"""Read-only held-out legal-identity evaluation on retained primary bytes."""
import base64
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import zlib
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot,MAX_SOURCE_BYTES
from market_checker_app.services.legal_entity_evidence_service import parse_legal_entity
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry,DEFAULT_COUNTERPARTY_IDENTITIES
from market_checker_app.services.reviewed_identity_service import clock

DEFAULT_HOLDOUT=Path(__file__).resolve().parents[2]/'evidence/identity_holdout_sources_20261015.json'


def registry_digest(path):
    # Git checkouts may convert JSON line endings on Windows. Preserve the
    # exact UTF-8 content hash while making only CRLF/LF representation neutral.
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def evaluate_identity_holdout(path=DEFAULT_HOLDOUT,*,registry_path=DEFAULT_COUNTERPARTY_IDENTITIES):
    with Path(path).open('rb') as stream:body=stream.read(5_000_001)
    if len(body)>5_000_000:raise ValueError('Unbounded identity holdout')
    data=json.loads(body)
    if data.get('schema_version')!=1 or not 1<=len(data['records'])<=1000:
        raise ValueError('Invalid identity holdout')
    training_sha=registry_digest(registry_path)
    if training_sha!=data['training_registry_sha256']:
        raise ValueError('Training registry changed after holdout capture')
    registry=DatedCounterpartyIdentityRegistry.load(registry_path);training=registry.records()
    training_urls={e['url'] for r in training for e in r['evidence']}
    training_hashes={e.get('sha256') for r in training for e in r['evidence']}
    before=min(clock(r['known_at']) for r in training)-timedelta(microseconds=1)
    frozen=clock(data['training_frozen_at']);results=[];seen=set()
    for row in data['records']:
        meta=row['source']
        if row['canonical_ticker'] in seen:raise ValueError('Duplicate holdout case')
        seen.add(row['canonical_ticker'])
        if (meta['url'] in training_urls or meta['sha256'] in training_hashes
                or clock(meta['observed_at'])<frozen):
            raise ValueError('Holdout overlaps training or precedes freeze')
        decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(row['payload_zlib_base64'],validate=True),MAX_SOURCE_BYTES+1)
        if len(raw)>MAX_SOURCE_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Oversized or invalid compressed primary source')
        snapshot=SourceSnapshot(meta['kind'],meta['url'],clock(meta['observed_at']),raw,clock(meta['published_at']))
        if snapshot.sha256!=meta['sha256']:raise ValueError('Holdout primary checksum mismatch')
        legal=parse_legal_entity(snapshot)
        if legal['cik']!=row['expected_cik'] or legal['legal_name']!=row['legal_name_as_reported']:
            raise ValueError('Holdout label contradicts primary registrant context')
        args={'disclosed_name':legal['legal_name'],'published_at':snapshot.published_at,
            'as_of':snapshot.observed_at,'exposure_source_url':snapshot.url}
        result=registry.resolve(**args)
        details=result.get('counterparty_identity_evidence') or {}
        identified=details.get('entity_identifier',{}).get('value')
        earlier=registry.resolve(**{**args,'as_of':before})
        results.append({'ticker':row['canonical_ticker'],'source_url':snapshot.url,
            'sha256':snapshot.sha256,'publication_time':meta['published_at'],
            'capture_time':meta['observed_at'],'expected_cik':row['expected_cik'],
            'resolved_cik':identified,'resolution':result['counterparty_identity_resolution'],
            'incorrect_attribution':identified is not None and identified!=row['expected_cik'],
            'financial_attribution_allowed':result['counterparty_financials_attribution_allowed'],
            'preknowledge_attribution_allowed':earlier['counterparty_issuer_attribution_allowed']})
    identified=sum(r['resolved_cik'] is not None for r in results)
    incorrect=sum(r['incorrect_attribution'] for r in results)
    leakage=sum(r['preknowledge_attribution_allowed'] for r in results)
    financial=sum(r['financial_attribution_allowed'] for r in results)
    return {'schema_version':1,'scope':'Independent archived issuer-identity classification; no price/return performance claim',
        'training_registry_sha256':training_sha,'training_frozen_at':data['training_frozen_at'],
        'cases':len(results),'correct_identifications':identified-incorrect,'abstentions':len(results)-identified,
        'incorrect_attributions':incorrect,'preknowledge_leakage':leakage,'financial_attributions':financial,
        'resolution_counts':dict(Counter(r['resolution'] for r in results)),
        'independent_primary_documents_verified':True,'results':results,
        'bounded_identity_holdout_verified':bool(results) and identified>0 and incorrect==leakage==financial==0}
