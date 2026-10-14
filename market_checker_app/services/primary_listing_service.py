"""Validate one requested SEC ticker/class context without mixing securities."""
from datetime import date
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup
from market_checker_app.utils.symbols import normalize_yahoo_symbol
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.services.sec13f_identity_bulk import official_url

DEFAULT_BINDINGS=Path(__file__).resolve().parents[1]/'data/primary_listing_bindings.json'


class PrimaryListingRegistry:
    """Reviewed, exact as-reported classes, corroborated by a fresh catalog.

    A cover observation is not an effective-date history or a CUSIP/ISIN
    mapping. Keep its reporting date and knowledge time visible to consumers.
    """
    def __init__(self, data):
        if data.get('schema_version') != 1 or data.get('scope') != 'AS_REPORTED_REGISTERED_CLASS':
            raise ValueError('Invalid primary listing registry scope')
        self.records={}
        for row in data['records']:
            ticker=row['canonical_ticker']
            if ticker in self.records or not re.fullmatch('[A-Z0-9.-]{1,15}',ticker):
                raise ValueError('Duplicate or invalid primary listing ticker')
            if not re.fullmatch('[0-9]{10}',row['issuer_cik']) or int(row['issuer_cik'])==0:
                raise ValueError('Invalid primary listing issuer')
            official_url('ticker_filing',row['source_url'])
            if urlsplit(row['source_url']).path.split('/')[4].zfill(10)!=row['issuer_cik']:
                raise ValueError('Primary listing source belongs to another issuer')
            if (not re.fullmatch('[0-9a-f]{64}',row['sha256'])
                    or not row['class_title'].strip() or len(row['class_title'])>2000
                    or not row['context_ref'] or _symbol(row['as_filed_ticker'])!=row['ticker']):
                raise ValueError('Invalid primary class evidence')
            known=clock(row['known_at']);published=clock(row['published_at'])
            if published>known or date.fromisoformat(row['anchor_date'])>published.date():
                raise ValueError('Future primary class evidence')
            metadata={'kind':'ticker_filing','url':row['source_url'],'sha256':row['sha256'],
                      'observed_at':known.isoformat(),'published_at':published.isoformat()}
            if hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()!=row['snapshot_id']:
                raise ValueError('Primary source fingerprint mismatch')
            context=row.get('context_source')
            if context:
                official_url('ticker_filing',context['url'])
                if (context['url'].rsplit('/',1)[0]!=row['source_url'].rsplit('/',1)[0]
                        or clock(context['published_at'])!=published
                        or clock(context['observed_at'])>known
                        or not re.fullmatch('[0-9a-f]{64}',context['sha256'])):
                    raise ValueError('Unrelated or future primary context evidence')
            self.records[ticker]=deepcopy(row)

    @classmethod
    def load(cls, path=None):
        selected=path or os.getenv('JOHNY_SKORE_PRIMARY_LISTING_REGISTRY') or DEFAULT_BINDINGS
        with Path(selected).open('rb') as stream:body=stream.read(2_000_001)
        if len(body)>2_000_000:raise ValueError('Unbounded primary listing registry')
        return cls(json.loads(body))

    def lookup(self, canonical_ticker, *, symbol, cik, as_of, catalog):
        row=self.records.get(canonical_ticker)
        when=clock(as_of)
        if not row or clock(row['known_at'])>when or row['issuer_cik']!=cik or row['ticker']!=_symbol(symbol):
            return None
        binding=catalog.lookup(symbol,as_of=when)
        if not binding or binding['cik']!=cik:return None
        # Fresh issuer evidence is necessary, but never extends the cover's
        # class observation indefinitely or retroactively.
        from datetime import timedelta
        if when>=clock(row['known_at'])+timedelta(days=7):return None
        return deepcopy(row)


def _symbol(value):
    text=value.strip()
    # Some SEC covers explicitly include the venue in TradingSymbol.
    # Parse this exact published syntax, never a fuzzy ticker/name match.
    annotated=re.fullmatch(r'([A-Z0-9./-]+)\s*\((NYSE|NASDAQ|NYSE AMERICAN)\)',text)
    return normalize_yahoo_symbol(annotated[1] if annotated else text).replace('/','-')


