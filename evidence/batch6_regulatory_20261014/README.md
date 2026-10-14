# Captured-only EPA FRS facility/legal-owner review

`review_frs_ownership(proposal, captures, repository_root=..., ingested_at=...,
as_of=...)` reads at most four exact original capture pins. It performs no HTTP,
DB writes, quota reservations, registry adoption or scoring. The physical import
clock controls knowledge availability; historical fact dates cannot backdate it.
`import_frs_ownership_review(..., archive_root=...)` uses its own actual UTC clock
and saves only an immutable review JSON. It creates no replacement source body.

The plan/proposal JSON define exact ticker, CIK, legal name, legal-context date,
12-digit FRS ID and facility name. EPA facility evidence uses an exact JSON row
pointer and bounded program ID/name/acronym bindings. Duplicate IDs, errors,
changed query/response scope, wrong bytes or clocks and truncated queries remain
PARTIAL. An operator, matching facility name or parent/subsidiary narrative does
not establish facility ownership. Ownership passage support requires a separate
primary source, an explicit owner relation, exact facility/FRS scope and a literal
date. Source support remains UNVERIFIED and candidate-only even when all
synthetic contract checks pass; all attribution/liability/adoption flags are false.

The actual case is canonical DOW and the earlier EPA name lead `DOW INC`, FRS
`110019986586`. `evidence/epa_echo_live_20261002.json` contains a dated summary
and a response hash, but the original EPA body is missing. No body was rebuilt
from that summary. Original EPA quota history is unproven, so no EPA API request
was issued. The already exhausted NHTSA day was not touched or reset.

Two new bounded primary SEC research requests captured the exact DOW 2025 10-K
index and full original filing on 2026-10-14. The index states filing date
2026-02-03; the report period is 2025-12-31. Those dates are not knowledge times.
The new raw files retain original bytes, hashes, request/capture clocks and Git
`text: unset` protection. Neither contains the selected FRS ID or dated ownership
proof for that facility. The issuer filing supports only `Dow Inc.` / CIK
`0001751788` / context `c-2` / date `2025-12-31` for this proposal.

A genuine co-filer issue is covered: the secondary `The Dow Chemical Company`
context `c-1` has explicit DEI CIK `0000029915`, although its generic XBRL entity
identifier is the parent CIK. The new review requires explicit same-context DEI
CIK/name facts and refuses to join that subsidiary name to the parent CIK. No
shared SEC parser or production identity changed.

`replay.json` and `reviews/<hash>/review.json` record the actual UTC physical
import. Result: PARTIAL; legal-context support present, original facility/program
and independent dated ownership evidence missing. There is no new verified
ownership, issuer liability, full relevant coverage, Windows or historical/OOS
acceptance. Eleven focused regressions passed. The earlier pre-expanded-sensitive-
body-guard log is retained separately and is diagnostic of that earlier version.
`handoff.json` records current hashes, limits and unchanged prior source evidence.

Primary discovery references (not additional original-byte archives):

- https://www.epa.gov/frs/frs-api
- https://www.epa.gov/frs/search-frs-data
- https://echo.epa.gov/system/files/ECHO-DFR_Rest_Services.pdf
- https://www.sec.gov/Archives/edgar/data/1751788/000175178826000018/0001751788-26-000018-index.htm

The rejected NHTSA documentation and both withheld IR authority bodies remain
absent. Source bodies with access-value cues are wholly withheld before parsing;
no sanitized, encoded or compressed substitute is exported. Root owns CLI/UI
wiring and any later registry review or properly authorized LIVE collection.
