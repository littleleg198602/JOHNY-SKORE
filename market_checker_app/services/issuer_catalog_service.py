"""Contemporary SEC issuer bindings; never infer historical share identities."""
import base64
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.utils.symbols import normalize_yahoo_symbol

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / 'data/sec_issuer_catalog_snapshot.json'
SOURCE_URL = 'https://www.sec.gov/files/company_tickers.json'
MAX_BYTES = 4_000_000


class IssuerCatalog:
    def __init__(self, capture):
        if capture.get('schema_version') != 1 or capture.get('source_url') != SOURCE_URL:
            raise ValueError('Unexpected issuer catalog source')
        self.known_at = clock(capture['observed_at'])
        body = base64.b64decode(capture['payload_base64'], validate=True)
        if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != capture['sha256']:
            raise ValueError('Issuer catalog checksum mismatch')
        self.sha256 = capture['sha256']
        payload = json.loads(body)
        if not isinstance(payload, dict) or not 0 < len(payload) <= 100000:
            raise ValueError('Invalid issuer catalog')
        self.by_symbol = {}
        for row in payload.values():
            if (not isinstance(row, dict) or not isinstance(row.get('ticker'), str)
                    or not re.fullmatch('[A-Z0-9./-]{1,15}', row['ticker'])
                    or not isinstance(row.get('title'), str) or not row['title'].strip()
                    or not re.fullmatch('[0-9]{1,10}', str(row.get('cik_str','')))
                    or int(row['cik_str']) == 0):
                raise ValueError('Invalid issuer catalog row')
            key=row['ticker'].replace('.','-').replace('/','-')
            value={'cik':str(row['cik_str']).zfill(10),'name':row['title']}
            if value not in self.by_symbol.setdefault(key,[]):
                self.by_symbol[key].append(value)

    @classmethod
    def load(cls, path=None, *, as_of=None):
        local = Path('outputs/sec_issuer_catalog_snapshot.json')
        explicit = path or os.getenv('JOHNY_SKORE_ISSUER_CATALOG_SNAPSHOT')
        selected = explicit or (local if local.exists() else DEFAULT_CATALOG)
        def read(candidate):
            with Path(candidate).open('rb') as stream:
                body=stream.read(6_000_001)
            if len(body)>6_000_000:
                raise ValueError('Unbounded issuer catalog capture')
            return cls(json.loads(body))
        current=read(selected)
        if as_of is None or explicit or current.known_at <= clock(as_of):
            return current
        # A refreshed pointer must not erase the capture used by an earlier
        # task. Historical contexts may select a retained, already-known copy.
        archives=list((local.parent/'issuer_catalog_archive').glob('*.json'))
        if len(archives)>100:
            raise ValueError('Select an explicit immutable capture for long historical replay')
        best=None
        for candidate in [DEFAULT_CATALOG,*archives]:
            item=read(candidate)
            if (best is not None and item.known_at == best.known_at
                    and item.known_at <= clock(as_of) and item.sha256 != best.sha256):
                raise ValueError('Ambiguous issuer captures at the same observation time')
            if item.known_at<=clock(as_of) and (best is None or item.known_at>best.known_at):
                best=item
        return best or current

    def lookup(self, ticker, *, as_of):
        when=clock(as_of)
        # A stale capture is not proof of today's binding; refresh explicitly.
        if when < self.known_at or when >= self.known_at + timedelta(days=7):
            return None
        key=normalize_yahoo_symbol(ticker).replace('.','-').replace('/','-')
        rows=self.by_symbol.get(key,[])
        if len(rows) != 1:
            return None
        return dict(rows[0],source_url=SOURCE_URL,known_at=self.known_at.isoformat(),
                    source_sha256=self.sha256,scope='CURRENT_ISSUER_ONLY_NOT_SHARE_CLASS')
