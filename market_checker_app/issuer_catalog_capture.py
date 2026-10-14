"""Refresh the current SEC issuer capture and retain immutable prior copies."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from market_checker_app.collectors.sec_edgar_client import SecEdgarClient
from market_checker_app.services.issuer_catalog_service import IssuerCatalog, SOURCE_URL, DEFAULT_CATALOG
from market_checker_app.services.sec13f_identity_bulk import atomic_json, guard_production_write


def capture(*, output=Path('outputs/sec_issuer_catalog_snapshot.json'), client=None):
    output=Path(output)
    guard_production_write(output)
    if output.suffix != '.json':
        raise ValueError('Issuer capture must use a JSON file')
    if client is None:
        identity=os.getenv('JOHNY_SKORE_SEC_USER_AGENT','')
        if '@' not in identity:
            raise ValueError('SEC contact is required')
        client=SecEdgarClient(user_agent=identity,max_attempts=2)
    payload=client._request_bytes(SOURCE_URL)
    data={'schema_version':1,'source_url':SOURCE_URL,
          'observed_at':datetime.now(timezone.utc).isoformat(),
          'sha256':hashlib.sha256(payload).hexdigest(),
          'payload_base64':base64.b64encode(payload).decode()}
    IssuerCatalog(data)
    archive=output.parent/'issuer_catalog_archive'
    archive.mkdir(parents=True,exist_ok=True)
    for old in [DEFAULT_CATALOG,output]:
        if old.exists():
            prior=json.loads(old.read_text());IssuerCatalog(prior)
            key=prior['sha256']+'-'+hashlib.sha256(prior['observed_at'].encode()).hexdigest()[:12]
            path=archive/(key+'.json')
            if path.exists() and json.loads(path.read_text())!=prior:
                raise ValueError('Immutable issuer capture collision')
            if not path.exists():atomic_json(path,prior)
    key=data['sha256']+'-'+hashlib.sha256(data['observed_at'].encode()).hexdigest()[:12]
    path=archive/(key+'.json')
    if path.exists() and json.loads(path.read_text())!=data:
        raise ValueError('Immutable issuer capture collision')
    if not path.exists():atomic_json(path,data)
    atomic_json(output,data)
    return {'status':'SAVED','sha256':data['sha256'],'observed_at':data['observed_at']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('outputs/sec_issuer_catalog_snapshot.json'))
    args=parser.parse_args()
    result=capture(output=args.output)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
