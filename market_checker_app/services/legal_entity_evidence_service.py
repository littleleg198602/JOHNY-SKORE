"""Extract legal-person evidence from that registrant's own SEC contexts."""
from datetime import date
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup


def parse_legal_entity(snapshot):
    if snapshot.kind!='ticker_filing' or re.search(br'<!\s*ENTITY\b',snapshot.payload,re.I):
        raise ValueError('Invalid legal entity source')
    soup=BeautifulSoup(snapshot.payload,'html.parser')
    def namespace(node,prefix,pattern):
        for parent in [node,*node.parents]:
            value=getattr(parent,'attrs',{}).get('xmlns:'+prefix)
            if value is not None:
                if not re.fullmatch(pattern,value):raise ValueError('Invalid legal entity namespace')
                return
        raise ValueError('Missing legal entity namespace')
    contexts={}
    for context in soup.find_all('xbrli:context'):
        key=context.get('id')
        if not key or key in contexts:raise ValueError('Ambiguous legal entity context')
        contexts[key]=context
    cik=urlsplit(snapshot.url).path.split('/')[4].zfill(10);matches=[]
    for fact in soup.find_all('ix:nonnumeric'):
        if fact.get('name','').lower()!='dei:entityregistrantname':continue
        if fact.get('continuedat') or str(fact.get('xsi:nil','')).lower() in {'true','1'}:continue
        namespace(fact,'ix',r'http://www\.xbrl\.org/(2008|2013)/inlineXBRL')
        namespace(fact,'dei',r'http://xbrl\.sec\.gov/dei/20[0-9]{2}')
        if (fact.find('ix:exclude') or any(n.get('name','').lower()!='dei:entityregistrantname'
                or n.get('continuedat') or str(n.get('xsi:nil','')).lower() in {'true','1'}
                for n in fact.find_all('ix:nonnumeric'))):raise ValueError('Unresolved legal name fact')
        context=contexts.get(fact.get('contextref'))
        if context is None:raise ValueError('Missing legal name context')
        namespace(context,'xbrli',r'http://www\.xbrl\.org/2003/instance')
        entities=context.find_all('xbrli:entity',recursive=False)
        periods=context.find_all('xbrli:period',recursive=False)
        if len(entities)!=1 or len(periods)!=1:raise ValueError('Malformed legal name context')
        ids=entities[0].find_all('xbrli:identifier',recursive=False)
        if len(ids)!=1 or ids[0].get('scheme')!='http://www.sec.gov/CIK':
            raise ValueError('Unverified legal identifier')
        value=ids[0].get_text(strip=True)
        if not re.fullmatch('[0-9]{1,10}',value):raise ValueError('Invalid legal identifier')
        keys=[node for node in soup.find_all('ix:nonnumeric')
              if node.get('name','').lower()=='dei:entitycentralindexkey'
              and node.get('contextref')==fact.get('contextref')]
        if keys:
            declared=set()
            for key in keys:
                namespace(key,'ix',r'http://www\.xbrl\.org/(2008|2013)/inlineXBRL')
                namespace(key,'dei',r'http://xbrl\.sec\.gov/dei/20[0-9]{2}')
                text=key.get_text(strip=True)
                if (key.get('continuedat') or str(key.get('xsi:nil','')).lower() in {'true','1'}
                        or not re.fullmatch('[0-9]{1,10}',text)):
                    raise ValueError('Unresolved registrant CIK fact')
                declared.add(text.zfill(10))
            if len(declared)!=1:raise ValueError('Conflicting registrant CIK facts')
            if next(iter(declared))!=cik:continue
        elif value.zfill(10)!=cik or context.find('xbrli:segment') is not None:continue
        # Co-registrant reports use the parent's reporting-context CIK.
        # Only an explicit DEI CIK/name pair in this exact same context may
        # identify a different registrant; dimensions or names alone cannot.
        anchors=periods[0].find_all(['xbrli:instant','xbrli:enddate'],recursive=False)
        if len(anchors)!=1:raise ValueError('Ambiguous legal entity date')
        anchor=anchors[0].get_text(strip=True);day=date.fromisoformat(anchor)
        starts=periods[0].find_all('xbrli:startdate',recursive=False)
        if (day>snapshot.published_at.date() or
                (anchors[0].name=='xbrli:instant' and starts) or
                (anchors[0].name=='xbrli:enddate' and (len(starts)!=1 or date.fromisoformat(starts[0].get_text(strip=True))>day))):
            raise ValueError('Future or inconsistent legal entity period')
        name=' '.join(fact.get_text(' ',strip=True).split())
        if not name or len(name)>1000:raise ValueError('Invalid legal name')
        matches.append({'legal_name':name,'cik':cik,'reporting_context_cik':value.zfill(10),
                        'context_ref':fact['contextref'],'anchor_date':anchor})
    if not matches or len({m['legal_name'].casefold() for m in matches})!=1:
        raise ValueError('Missing or conflicting own legal name')
    result=max(matches,key=lambda m:(m['anchor_date'],m['context_ref']))
    return dict(result,source=snapshot.metadata(),snapshot_id=snapshot.snapshot_id)
