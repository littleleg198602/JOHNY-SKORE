# Dated DOJ case and legal-entity scope review

The original DOJ-hosted Apple complaint supports docket `2:24-cv-04055`,
Document 1, filed 21 March 2024, and the exact caption entity `APPLE INC.`,
Defendant. Its document stage is **ALLEGATIONS_ONLY**. The complaint's request
that the court “Adjudge and decree” is requested relief, not an adjudication.
This review makes no claim about the case's current disposition.

Four precommitted primary captures returned HTTP200 without redirects on
14 October 2026 UTC, totaling 2,753,451 original bytes. The immutable review was
physically ingested on 15 October 2026 at 04:34:03.649657 UTC. Directory dates and
historical document dates do not establish historical source availability.

The separately captured SEC 2023 Apple 10-K pairs `Apple Inc.` with
`0000320193` in the same DEI context `c-1`, dated 30 September 2023. Its exact
accession index displays Filing Date 3 November 2023 and Accepted
`2023-11-02 18:08:27`; the Accepted display's timezone remains UNKNOWN.
The index's SEC `/ix?doc=` link is recognized as a reference to the exact
archive path. The reviewer does not follow that link or infer a new request.
The index's advertised renderer length does not replace the actual body pin.

The production legal registry's Apple row is a 31 October 2025 document-day
snapshot, known 12 October 2026. It does not cover the 2024 complaint date.
The source name equality and the earlier dated filing do not prove continuity
or knowledge before the case. The actual review remains **PARTIAL**, with:

- `MISSING_CASE_DATE_REGISTERED_IDENTITY`
- `MISSING_IDENTITY_KNOWN_BEFORE_CASE_DAY`
- `MISSING_INDEPENDENT_LEGAL_CONTINUITY`

No identity is adopted. Issuer/group/parent liability, fine, conviction,
adjudication, current case status, complete issuer coverage, live acceptance,
historical availability, prediction input and scoring flags remain false.
There were no NHTSA/EPA/DOJ scout API requests or further source captures.
The pure reviewer reads only supplied source bodies and legal rows. It does
not open a quota store or change registries or shared configuration.

## Pure API and packet

`review_doj_enforcement_scope(request, captures, *, as_of, ingested_at,
legal_records=())` accepts already loaded metadata and whole original bytes.
`captures` can be an ID-keyed map of `{metadata, body}` objects or a bounded
list of those objects. A map key must equal its metadata `capture_id`.
The function performs no HTTP, filesystem, SQLite or registry writes.

`request.json` contains the exact issuer/case/date/document selectors and
selected original passages. PDF pages are one-based; HTML is page 1.
`capture_packet.json` gives each metadata and original-body path for the
coordinator's separately pinned loader. `capture_index.json` retains all four
actual attempts, URLs, raw hashes/lengths and source clocks.

Original bodies must be successful, unchanged official HTTPS responses with
exact hash/length pins and ordered source/physical ingestion clocks. Input
is limited to four bodies of at most 4,000,000 bytes. PDF input is checked for
encryption/page count before extraction: at most 120 pages, at most 12 selected
pages, at most 2,000,000 decoded selected-page characters. The first three
caption pages are included in this selection budget. The text bound is
checked immediately after each page extraction, not a general CPU/memory
sandbox for a PDF decoder. Missing decoder/content produces PARTIAL. No HTML
fallback or metadata title can substitute for a rejected PDF.

The exact full caption legal-name line is required, preserving punctuation
and suffixes. A brand prefix such as `Apple` is insufficient. Stage comes from
the caption title following the party role. A proposed judgment remains a
proposal; even a final-judgment title requires separate outcome review and
does not grant adjudication/liability assertions.

## Qualification and preservation

`tests_final.json` and `.log` qualify the exact source/test hashes: 19/19
passed, no skips/failures/errors. The first 19-test run found one brand-prefix
failure; `tests_initial.*` and exact pre-fix source/test snapshots remain
historical diagnostics. They do not qualify the corrected source.

`review_record.json` pins the immutable actual review, request, source/test
versions and existing legal registry. `prior_preservation.json` verifies all
822 prior evidence/data files, 39,328,744 bytes, against public base `089ce4d`.
`content_audit.json` records whole original-body and decoded PDF access-value
cue checks; derived PDF text is not exported. The three rejected bodies stay
absent, without sanitized, encoded or compressed substitutes.

`git_diff_check_original_raw.json` records the source/test/evidence hygiene
check. Original publisher whitespace and the exact failed unittest console
status-line whitespace remain byte-identical and are explicitly excluded
from that source hygiene check. Their byte/hash assertions are preserved.

The local tests are focused review qualification, not a new full-suite,
hosted Windows, source-history, OOS or 21-specialist acceptance result.
