This batch adds an independent original-byte SEC reporting-owner account
bridge for the eight pinned September 1, 2026 Microsoft Form4 source rows.
It separately retains the issuer CIK0000789019 and reporting-owner
CIK0001513142 / source filer name `Nadella Satya`.

Three genuine new HTTP200 originals were collected under the existing shared
database's SEC provider lease/cooldown and client cadence: the February 7, 2014
Form3 full SGML submission, raw primary XML and index. The owner profile's
existing exact accession pointer selected this packet. The three documents
are one disclosure, independently distinct from the current Form4. This was
bounded one-off primary research, with no automatic redirects or retries; it
is not a quota-managed application LIVE collector or reconstructed daily
request history. Original response bytes and physical/header clocks are pinned
in `captures.json`. No older evidence or registry is changed.

The SGML REPORTING-OWNER and XML reportingOwnerId agree exactly on
CIK0001513142 and `Nadella Satya`. Their issuer fields separately identify
MICROSOFT CORP. The dated source reports an event on 2014-02-04 and filing on
2014-02-07. Its raw acceptance string `20140207180916` has no explicit timezone.
The old captured profile's accession row reports
`2014-02-07T23:09:16.000Z`. Both clocks remain distinct; no timezone is inferred
or reconciled. Current capture availability is October 2026, not 2014 machine
knowledge.

The packet signature says `Keith R. Dolliver, Attorney-in-Fact for Satya Nadella`.
That signature and name order do not approve a legal-name alias or establish
the account holder's legal person kind. The current SEC profile declares
`entityType=other`; it is an observed October 13 snapshot, not historical
natural-person evidence. Neither it nor the 2014 report proves the requested
September 1, 2026 identity. All eight person/instrument/issuer attributions
remain withheld. The concrete result is exact independent SEC account/name
corroboration plus an explicit dated scope/kind gap, with zero verified person
or instrument rows, registry adoption or score changes.

Primary research checked [SEC CIK Lookup](https://www.sec.gov/search-filings/cik-lookup)
and an [actual SEC Form3 with organizational reporting owners](https://www.sec.gov/Archives/edgar/data/1305473/000089914026000482/xslF345X06/form3.xml).
CIKs serve both organizations and individuals; Form3 individual/group filing
scope and director/officer role fields do not establish natural-person kind.
Search/discovery was not counted as an original-byte captured source or LIVE
acceptance. Three archived HTTP responses are separately counted.

Runtime API:

```python
review_form4_person_bridge(
    observation, source_capture=SECPersonCapture(...),
    evidence=[SECPersonCapture(...), ...], knowledge_at=aware_cutoff,
    scope_mode="EVENT_DATE",  # or OBSERVED_SNAPSHOT
)
```

Capture metadata requires record_id, purpose (FORM4_SOURCE / OWNER_PROFILE /
DATED_OWNERSHIP_PACKET), canonical SEC URL, original body/hash/size and aware
observed_at/known_at. Missing/withheld bodies contain no substitute bytes.
Hash/size/source-date/owner/header conflicts fail conservatively. Access-valued
bodies are wholly withheld before identity parsing. Future candidate facts are
not exposed. All directory/XSL/index variants of a Form4 accession are the same
disclosure, and repeated copies of an independent packet do not become extra
corroboration. Distinct actual unchanged-byte profile snapshots retain their
own observed days without extending intervals.

The service is captured-only: no HTTP, database construction, quota/lease,
registry or scoring mutation. EVENT_DATE keeps exact source date scope;
OBSERVED_SNAPSHOT exposes the limited observed account link while still showing
requested_event_day_matches=false for these September rows. The coordinator
owns physical report ingestion and runtime/UI integration. Original hashes,
replay JSON and meaningful focused tests are stored here. Wider dated person
kind/legal-name evidence, instrument identity, coverage and historical/OOS
evaluation remain missing; no specialist or Windows acceptance is claimed.
