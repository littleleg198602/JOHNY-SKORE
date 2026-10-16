"""Rebuild reviewed subsidiary observations from retained, checksummed sources.

Selections are reviewed literal rows, not fuzzy name matches. A filing title or
joint registrant list cannot create ownership or an independently invented CIK.
"""
import base64
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import zlib
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from market_checker_app.services.legal_entity_evidence_service import parse_legal_entity
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.services.sec13f_identity_bulk import SourceSnapshot, MAX_SOURCE_BYTES
from market_checker_app.services.sec_counterparty_identity import DatedCounterpartyIdentityRegistry

DEFAULT_SOURCES = Path(__file__).resolve().parents[2] / 'evidence/subsidiary_sources_20261016.json'


def _text(value):
    return ' '.join(value.split())


def replay_subsidiary_sources(path=DEFAULT_SOURCES):
    with Path(path).open('rb') as stream:
        raw = stream.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError('Subsidiary source archive exceeds budget')
    archive = json.loads(raw)
    if archive.get('schema_version') != 1 or not 1 <= len(archive['selections']) <= 1000:
        raise ValueError('Invalid subsidiary source archive')
    if len(archive['sources']) > 2000:
        raise ValueError('Too many subsidiary sources')
    sources = {}
    total_bytes = 0
    for source_id, item in archive['sources'].items():
        decoder = zlib.decompressobj()
        body = decoder.decompress(base64.b64decode(item['payload_zlib_base64'], validate=True), MAX_SOURCE_BYTES + 1)
        if len(body) > MAX_SOURCE_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Invalid compressed subsidiary source')
        total_bytes += len(body)
        if total_bytes > 100_000_000:
            raise ValueError('Subsidiary source bundle exceeds budget')
        meta = item['source']
        snapshot = SourceSnapshot('ticker_filing', meta['url'], clock(meta['observed_at']), body, clock(meta['published_at']))
        if snapshot.sha256 != meta['sha256'] or snapshot.snapshot_id != source_id:
            raise ValueError('Subsidiary source checksum mismatch')
        sources[source_id] = snapshot
    parents = {r['issuer_cik']: r for r in DatedCounterpartyIdentityRegistry.load().records()
               if r['relationship_role'] == 'DIRECT_ISSUER'}
    records = []
    for selection in archive['selections']:
        child_source = sources[selection['child_source_id']]
        child = parse_legal_entity(child_source)
        parent = parents[selection['parent_cik']]
        exhibit = sources[selection['exhibit_source_id']]
        if not exhibit.url.startswith(f"https://www.sec.gov/Archives/edgar/data/{int(parent['issuer_cik'])}/"):
            raise ValueError('Subsidiary exhibit belongs to another parent')
        index = sources[selection['filing_index_source_id']]
        base = exhibit.url.rsplit('/', 1)[0] + '/'
        if not index.url.startswith(base) or not index.url.endswith('-index.html'):
            raise ValueError('Subsidiary exhibit index belongs to another accession')
        linked = False
        for tr in BeautifulSoup(index.payload, 'html.parser').find_all('tr'):
            if selection['document_type'] not in [td.get_text(' ', strip=True) for td in tr.find_all('td')]:
                continue
            for link in tr.find_all('a', href=True):
                target = urlsplit(urljoin(index.url, link['href']))
                if (target.hostname == 'www.sec.gov'
                        and target.path.rsplit('/', 2)[-2:] == urlsplit(exhibit.url).path.rsplit('/', 2)[-2:]):
                    linked = True
        if not linked:
            raise ValueError('Subsidiary exhibit is not linked with the declared document type')
        soup = BeautifulSoup(exhibit.payload, 'html.parser')
        text = _text(soup.get_text(' ', strip=True))
        heading, row, date_quote = (selection[k] for k in ('heading', 'row_text', 'date_quote'))
        if not all(_text(part) in text for part in (heading, row, date_quote)):
            raise ValueError('Subsidiary projection is not literal source text')
        if (not re.search(r'\bsubsidiar(?:y|ies)\b', heading, re.I)
                or re.search(r'\bjoint ventures?\b|\bregistrants\b', heading, re.I)):
            raise ValueError('Ambiguous subsidiary heading')
        matched = [tr for tr in soup.find_all('tr') if _text(tr.get_text(' ', strip=True)) == row]
        if len(matched) != 1 or not re.search(re.escape(child['legal_name']) + r'(?![\w])', row, re.I):
            raise ValueError('Ambiguous or mismatched legal subsidiary row')
        cells = [_text(td.get_text(' ', strip=True)) for td in matched[0].find_all('td', recursive=False)]
        name_cell = next((cell for cell in cells if cell and not cell.isdecimal()), '')
        if not re.fullmatch(re.escape(child['legal_name']) + r'\.?', name_cell, re.I):
            raise ValueError('Subsidiary name must occupy its own exact legal-name cell')
        day = datetime.strptime(date_quote, selection['date_format']).date()
        if day.isoformat() != selection['as_reported_date'] or day > exhibit.published_at.date():
            raise ValueError('Invalid as-reported subsidiary date')
        known = clock(selection['known_at'])
        if max(child_source.observed_at, exhibit.observed_at, index.observed_at, clock(parent['known_at'])) > known:
            raise ValueError('Subsidiary evidence cannot precede knowledge')
        legal_evidence = dict(purpose='ENTITY_IDENTITY', url=child_source.url,
            locator='Inline XBRL own registrant name/CIK; context ' + child['context_ref'],
            publisher='SEC EDGAR', quote=f"{child['legal_name']} CIK {child['cik']}",
            published_at=child_source.published_at.isoformat(), observed_at=child_source.observed_at.isoformat(),
            sha256=child_source.sha256, representation='VALIDATED_INLINE_XBRL_FACTS',
            fact_projection={k: child[k] for k in ('legal_name', 'cik', 'context_ref')})
        relation = dict(purpose='ENTITY_TO_ISSUER', url=exhibit.url,
            locator='Reviewed Exhibit 21 heading, date and exact subsidiary row', publisher='SEC EDGAR',
            quote=heading + ' [...] ' + date_quote + ' [...] ' + row,
            published_at=exhibit.published_at.isoformat(), observed_at=exhibit.observed_at.isoformat(),
            sha256=exhibit.sha256, representation='SEC_EXHIBIT_21_ROW', exhibit_row=dict(
                parent_legal_name=parent['legal_name'], parent_cik=parent['issuer_cik'],
                child_legal_name=child['legal_name'], relationship_scope='SUBSIDIARY',
                document_type=selection['document_type'], row_text=row))
        records.append(dict(record_id='sec-child-' + child['cik'], disclosed_name=child['legal_name'],
            legal_name=child['legal_name'], relationship_role='SUBSIDIARY_OF_ISSUER',
            entity_identifier=dict(scheme='CIK', authority='SEC', value=child['cik']),
            issuer_ticker=parent['issuer_ticker'], issuer_cik=parent['issuer_cik'],
            issuer_legal_name=parent['legal_name'], effective_from=day.isoformat(),
            effective_to=(day + timedelta(days=1)).isoformat(), known_at=known.isoformat(),
            evidence=[legal_evidence, next(e for e in parent['evidence'] if e['purpose'] == 'ISSUER_IDENTITY'), relation],
            scope='AS_REPORTED_ONLY; no uninterrupted ownership or parent financial attribution'))
    return DatedCounterpartyIdentityRegistry(records).records()
