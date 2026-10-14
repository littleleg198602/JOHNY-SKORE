"""Capture exact SEC registered-class observations for explicitly selected tickers.

Writes review artifacts and immutable primary bytes under outputs, never the
production registry. Supply JOHNY_SKORE_SEC_USER_AGENT through the environment.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from market_checker_app.collectors.sec_edgar_client import (
    SecEdgarClient, SEC_SUBMISSIONS_URL, SecAccessBlockedError, SecRateLimitedError, SecEdgarError,
)
from market_checker_app.services.issuer_catalog_service import IssuerCatalog
from market_checker_app.services.reviewed_identity_service import ReviewedIdentityRegistry, clock
from market_checker_app.services.primary_listing_service import parse_requested_listing, PrimaryListingRegistry
from market_checker_app.services.sec13f_identity_bulk import (
    SourceSnapshot, atomic_json, save_snapshot, guard_production_write, official_url,
)
from market_checker_app.utils.ticker_universe import load_canonical_tickers


def _proof(snapshot, symbol, *, parent=None):
    result=parse_requested_listing(snapshot,symbol,context_snapshot=parent)
    return dict(result,source_url=snapshot.url,sha256=snapshot.sha256,
                known_at=snapshot.observed_at.isoformat(),published_at=snapshot.published_at.isoformat())


def capture_one(ticker, *, catalog, reviewed, client, workspace, as_of=None):
    when=clock(as_of or datetime.now(timezone.utc))
    event=reviewed.decision(ticker,as_of=when)
    if event and event['instrument_status']=='INACTIVE':
        return {'canonical_ticker':ticker,'status':'INACTIVE','event_id':event['id']}
    symbol=event['current_symbol'] if event else ticker
    binding=catalog.lookup(symbol,as_of=when)
    if not binding or (event and event['cik']!=binding['cik']):
        return {'canonical_ticker':ticker,'status':'WAIT_CURRENT_ISSUER'}
    cik=binding['cik']
    data=client._request_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    recent=data['filings']['recent']
    filings=client._parse_filings(data,cik=cik,allowed_forms=('8-K','10-Q','20-F','6-K'),limit=6)
    attempts=[]
    for filing in filings:
        index=recent['accessionNumber'].index(filing.accession_number)
        published=clock(recent['acceptanceDateTime'][index].replace('Z','+00:00'))
        try:
            body=client._request_bytes(filing.filing_url)
            parent=SourceSnapshot('ticker_filing',filing.filing_url,datetime.now(timezone.utc),body,published)
            save_snapshot(workspace,parent)
            try:
                proof=_proof(parent,symbol)
            except ValueError as error:
                attempts.append({'url':parent.url,'reason':str(error)})
                proof=None
                soup=BeautifulSoup(body,'html.parser')
                # Follow only explicit cover references to the registered
                # securities exhibit, within this exact accession directory.
                urls=[]
                for anchor in soup.find_all('a',href=True):
                    text=' '.join(anchor.get_text(' ',strip=True).split()).lower()
                    if ('securities registered pursuant to section 12(b)' not in text
                            and text!='see exhibit 99.3'):continue
                    url=urljoin(parent.url,anchor['href'])
                    if url.rsplit('/',1)[0]!=parent.url.rsplit('/',1)[0]:continue
                    official_url('ticker_filing',url)
                    if url not in urls:urls.append(url)
                for url in urls[:3]:
                    try:
                        exhibit_body=client._request_bytes(url)
                        exhibit=SourceSnapshot('ticker_filing',url,datetime.now(timezone.utc),exhibit_body,published)
                        save_snapshot(workspace,exhibit)
                        proof=_proof(exhibit,symbol,parent=parent)
                        break
                    except (SecAccessBlockedError,SecRateLimitedError):raise
                    except (ValueError,SecEdgarError) as error:attempts.append({'url':url,'reason':str(error)})
            if proof:
                return dict(proof,canonical_ticker=ticker,status='VALIDATED',attempts=attempts)
        except (SecAccessBlockedError,SecRateLimitedError):raise
        except (ValueError,SecEdgarError) as error:attempts.append({'url':filing.filing_url,'reason':str(error)})
    return {'canonical_ticker':ticker,'issuer_cik':cik,'status':'UNRESOLVED' if filings else 'NO_PRIMARY_FILING','attempts':attempts}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tickers',required=True,help='Comma-separated canonical watchlist keys')
    parser.add_argument('--workspace',type=Path,default=Path('outputs/primary_listing_capture'))
    args=parser.parse_args()
    tickers=list(dict.fromkeys(t.strip().upper() for t in args.tickers.split(',')))
    if not tickers or len(tickers)>687 or set(tickers)-set(load_canonical_tickers()):
        parser.error('Select existing canonical watchlist tickers')
    identity=os.getenv('JOHNY_SKORE_SEC_USER_AGENT','')
    if '@' not in identity:parser.error('SEC contact environment variable is required')
    guard_production_write(args.workspace)
    args.workspace.mkdir(parents=True,exist_ok=True)
    catalog=IssuerCatalog.load();reviewed=ReviewedIdentityRegistry.load()
    client=SecEdgarClient(user_agent=identity,min_request_interval_seconds=.25,max_attempts=1)
    records=[];unresolved=[];inactive=[]
    for ticker in tickers:
        record=capture_one(ticker,catalog=catalog,reviewed=reviewed,client=client,workspace=args.workspace)
        atomic_json(args.workspace/(ticker+'.json'),record)
        if record['status']=='VALIDATED':
            records.append({k:v for k,v in record.items() if k not in {'status','attempts'}})
        elif record['status']=='INACTIVE':inactive.append(ticker)
        else:unresolved.append({'ticker':ticker,'reason':record['status']})
        data={'schema_version':1,'scope':'AS_REPORTED_REGISTERED_CLASS',
              'records':records,'unresolved':unresolved,'inactive':inactive}
        PrimaryListingRegistry(data)
        atomic_json(args.workspace/'bindings.json',data)
        print(json.dumps({'ticker':ticker,'status':record['status']}),flush=True)


if __name__=='__main__':main()
