# Apple DOJ case procedural documents

The original GovInfo PDF is the District of New Jersey's opinion in
`2:24-cv-04055`, Document 283, filed and dated June 30, 2025. It denies the motion
to dismiss identified as ECF 86. This is a procedural decision. Page 33 refers to
an accompanying Order; the separate original Order was not obtained. This packet
does not establish a merits decision, final judgment, liability or the case's
complete current disposition.

The second original PDF is the plaintiffs' opposition, Document 106, filed
September 12, 2024, from DOJ. It establishes the dated party filing. Its arguments
are party content, not findings by the court. The DOJ case index is preserved as
context; its September 2024 update does not establish current docket completeness.

The caption is literally `APPLE, INC.`. The source OCR layer, including the
opinion's `ef at` and judge-line punctuation, stays exact in the passage pins.
These documentary caption facts do not adopt a name alias or establish an earlier
issuer identity. The existing legal registry does not supply an exact earlier
case-date binding or independent legal continuity. Overall status remains
`PARTIAL`, with issuer attribution, registry adoption, live acceptance and all
score/trading flags false.

## Captures and original bytes

`research_plan.json` was fixed before I/O. Five single-hop requests were reserved
against its six-request cap, with 25 seconds and 4,000,000 bytes per attempt.
Automatic redirects were disabled. No additional HTTP is needed or was performed
during finalization. No collector API or quota database was used.

| Capture | Result | Public original |
| --- | --- | --- |
| NJD case page | HTTP 200, whole body withheld because it contains an access value | None; only original hash, size, URL and capture clocks |
| GovInfo opinion | HTTP 200, 33-page PDF | 1,344,697 bytes; SHA256 `dfc68609ea10dbb6f2e86b99df9b797030c17fc3224e020bd33f8a40478b0efa` |
| Public ECF locator probe | Transport error, no response | None; the guessed document 284 is not verified |
| DOJ case index | HTTP 200, HTML | 107,793 bytes; SHA256 `93e092c53234733cd13e4b6affc8cb49e4d5b1230920dc276b9c77c1c59491b3` |
| DOJ opposition | HTTP 200, 62-page PDF | 577,500 bytes; SHA256 `8d68256e4a356aff8a501eeaa887439185e97ea836e830c434adf51adf6d45c3` |

`capture_index.json` pins each untouched original metadata/reservation file and
the three public original bodies. The NJD page was discarded as a whole; there
is no sanitized or encoded substitute. Access-bearing meeting instructions were
not fetched and are recorded only by a safe document URL without access queries
or values in `skipped_access_source.json`. All previously withheld bodies remain
absent. Capture safety checks covered whole raw/HTML-unescaped content and all 95
PDF pages, within bounded text/page limits. These checks do not claim universal
secret detection or validate a cryptographic PDF signature.

Original PDF pages 1 and 33 of the opinion and page 1 of the opposition were also
rendered and visually inspected. Renderings are not published as publisher-body
substitutes. All actual source clocks remain October 15, 2026; the filing dates in
2024/2025 are not historical acquisition or knowledge dates.

## Pure API and actual review

```python
review_doj_case_posture(
    request, captures, *, as_of=None, ingested_at, legal_records=()
)
```

The module is `market_checker_app/services/doj_case_posture_review.py`.
`captures` is `{capture_id: {"metadata": metadata, "body": original_bytes_or_none}}`.
The root loader can consume `captured_review_plan.json`, kind `DOJ_CASE_POSTURE`,
and pass its separately validated legal registry records. The plan and
`request.json` are the complete actual case packet. No URL or path is fetched or
opened by the pure API. It uses no store or real HTTP client.

The review bounds six captures/events, twelve explicit passage pins per event,
4 MB per original response, and the shared PDF limits. It verifies exact
URL/status/hash/length, document stamp/docket/number/filed date, literal parties,
document type, judge role, selected date and affirmative motion outcome.
An opinion cannot become the accompanying order merely by mentioning it. A
proposed/draft document and a DOJ HTML report cannot become an issued original
court order. Party filings and publisher reports retain their separate meanings.

Omitting `as_of` uses the actual physical time. Explicit past cutoffs exclude
reports ingested later before decoding. Source observation/known clocks cannot
follow ingestion. `review_record.json` pins the exact code/test version, request,
plan, registry bytes and immutable report. The actual review was ingested on
October 16, 2026 at the clock recorded there; it is not backdated to the documents.
`replay.json` is byte-identical to the content-addressed `reviews/*/review.json`.

## Verification and preserved limits

The final focused suite completed October 15, 2026: 16 tests passed, exit 0.
`tests_qualified.json` pins the unchanged service and test hashes and exact log.
Tests cover both actual original PDFs and negative/mismatched stamps, scope,
caption, motion, clock, hash, size, status, redirects and withheld controls, plus
bounded synthetic order/report contracts. Synthetic order tests do not constitute
an actual source-order acceptance.

Two earlier diagnostic runs remain intact: one synthetic PDF apostrophe pin
failure and one synthetic fixture-reset index error. Their logs and exact earlier
test source versions are preserved; publisher bytes and strict production hash
or text assertions were not relaxed. The successful suite was not repeated after
unchanged code, and no full baseline suite or hosted Windows acceptance is claimed.

`prior_byte_preservation.json` verifies all 1,069 previously tracked evidence and
production-data files against their base-commit Git blob identities and SHA256s.
No old evidence, registry, shared runtime/UI, quota source, dependency or score
file is changed. `handoff.json` records integration points and remaining gaps.
