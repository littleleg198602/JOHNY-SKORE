# Document-local DOJ case identity

This offline review extracts dated case-document identity facts from the two
unchanged whole primary PDFs already retained for D.N.J. `2:24-cv-04055`.
It adds page-local fields and evidence spans to the earlier procedural review.
It does not reclassify the motion outcome or establish financial issuer identity.

| Original document | Filed stamp | Pages | Caption observations |
| --- | --- | --- | --- |
| DOJ party opposition, Doc106 | `09/12/24` | 62 | Page1: `APPLE, INC.` / `Defendant`; page62 service certificate: `APPLE INC.` / `Defendants` |
| GovInfo court opinion, Doc283 | `06/30/25` | 33 | Page1: `APPLE, INC.,` / `Defendant`; source OCR plaintiff `ef at` remains unchanged |

All 95 pages have their own full docket/document/filed/page stamps. Three
caption blocks bind the court, named parties and roles within their actual page
and document. The documents corroborate the same case base. Caption spelling and
role variations remain separate observations; they are not legal aliases or a
complete list of all plaintiffs.

The full `2:24-cv-04055-JXN-LDW` is printed in the page stamps. The opinion's
caption says `24-cv-4055 (JXN)(LDW)`. Its omitted division and full suffix stay
null. Compatibility compares only the explicitly printed numeric case base;
it does not fill missing components. Parenthetical identifiers are covered by
their own complete local evidence span, including the original line wrap.

Every extracted field carries its original PDF SHA256, exact page-text SHA256,
source literal, character offsets and source-literal SHA256. Display values
collapse whitespace while preserving words, punctuation and the PDF text layer.
The modern filed-date selector is restricted to 2000–2099 and must match the
printed two-digit stamp; it is not inferred from another document's date.

`review_doj_case_caption(request, captures, *, reviewed_at=None)` is a pure API
in `market_checker_app/collectors/doj_case_caption_review.py`. Request fields are
`case_docket`, `court` and bounded `documents` selectors containing `capture_id`,
`document_number`, `filed_on`, `document_kind`. It accepts tuple packets or the
central loader's `{metadata, body}` packets. It opens no path, URL, registry,
database or real HTTP client. There is no financial issuer/CIK selector.

The actual central plan is `actual_review_plan.json`, kind `DOJ_CASE_CAPTION`.
It references the original batch8 PDF paths and untouched capture metadata.
No publisher body is copied or replaced. The physical report clock in
`review_record.json` belongs to October 18, 2026; retained source clocks remain
October 15, 2026. Filing dates in 2024/2025 do not establish earlier availability.
Explicit earlier review cutoffs and future capture clocks fail closed.

`actual_review.json` and its byte-identical content-addressed `reviews/*/review.json`
contain 2 supported case documents, 95 page stamps, 3 caption blocks and 686 field
evidence records. Overall status remains `PARTIAL`. Doc106 is a party filing.
Doc283 is a court opinion; its reference to an accompanying Order is not the
whole separate Order. Issuer/CIK identity, legal continuity, current docket,
disposition, merits and final judgment remain unverified. No registry adoption,
model/score change, prediction or trading result follows.

The current focused suite passed 21/21 with all failure/error/skip/expected/
unexpected counts 0. `focused_02.json` pins the exact producer/test SHA and full
log. It checks actual page hashes/spans plus cross-case, page, party, date,
suffix, type, missing-body, whole-withholding, clock and resource boundaries.
The earlier 20-method version and a one-method expected failure against that
earlier version are preserved with exact source bytes. The added parenthetical
span regression passes on the final version. Synthetic PDFs are contract tests,
not publisher acceptance evidence. No full baseline or hosted suite was run.

The new service-certificate caption on original Doc106 page62 was also rendered
and visually checked. Its printed name and plural role match the extracted
literal. The rendering is not published and no cryptographic signature claim
is made.

`prior_byte_preservation.json` verifies 1,973 prior evidence/data/Python files
against base Git blobs and SHA256s; all four whole-withheld body paths remain
absent. This lane made 0 HTTP requests and 0 database reads/connections/writes.
The retained quota file was not opened, and the NHTSA approval block was not
retried or worked around. `handoff.json` pins the actual plan, source/producer/
test evidence, limits and remaining work. Root owns shared runtime/UI and
publication.
