"""Capture the official Nasdaq directory without changing production identities."""
import base64
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from urllib.request import Request,urlopen

from market_checker_app.services.nasdaq_listing_service import (
    NasdaqListingRegistry,SOURCE_URL,DEFAULT_CAPTURE,MAX_BYTES,
)
from market_checker_app.services.sec13f_identity_bulk import atomic_json,guard_production_write


def capture(*,output=DEFAULT_CAPTURE,transport=None):
    output=Path(output);guard_production_write(output)
    if transport is None:
        def transport(url):
            with urlopen(Request(url,headers={'User-Agent':'JohnySkore/2.1'}),timeout=30) as response:
                if response.url!=SOURCE_URL:raise ValueError('Unexpected exchange directory redirect')
                return response.read(MAX_BYTES+1)
    body=transport(SOURCE_URL)
    data={'schema_version':1,'source_url':SOURCE_URL,
          'observed_at':datetime.now(timezone.utc).isoformat(),
          'sha256':hashlib.sha256(body).hexdigest(),'payload_base64':base64.b64encode(body).decode()}
    NasdaqListingRegistry(data)
    archive=output.parent/'nasdaq_listing_archive'
    captures=[data]
    if NasdaqListingRegistry.load_optional(output) is not None:
        captures.append(json.loads(output.read_text()))
    for item in captures:
        path=archive/(item['sha256']+'-'+hashlib.sha256(item['observed_at'].encode()).hexdigest()[:12]+'.json')
        if path.exists() and json.loads(path.read_text())!=item:
            raise ValueError('Immutable exchange capture collision')
        if not path.exists():atomic_json(path,item)
    atomic_json(output,data)
    return {'status':'SAVED','observed_at':data['observed_at'],'sha256':data['sha256']}


if __name__=='__main__':
    print(json.dumps(capture()))
