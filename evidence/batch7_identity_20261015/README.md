# LEG/SGI dated control and instrument review

This is a new, read-only research output on public base `089ce4d`. It leaves
the previous LEG stock-merger review, co-filer binder, seven production
registries and all earlier evidence unchanged. No identity, alias, interval,
Q3 coverage, score, historical knowledge or full specialist acceptance is
created.

Six reserved one-attempt SEC research GETs returned HTTP200. Each request
reserved its receipt before HTTP, prohibited redirects and bounded its body
at 4,000,000 bytes. Original bytes, hashes, URLs and physical clocks are in
`captures/`. The actual requests and observations occurred on **14 October
2026 UTC, 23:25–23:29**; the batch directory and later report-creation day do
not change those clocks. These are research responses, not quota-managed
collector LIVE runs or a reconstructed SEC request ledger. Four prior web
discovery queries were separate searches, not original-byte acceptance.

The new independent disclosures supply useful but limited facts:

* A newly captured LEG Schedule13G/A has exact issuer CIK58492, CUSIP524660107,
  Common Stock and event day2026-06-30. It aligns with the retained LEG
  June30 ticker/class cover. This is one Q2 anchor; it does not extend Q2
  identity into Q3.
* A newly captured SGI10-Q independently has CIK1206264, symbolSGI and
  common-stock $0.01 par at2026-06-30, aligning with the retained SGI
  Schedule/CUSIP88023U101. Its registrant-name fact contains two nested
  `ix:nonNumeric` name facts. The existing binder correctly rejects that
  structure; this review records UNSUPPORTED/MISSING and keeps it partial.
* LEG's April13 agreement disclosure and original EX-2.1 agreement describe
  **planned direct** surviving-company control. Their unknown completion
  day remains UNKNOWN. The filing and its exhibit share one accession.
* The retained August26 LEG completion describes **indirect** surviving
  control; the separate SGI completion describes **direct** control. The
  disagreement remains explicit. SGI's DEI identity context is August25,
  which is also kept separate from the August26 action day.
* The newly captured April13 SGI announcement describes a proposed
  transaction. The new August26 closing press release corroborates a
  completed combination but supplies no direct/indirect control or dated
  CUSIP bridge. It shares an accession with SGI's completion8-K.

None of the four new corporate-action visible texts contains either exact
old or recipient CUSIP. That narrow observation concerns these exact bodies
only. It proves neither global absence nor delisting. Exact old/new
effective CUSIP/class/action binding remains missing. The original five
baseline blockers remain present alongside the new precisely scoped gaps.

`review_control_instrument_bridge(service, request)` is the production API.
It reads caller-supplied `Sec13fIdentityBulk` snapshots and performs no file
reads, HTTP, registry writes or adoption. `request.json` gives the exact
request: `stock_merger`, `control_disclosures`, `instrument_disclosures`.
Each control input references its pinned snapshot, exact party names and
local references, SURVIVING_ISSUER/MERGER_SUB, PLANNED/COMPLETED,
DIRECT/INDIRECT/UNSPECIFIED, an exact passage and relation passage, and an
exact event date pair or explicit nulls. Merger Sub receives no guessed CIK.
Different files in one accession count as one independent disclosure.

`captured_review_plan.json` is the complete root-dispatch input:
`captured_identity_review_plan_v1`, kind `SEC_CONTROL_INSTRUMENT`, request
`{quarter, review_request}`, with sixteen explicit original-body pins and
their exact `SourceSnapshot` metadata. The counted bundle is7,716,508bytes;
every individual body is below4MB and the bundle is below24MB. It preserves
the actual cutoff `2026-10-14T23:29:18.942027+00:00`. Root can load this plan
without discovery, globbing or replay helper imports. Unsupported/withheld
whole bodies are rejected before any body read.

`python -m evidence.batch7_identity_20261015.replay_review` reproduces the
actual PARTIAL report and exact plan with zero HTTP. Existing different
outputs are never overwritten. The final focused24-case suite includes
actual source replay and a complete pinned-runtime roundtrip, scoped
control direction, competing names/classes, event/publication clocks,
planned versus completed events, same-accession independence, exact
EX-2.1/hidden-text parsing, no-par versus decimal-par distinction and a
withheld-before-read guard. The initial22-case run's one no-par
normalization failure and the subsequent23-case qualification remain
preserved. The final24 cases pass; they are not live identity, Windows or
OOS acceptance.
