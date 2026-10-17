# Original static FDA application/product review

Three genuine bounded research transports captured three complete HTTP200 originals (202,505 bytes) for the exact BLA761081 scope. The request cap remained six, the whole-body cap remained 4,000,000 bytes, and no redirects, retries, collector API requests, database/quota resets or accounts were used. Slots4–6 remain unused. No request was made after resumption. The original bodies and their reservation/result metadata remain unchanged.

| Original | Observed UTC on 2026-10-16 | Scope established |
| --- | --- | --- |
| SEC PFE submissions | 12:18:23.142227 | Exact CIK0000078003/name PFIZER INC/ticker PFE snapshot, before either FDA request |
| Drugs@FDA BLA761081 application | 12:18:47.144073 | Company PFIZER INC; one TRAZIMERA/TRASTUZUMAB-QYYP/420MG/INJECTABLE;INJECTION structured table row |
| Purple Book BLA761081 details | 12:19:55.747533 | Applicant Pfizer Ireland Pharmaceuticals Unlimited Company; products001/420MG and002/150MG, For Injection/Intravenous |

The missing original static application body is now available and parsed. The two FDA datasets are distinct. Drugs@FDA labels the company as **Company**, does not publish a product number in this table and combines dosage form/route in one source cell. Its row ordinal is never converted to001, and the Company label does not become Applicant, clinical Sponsor, manufacturer, legal owner or parent. The earlier SEC original supports only the exactly displayed company identity.

Purple Book publishes explicit product numbers, form, route, strength, presentation and Applicant. Its applicant is different from the exact SEC registrant name. The parser does not infer a parent relationship from the Pfizer name or combine its two product rows with the single Drugs@FDA row. The genuine Purple Book response also omits the outer product-details div's closing tag. Only the explicitly closed labelled fields and grid rows within the same closed section are recovered. This source defect remains an explicit PARTIAL blocker. No response bytes are repaired, and a candidate cannot pass with that defect.

Both actual reviews remain **PARTIAL**, with zero applicant/product candidates, registry adoptions, new verified instruments or current Q3 coverage. The independent source objects improve evidence scope; they do not complete any specialist. Original approval March11,2019 is preserved as a displayed application date, not a current applicant/ownership start or a machine-availability date. Both pages lack a unique publisher version identifier/first-availability clock. Whole-body hashes identify the exact captured versions. Actual fresh review/ingestion clocks are 2026-10-16T17:14:41 UTC; source observations remain at their original12:18/12:19 clocks. Preliminary pre-resumption reviews are retained as diagnostics and are not substituted for the fresh results.

The pure production API is:

```python
review_fda_application_product(request: dict, captures: Mapping, *, reviewed_at=None) -> dict
```

`captures` maps explicit keys to `(metadata, original_bytes_or_None)`. Request keys are exactly `scope`, `source_kind`, `source_key`, `issuer_key`, `source_version_sha256`, `product_selection`. Scope keys are `application_type`, `application_number`, `issuer_cik`, `issuer_ticker`. Supported source kinds are `DRUGSFDA_APPLICATION_PAGE` and `PURPLE_BOOK_PRODUCT_DETAIL`. The compatible `captured_identity_review_plan_v1` kind is `FDA_APPLICATION_PRODUCT`. Both actual plans are included; the coordinator owns central dispatch/UI.

An optional product selection pins every published identity field and source row against the exact original hash. A reviewable applicant snapshot additionally requires a complete explicit product number/form/route object, exactly named independent SEC CIK/ticker evidence actually known strictly before the FDA request, the same UTC observation cohort, no source defect and no remaining gap. Applicant identity still does not prove ownership, parent, IP, historical approval holder, sponsor continuity or an ownership interval. The only positive candidate cases are synthetic contract tests.

`reviewed_at` is a bounded source-read cutoff, permitting normal coordinator read latency. It cannot be in the future and all source knowledge must precede it. New review/ingestion/knowledge always use the actual physical invocation clock, never a historical input cutoff. No score/model/trading change or historical out-of-sample/Windows acceptance is claimed.

Before interpretation, the adapter verifies whole original hash/length/4MB budget, bounded safe metadata, complete HTTP200, no transformed or withheld bodies, exact HTTPS endpoint/application query, duplicate query fields, aware request/observation/knowledge clocks and source version. Parsing bounds HTML depth/nodes/rows/cells, rejects duplicate attributes/identifiers/labels/product numbers, excludes hidden content and keeps NDA/BLA namespaces separate. HTTP errors/empty pages are not accepted issuer-wide negatives. The four older withheld IR/NHTSA bodies remain absent and were not read or reconstructed.

Reproduce the source review offline, without repeating any transport:

```sh
python -B -m evidence.batch9_healthcare_20261016.replay_sources
python -B -m unittest tests.test_batch9_fda_application_product -v
```

The final focused suite passed27/27, zero failures/errors/skips, in0.329 seconds. The earlier25-case success log is retained. The full2037 baseline was not repeated. Original-byte captures use `text: unset`, preserving LF/CRLF and exact hashes across checkouts. Seven protected production data inputs and the unchanged canonical687 universe are pinned in the handoff. Search discovery snippets were used to locate official URLs, never as captured source bodies or acceptance evidence.