def parse_requested_listing(snapshot, symbol, *, context_snapshot=None):
    """Return an exact primary binding; unrelated debt facts grant no credit.

    Unlike the complete 13F-document validator, this validates the requested
    security's contexts. It never imports other symbols, hidden continuations,
    nil values, conflicting classes or a different registrant CIK.
    """
    if snapshot.kind != 'ticker_filing' or re.search(br'<!\s*ENTITY\b',snapshot.payload,re.I):
        raise ValueError('Invalid primary listing source')
    soup=BeautifulSoup(snapshot.payload,'html.parser')
    def namespace(node,prefix,pattern):
        for parent in [node,*node.parents]:
            value=getattr(parent,'attrs',{}).get('xmlns:'+prefix)
            if value is not None:
                if not re.fullmatch(pattern,value):raise ValueError('Wrong primary namespace')
                return
        raise ValueError('Missing primary namespace')
    def content(node):
        if node.get('continuedat') or str(node.get('xsi:nil','')).lower() in {'true','1'}:
            raise ValueError('Unresolved requested fact')
        # Only same-concept nested facts are allowed; they must independently
        # validate their own contexts. Excluded footnotes are not fact values.
        for child in node.find_all('ix:nonnumeric'):
            if child.get('name','').lower()!=node.get('name','').lower():
                raise ValueError('Nested different primary concept')
        from copy import deepcopy
        copied=deepcopy(node)
        for excluded in copied.find_all('ix:exclude'):excluded.decompose()
        text=' '.join(copied.get_text(' ',strip=True).split())
        if not text or len(text)>2000:raise ValueError('Invalid primary fact value')
        return text
    context_nodes=soup.find_all('xbrli:context')
    if context_snapshot is not None:
        # An exhibit can refer to the cover's XBRL contexts. Accept only a
        # direct, published link within the same issuer and accession folder.
        if (context_snapshot.kind != 'ticker_filing'
                or context_snapshot.url.rsplit('/',1)[0] != snapshot.url.rsplit('/',1)[0]
                or context_snapshot.published_at != snapshot.published_at
                or re.search(br'<!\s*ENTITY\b',context_snapshot.payload,re.I)):
            raise ValueError('Unrelated primary context document')
        parent=BeautifulSoup(context_snapshot.payload,'html.parser')
        if snapshot.url not in {urljoin(context_snapshot.url,a['href']) for a in parent.find_all('a',href=True)}:
            raise ValueError('Exhibit is not linked by its primary cover')
        if not context_nodes:
            context_nodes=parent.find_all('xbrli:context')
    contexts={}
    for node in context_nodes:
        identifier=node.get('id')
        if not identifier or identifier in contexts:raise ValueError('Duplicate primary context')
        contexts[identifier]=node
    target=_symbol(symbol)
    matches=[]
    archive_cik=urlsplit(snapshot.url).path.split('/')[4].zfill(10)
    facts=soup.find_all('ix:nonnumeric')
    for fact in facts:
        if fact.get('name','').lower()!='dei:tradingsymbol':continue
        # Nil/continued unrelated securities stay outside the selected scope.
        if fact.get('continuedat') or str(fact.get('xsi:nil','')).lower() in {'true','1'}:continue
        if _symbol(content(fact))!=target:continue
        namespace(fact,'ix',r'http://www\.xbrl\.org/(2008|2013)/inlineXBRL')
        namespace(fact,'dei',r'http://xbrl\.sec\.gov/dei/20[0-9]{2}')
        ref=fact.get('contextref');context=contexts.get(ref)
        if context is None:raise ValueError('Missing requested context')
        namespace(context,'xbrli',r'http://www\.xbrl\.org/2003/instance')
        entities=context.find_all('xbrli:entity',recursive=False)
        periods=context.find_all('xbrli:period',recursive=False)
        if len(entities)!=1 or len(periods)!=1:raise ValueError('Malformed requested context')
        ids=entities[0].find_all('xbrli:identifier',recursive=False)
        if (len(ids)!=1 or ids[0].get('scheme')!='http://www.sec.gov/CIK'
                or not re.fullmatch('[0-9]{1,10}',ids[0].get_text(strip=True))
                or ids[0].get_text(strip=True).zfill(10)!=archive_cik):
            raise ValueError('Requested listing belongs to another issuer')
        anchors=periods[0].find_all(['xbrli:instant','xbrli:enddate'],recursive=False)
        if len(anchors)!=1:raise ValueError('Ambiguous requested period')
        anchor=anchors[0].get_text(strip=True)
        if not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',anchor):raise ValueError('Invalid requested period')
        starts=periods[0].find_all('xbrli:startdate',recursive=False)
        if ((anchors[0].name=='xbrli:instant' and starts)
                or (anchors[0].name=='xbrli:enddate' and (len(starts)!=1 or date.fromisoformat(starts[0].get_text(strip=True))>date.fromisoformat(anchor)))
                or date.fromisoformat(anchor)>snapshot.published_at.date()):
            raise ValueError('Future or inconsistent requested period')
        titles=[f for f in facts if f.get('name','').lower()=='dei:security12btitle' and f.get('contextref')==ref]
        if not titles:raise ValueError('Requested class missing')
        values=set()
        for title in titles:
            namespace(title,'ix',r'http://www\.xbrl\.org/(2008|2013)/inlineXBRL')
            namespace(title,'dei',r'http://xbrl\.sec\.gov/dei/20[0-9]{2}')
            values.add(content(title))
        if len(values)!=1:raise ValueError('Conflicting requested classes')
        title=next(iter(values))
        # The canonical watchlist is equity-only. The same bare ticker can
        # appear for unrelated notes or preferred securities in a cover.
        if re.search(r'\b(NOTES?|DEBENTURES?|BONDS?|DEBT|PREFERRED|PFD|WARRANTS?|RIGHTS?|CALL|PUT)\b',title,re.I):
            continue
        matches.append({'ticker':target,'as_filed_ticker':content(fact),'issuer_cik':archive_cik,'class_title':title,
            'anchor_date':anchor,'context_ref':ref,'snapshot_id':snapshot.snapshot_id})
    if not matches or len({m['class_title'] for m in matches})!=1:
        raise ValueError('Missing or ambiguous requested listing')
    result=max(matches,key=lambda m:(m['anchor_date'],m['context_ref']))
    if context_snapshot is not None:
        result['context_source']=context_snapshot.metadata()
    return result
