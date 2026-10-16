# Exact Form 4 identity review — 13 October 2026

This lane adds a read-only review service and stores its report in the Scout
primary finding's `details.form4_identity_review`, beside the unchanged
`form4_observation`. CIKs, director/officer flags, security titles, derivative
type, quantities and footnotes remain as-filed facts. Nothing becomes an
independently verified person, legal entity, instrument, execution or score.
The observation parser version remains compatible with existing observations.
The new report version participates in new primary findings' content hashes;
already parsed accessions are not forced through another download.

Five bounded raw SEC responses returned HTTP 200 on 13 October, with actual
observation timestamps and SHA-256 hashes in `captures.json`. The chosen
Microsoft accession is `0000789019-26-000161`, filed on 2 September for
1 September transactions. Its eight rows report owner CIK `0001513142`,
`Nadella Satya`, and non-derivative `Common Stock`, with price footnotes.
The index and raw XML are the same disclosure, not independent identity proof.

The separate owner submissions profile exactly confirms the publisher's CIK
and name but says `entityType=other`; this does not establish a legal person or
dated director role. Its declared capture-day-only interval misses September.
Microsoft's separate 10-K cover reports
`Common stock, $0.00000625 par value per share` and `MSFT`, but no exact
instrument identifier. Its declared filing-day-only interval also misses
September, and the review does not invent a title alias. Both candidates are
withheld. Their declared intervals are scope restrictions, not independently
verified effective dates.

`identity_review.json` is reproduced offline by:

```sh
python -m evidence.batch4_sec_20261013.build_review
```

The builder checks every pinned capture's raw bytes, size and hash. It retains
the unqualified index acceptance clock (`2026-09-02 18:04:41`), submissions
clock (`2026-09-02T22:04:41.000Z`) and actual October observation clocks.
Neither a normalized filing date nor HTTP/Last-Modified becomes historical
availability. Both raw 10-K cover snapshots are preserved: the publisher
changed its script URL, while the extracted visible text is identical.

The matcher requires exact owner CIK/name and exact issuer/ticker/title/type,
hash-checked captured quotes and bounded declared intervals. CUSIP/ISIN
checksums remain strict. A Form 4 index, raw/XSL XML, text file or exhibit in
the same accession cannot supply independent evidence. Identical source bytes
at another URL cannot either. Future candidate quotes are withheld; publication
clock conflicts remain visible and prevent the candidate from advancing.
Each owner's interval is checked separately at the report date and every
transaction date. Joint reports remain unallocated. An absent derivative
underlying field is an explicit source-observation gap. Limits are 64 captures,
2 MB per referenced capture, 16 MB aggregate and 4,096 review comparisons.
Even a fully matching candidate requires independent review and remains
unverified; this service is not a registry approval mechanism.

`tests.json` and `tests.log` record 95 successful targeted/integration tests
on Linux/Python 3.12.14, including the existing Form 4, Scout, document,
governance, Schedule 13 and runtime chains. The preceding current-runtime
attempt lacked `feedparser`/`yfinance`; its two dependency errors are preserved
in `tests_primary_missing_dependencies.*`. The prepared interpreter resolved
that environment failure. No assertion or benchmark was weakened.

This is one actual issuer/accession and two withheld identity candidates.
It is not relevant-universe coverage, OOS evidence, Windows end-to-end
acceptance or completion of any specialist. No production registry changed,
no score changed and no trading was enabled. More primary person/entity,
instrument/alias and independently dated evidence remains necessary.
