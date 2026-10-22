# Explicit dated instrument point review

This is a standalone exact-day operation. The older class-scope report has an
incidental same-day association; the control/merger reviewers require merger
roles, actions or a registry transition. The new consumer takes one explicit
requested point and four selected original capture roles, without a merger,
catalog, specimen, quarterly extension or registry input.

API: `review_sec_dated_instrument_point(request, captures, *, as_of=None)` in
`market_checker_app/services/sec_dated_instrument_point_review.py`.
The request contains `anchor_on` (one ISO day or explicit null), `identity`
(`issuer_cik`, `issuer_name`, `ticker`, `class_title`, `cusip`), an `instrument`
descriptor (`source_key`, `index_key`, Schedule13 `form`), and a `cover` descriptor
(`source_key`, `proof_key`, `proof_kind`, `form`, `registered_claim`). The registered
claim reuses existing whole Section12(b), header/data row and context pins.
Captures use the standard explicit metadata/original-byte pairs. Root integration
can use `captured_review_plan.json`, kind `SEC_DATED_INSTRUMENT_POINT`, with its
four pinned descriptors and no explicit cutoff. No file search is needed.

The operation binds the Schedule's literal subject CIK/name/class/CUSIP/event day
to independently filed cover issuer/name/symbol/class facts at the requested day.
A reporting-filer CIK is retained separately from the subject's CIK. Its raw
original index link must match the captured route. A mismatched subject route
is never treated as a filer alias. Same-accession or identical-body disclosures
cannot supply independent sources. Exact class and par disagreements, unknown
dates, unavailable originals, qualified-publication gaps and index/served
conflicts remain explicit. A generic class is not filled from another source.

`point_supported` concerns only a single source-asserted day. Even a synthetic
supported point has no adopted identity, issued validity interval, corporate
action effective day, continuity between points, alias, Q3 coverage or historical
availability. `acceptance_blockers` keeps those gaps separate from point-specific
`blockers`. Source selection stays at physical START; interpretation availability
is recorded at physical END. An explicit cutoff is a completion deadline and
cannot admit sources after START, backdate an interpretation or be crossed by END.

The actual retained P proposal requests 2026-08-26. The original August cover
declares P/ClassA/$0.0001 at that context anchor. The separate Schedule13G/A says
Common Stock/CUSIP74624M102 at 2026-03-13. Its original index raw link uses the
reporting-filer route while the retained response uses the subject route. Five
point blockers remain; no point or action bridge is asserted. All four originals
total 201,828 bytes. They retain their original UTC15/16 source clocks; the new
interpretation was physically recorded on UTC18. No original bytes or metadata
were changed, and none were newly fetched.

The new module's focused suite passed 22 discovered/executed cases, plain OK in
0.276 seconds, all failure/error/skip/expected/unexpected counts zero. It includes
the actual four-original replay and synthetic mechanisms for independent positive
points, class/par/name/symbol/CUSIP differences, same accession, source-role and
route conflicts, missing dates/publication time, withheld bodies, whole row pins,
13D/plural CUSIP, and sequential completion/rollback/cutoff clocks. Synthetic
positives are not live or analytical acceptance. No unchanged full suite ran.

No HTTP/search, collector API, DB, quota, registry, model, fitter, score, test
budget, account, license or trading change occurred. The four withheld bodies
were not accessed, restored or substituted. Canonical687/Q2instrument150,
currentQ3 verified coverage0, adoptions0 and full acceptance0/21 remain unchanged.
