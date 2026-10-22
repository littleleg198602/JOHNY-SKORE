This batch implements a separate, pure FDA product/application scope reviewer.
It preserves an exact CRL application array, file, letter day, addressee, query,
publisher version, original body hash and physical capture clocks even when a
legal-ownership bridge is missing. Product names are bounded literal mentions
inside the original letter text. They establish no product, license, IP,
application-holder or commercial ownership.

The actual replay selects five events in three unchanged complete batch3 FDA
responses: PFE BLA761081 (2018-04-20), MRK NDA204569 (2013-06-28), and ABBV
NDA216962 (2023-03-17 and 2024-06-18) / BLA761459 (2026-04-22). Four events
contain the selected literal product references; none has a structured product
identity. The displayed approval status is not treated as event-day status.
An archive filename containing 2019 or 2025 does not replace the letter day.
The publisher's 2026-08-13 update is not the application's first knowledge time.

Exactly one authorized SEC research request was reserved before transport. It
returned the entire safe Pfizer 2017 Exhibit21 body, 258262 original bytes,
on **2026-10-14 at 23:30:38.781155 UTC**. The batch directory's 20261015 label
does not change that physical clock. Its source is:

https://www.sec.gov/Archives/edgar/data/78003/000007800318000027/pfe-exhibit21x12312017.htm

The document lists subsidiaries of “the Company” as of 2017-12-31. Its tables
have no exact `Pfizer Inc.` addressee row and no product/application assignment.
The new observation is later than the original FDA capture on 2026-10-12;
neither an old annual period nor HTTP Last-Modified reconstructs historical
knowledge. MRK/ABBV retain their existing issuer filings without a new ownership
document. **All five actual legal-scope reviews are WITHHOLD; proposals and
adoptions remain zero.** There was no FDA/CMS/Clinical collector API operation,
quota reconstruction/reset/copy, registry, score, fitter or trading change.

`retained_pfe_review_plan.json` is the concrete coordinator request shape for
`HEALTHCARE_PRODUCT_SCOPE`: explicit `scope`, `ownership_selection=null`, and
`fda`/`ownership`/`issuer` captures. Each available body has an exact relative
path, SHA256 and length plus its original metadata. Unavailable bodies require
`body_available=false` and `body_ref=null`. The parent 2017 filing is also
stored as a byte-identical uncompressed original-body rearchive so the plan
can pin raw bytes directly; this is not an additional request or disclosure.

The public API is `review_healthcare_product_scope`, with frozen
`FdaProductApplicationScope`, `CapturedHealthcareSource` and optional
`SecOwnershipSelection`. The service reads no filesystem or database and
performs no HTTP, quota or registry action. It supports only bounded, original,
complete HTTP200 bytes, a first exact company/year query of at most 100 rows,
and explicit application/file/date selection. Partial queries preserve object
facts but cannot create a legal candidate. HTTP400/404 empties do not certify
issuer-wide negatives. Whole unsafe or unavailable bodies and metadata remain
withheld; no cleaned or encoded replacement is emitted.

A synthetic narrow legal-entity candidate additionally requires an exact SEC
ownership row with unique legal name/jurisdiction, the nearest correct parent
heading and point date, and a paired unsegmented parent CIK/name/period DEI
context in the same accession's separate 10-K. Parent and ownership bytes must
be captured before the original FDA request. The ownership point day cannot
be extended to another event day. These conditions still do not establish
product or application ownership; attribution and prediction flags stay false.

Reproduce without a source request:

```bash
python -m evidence.batch7_healthcare_20261015.review_sources
python -m unittest tests.test_batch7_healthcare_product_scope -v
```

The review CLI retains its original physical ingestion clock for exact replay.
A new runtime review supplies its own actual aware clock. The capture helper
reuses the original reservation instead of making another request. All positive
contract fixtures are explicitly synthetic. Focused tests, selected archival
coverage and source-only facts do not certify LIVE, historical/OOS, group
coverage, actual user Windows or full specialist acceptance. Canonical 687,
all production registries and older evidence remain unchanged.
