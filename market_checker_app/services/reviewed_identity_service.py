"""Primary-sourced issuer decisions, gated by effective and knowledge clocks."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from market_checker_app.utils.entity_identifiers import normalize_cik

DEFAULT_EVENTS = Path(__file__).resolve().parents[1] / 'data/reviewed_identity_events.json'


def clock(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Reviewed identity needs timezone-aware clocks')
    return result.astimezone(timezone.utc)


class ReviewedIdentityRegistry:
    def __init__(self, records):
        if not isinstance(records, list) or len(records) > 1000:
            raise ValueError('Invalid reviewed identity records')
        self.records = deepcopy(records)
        seen = set()
        for row in self.records:
            for key in ['id', 'ticker', 'name', 'current_symbol']:
                if not isinstance(row.get(key), str) or not row[key].strip() or len(row[key]) > 1000:
                    raise ValueError('Invalid reviewed identity field')
            for key in ['ticker','current_symbol']:
                if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', row[key]):
                    raise ValueError('Invalid reviewed identity symbol')
            if row['ticker'] in seen or row['kind'] not in {'ISSUER_SUCCESSION', 'INSTRUMENT_ENDED', 'OBSERVED_SYMBOL_BINDING'}:
                raise ValueError('Ambiguous reviewed identity scope')
            seen.add(row['ticker'])
            for key in ['cik', 'previous_cik']:
                if normalize_cik(row[key]) != row[key]:
                    raise ValueError('Invalid reviewed CIK')
            known = clock(row['known_at']);clock(row['effective_from'])
            if row['kind'] in {'ISSUER_SUCCESSION','INSTRUMENT_ENDED'}:
                closed=clock(row['close_prior_at'])
                if row['time_basis']=='DATE_ONLY_NEXT_DAY':
                    event_day=clock(row['event_date']+'T00:00:00+00:00')
                    if closed!=event_day or clock(row['effective_from'])!=event_day+timedelta(days=1):
                        raise ValueError('Date-only event must exclude the uncertain transition day')
                elif closed>clock(row['effective_from']):
                    raise ValueError('Prior issuer cannot overlap its reviewed successor')
            if row['instrument_status'] not in {'ACTIVE', 'INACTIVE'}:
                raise ValueError('Invalid instrument status')
            if row['kind'] == 'INSTRUMENT_ENDED' and row['instrument_status'] != 'INACTIVE':
                raise ValueError('Ended instrument cannot be active')
            if row['kind'] == 'ISSUER_SUCCESSION' and (
                    row['previous_cik'] == row['cik'] or row['instrument_status'] != 'ACTIVE'):
                raise ValueError('Issuer succession needs different legal issuers')
            if row['kind'] == 'OBSERVED_SYMBOL_BINDING' and (
                    row['previous_cik'] != row['cik'] or row['instrument_status'] != 'ACTIVE'
                    or clock(row['effective_from']) != known):
                raise ValueError('Observed symbol binding must not infer a historical interval')
            if row.get('parent_entity_id') is not None:
                value=row['parent_entity_id'].removeprefix('cik:')
                if row['parent_entity_id'] != 'cik:'+str(normalize_cik(value)):
                    raise ValueError('Invalid parent legal entity identifier')
            for key in ['primary_url','close_prior_url']:
                url = urlsplit(row[key])
                cik = row['cik'] if key == 'primary_url' else row['previous_cik']
                if (url.scheme != 'https' or url.hostname != 'www.sec.gov' or url.username
                        or url.password or url.port not in {None,443} or url.query or url.fragment
                        or not url.path.startswith(f'/Archives/edgar/data/{int(cik)}/')):
                    raise ValueError('Reviewed decision needs its own primary SEC filing')
            if not isinstance(row['evidence'], list) or len(row['evidence']) < 2:
                raise ValueError('Reviewed decision needs independent primary evidence')
            for evidence in row['evidence']:
                parsed=urlsplit(evidence['url'])
                if (parsed.scheme != 'https' or parsed.hostname != 'www.sec.gov'
                        or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or not parsed.path.startswith('/Archives/edgar/data/')):
                    raise ValueError('Evidence must be a primary SEC document')
                if not re.fullmatch('[0-9a-f]{64}', evidence['sha256']):
                    raise ValueError('Evidence must retain its original checksum')
                if clock(evidence['observed_at']) > known:
                    raise ValueError('Future reviewed evidence')

    @classmethod
    def load(cls, path=DEFAULT_EVENTS):
        with Path(path).open('rb') as stream:
            body = stream.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError('Reviewed identity file exceeds bound')
        data = json.loads(body)
        if data.get('schema_version') != 1:
            raise ValueError('Invalid reviewed identity schema')
        return cls(data['records'])

    def decision(self, ticker, *, as_of):
        when = clock(as_of)
        for row in self.records:
            if row['ticker'] == ticker and clock(row['known_at']) <= when and clock(row['effective_from']) <= when:
                if row['kind'] == 'OBSERVED_SYMBOL_BINDING':
                    # An observation does not authorize this symbol forever.
                    # A fresh independent SEC catalog must still bind it to
                    # the same issuer; reuse or stale data remains unresolved.
                    from market_checker_app.services.issuer_catalog_service import IssuerCatalog
                    current = IssuerCatalog.load(as_of=when).lookup(row['current_symbol'], as_of=when)
                    if not current or current['cik'] != row['cik']:
                        return None
                return deepcopy(row)
        return None

    def provider_symbol(self, ticker, *, as_of):
        row = self.decision(ticker, as_of=as_of)
        return row['current_symbol'] if row and row['instrument_status'] == 'ACTIVE' else ticker


def apply_decision(entity, decision):
    """Apply a reviewed binding without carrying old issuer identifiers across."""
    from dataclasses import replace
    metadata = dict(entity.metadata)
    metadata.update(reviewed_identity_event=decision['id'],
                    reviewed_identity_known_at=decision['known_at'],
                    reviewed_identity_effective_from=decision['effective_from'],
                    instrument_status=decision['instrument_status'],
                    current_trading_symbol=decision['current_symbol'],
                    identity_resolution='INACTIVE' if decision['instrument_status']=='INACTIVE' else 'RESOLVED',
                    instrument_identifier_scope='issuer/ticker; no inferred CUSIP, ISIN or LEI',
                    name_matching_used=False)
    succession = decision['kind']=='ISSUER_SUCCESSION'
    ended = decision['kind']=='INSTRUMENT_ENDED'
    if succession or ended:
        metadata['close_previous_identity'] = {'ticker':decision['ticker'],
            'previous_cik':decision['previous_cik'], 'valid_to':decision['close_prior_at'],
            'event_id':decision['id']}
    return replace(entity,
        # The listing key stays stable; separate issuer IDs and immutable
        # versions distinguish legal successors without duplicating a ticker.
        entity_id=entity.entity_id,
        cik=decision['cik'], name=decision['name'],
        legal_entity_id='cik:'+decision['cik'], issuer_id='cik:'+decision['cik'],
        instrument_id=f"issuer:{decision['cik']}:ticker:{decision['current_symbol']}",
        isin=None if succession else entity.isin, lei=None if succession else entity.lei,
        yahoo_ticker=decision['current_symbol'],
        parent_entity_id=decision['parent_entity_id'],
        source='reviewed_primary_identity', source_url=decision['primary_url'],
        valid_from=clock(decision['effective_from']), valid_to=None,
        confidence=1.0, metadata=metadata)
