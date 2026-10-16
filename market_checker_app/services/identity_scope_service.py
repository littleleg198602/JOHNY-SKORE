"""Explicit, knowledge-dated user exclusions; retain the canonical history."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.utils.ticker_universe import load_canonical_tickers

DEFAULT_SCOPE=Path(__file__).resolve().parents[1]/'data/identity_specialist_scope.json'


class IdentityScope:
    def __init__(self,data):
        if (data.get('schema_version')!=1 or data.get('specialist')!='identity'
                or data.get('canonical_universe_unchanged') is not True):
            raise ValueError('Invalid identity specialist scope')
        canonical=set(load_canonical_tickers());self.exclusions={}
        for row in data['exclusions']:
            ticker=row['ticker'];clock(row['known_at'])
            if ticker not in canonical or ticker in self.exclusions or not str(row['reason']).strip():
                raise ValueError('Invalid or duplicate identity scope exclusion')
            self.exclusions[ticker]=deepcopy(row)

    @classmethod
    def load(cls,path=DEFAULT_SCOPE):
        with Path(path).open('rb') as stream:body=stream.read(100001)
        if len(body)>100000:raise ValueError('Unbounded identity scope')
        return cls(json.loads(body))

    def exclusion(self,ticker,*,as_of):
        row=self.exclusions.get(ticker)
        return deepcopy(row) if row and clock(row['known_at'])<=clock(as_of) else None

    def report(self,tickers,*,as_of):
        excluded={t:self.exclusion(t,as_of=as_of) for t in tickers if self.exclusion(t,as_of=as_of)}
        included=[t for t in tickers if t not in excluded]
        return {'included':included,'excluded':excluded,'included_count':len(included),
            'excluded_count':len(excluded),'scope_sha256':hashlib.sha256(json.dumps(excluded,sort_keys=True).encode()).hexdigest()}
