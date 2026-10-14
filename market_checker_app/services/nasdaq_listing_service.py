"""Exact official exchange class observations, separate from SEC covers."""
import base64
import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import re

from market_checker_app.services.reviewed_identity_service import clock

SOURCE_URL='https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'
DEFAULT_CAPTURE=Path('outputs/nasdaq_listing_snapshot.json')
MAX_BYTES=8_000_000
HEADER=['Symbol','Security Name','Market Category','Test Issue','Financial Status','Round Lot Size','ETF','NextShares']


class NasdaqListingRegistry:
    def __init__(self,capture):
        if capture.get('schema_version')!=1 or capture.get('source_url')!=SOURCE_URL:
            raise ValueError('Unexpected exchange listing source')
        self.known_at=clock(capture['observed_at']);self.sha256=capture['sha256']
        body=base64.b64decode(capture['payload_base64'],validate=True)
        if not 0<len(body)<=MAX_BYTES or hashlib.sha256(body).hexdigest()!=self.sha256:
            raise ValueError('Exchange directory checksum mismatch')
        rows=list(csv.reader(io.StringIO(body.decode('utf-8-sig')),delimiter='|'))
        if len(rows)<3 or rows[0]!=HEADER or len(rows)>100000:
            raise ValueError('Invalid exchange directory schema')
        footer=rows[-1][0]
        match=re.fullmatch(r'File Creation Time: ([0-9]{8})[0-9:]{4,5}',footer)
        if not match or any(rows[-1][1:]):raise ValueError('Missing exchange directory footer')
        self.publisher_date=datetime.strptime(match[1],'%m%d%Y').date()
        if not self.known_at.date()-timedelta(days=7)<=self.publisher_date<=self.known_at.date():
            raise ValueError('Future or stale publisher directory date')
        self.rows={}
        for row in rows[1:-1]:
            if len(row)!=len(HEADER) or not re.fullmatch('[A-Z0-9.-]{1,15}',row[0]):
                raise ValueError('Malformed exchange listing row')
            self.rows.setdefault(row[0],[]).append(dict(zip(HEADER,row)))

    @classmethod
    def load_optional(cls,path=DEFAULT_CAPTURE):
        path=Path(path)
        if not path.exists():return None
        with path.open('rb') as stream:body=stream.read(12_000_001)
        if len(body)>12_000_000:raise ValueError('Unbounded exchange capture')
        return cls(json.loads(body))

    def lookup(self,symbol,*,cik,as_of,catalog):
        when=clock(as_of)
        if not self.known_at<=when<self.known_at+timedelta(days=7):return None
        rows=self.rows.get(symbol,[])
        if len(rows)!=1:return None
        row=rows[0]
        # This fallback is for the independently reviewed direct-bank case,
        # never a general name-to-issuer matcher or preferred-share inference.
        if (symbol!='OZK' or cik!='0001569650'
                or row['Security Name']!='Bank OZK - Common Stock'
                or row['Test Issue']!='N' or row['ETF']!='N' or row['NextShares']!='N'):
            return None
        issuer=catalog.lookup(symbol,as_of=when)
        if not issuer or issuer['cik']!=cik:return None
        return {'ticker':symbol,'issuer_cik':cik,'class_title':row['Security Name'],
            'anchor_date':self.publisher_date.isoformat(),'known_at':self.known_at.isoformat(),
            'source_url':SOURCE_URL,'sha256':self.sha256,'snapshot_id':self.sha256,
            'context_ref':'Symbol='+symbol,'scope':'AS_REPORTED_EXCHANGE_LISTED_CLASS'}
