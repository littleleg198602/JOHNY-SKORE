This LEG lane adds `bridge_kind="STOCK_MERGER_EXCHANGE"` to the existing
`instrument_transition_review(..., corporate_action_bridges=[claim])` API.
`Form25Snapshot(url, observed_at, payload, published_at)` retains exact exchange
withdrawal XML in the normal captured-source fingerprint. `parse_form25`
validates its issuer/archive, class, schema and signature/publication clocks.
The direct review API is `review_stock_merger_bridge(service, claim)`.

Six genuinely new, bounded, ordinary SEC responses returned HTTP200. Each had
one reserved request, no redirect and a complete safe original body. These
are one-off evidence research; they are not an app quota-managed LIVE run or
a reconstructed SEC request history. Three primary-site discovery queries
were separate research. No withheld body was read, sanitized, re-encoded or
recreated. Replay performs zero HTTP and verifies every original byte/hash.

The retained LEG completion 8-K reports the August 26, 2026 merger. Its common
stock conversion paragraph reports 0.1455 parent shares for eligible old
common shares, with cash only for fractional shares; awards, cancelled shares
and dissenters remain separate. The independently captured Somnigroup 8-K
corroborates the date and consideration. The LEG report describes indirect
ownership; the parent report describes direct ownership. This contradiction
remains explicit and is not turned into a production parent-identity record.

New independent Schedule13 sources bind LEG CIK `0000058492`/CUSIP
`524660107` at July 31, and Somnigroup CIK `0001206264`/CUSIP `88023U101`
at June 30. The independent LEG cover binds LEG and $0.01 common stock at
June 30 (its original spelling is `$.01`). The parent 8-K's DEI context binds
SGI and $0.01 common stock at August 25, although its completion text reports
August 26. None of those dates is rewritten to match another source.

The original NYSE Form25 body binds LEG's exact CIK/name and Common Stock;
it was signed and filed August 27. It proves the filed withdrawal notification,
not an actual delisting effective day. No effective day is calculated from a
signature, publication date, statutory delay, deleted Q3 row or absent catalog.
The later retained August31 Schedule13 old-CUSIP anchor also remains visible
and does not assert that old shares were still active after conversion.

Actual result: `PRIMARY_BRIDGE_PARTIAL`, with five blockers: direct/indirect
control conflict; different old instrument/cover dates; different recipient
instrument/cover dates; recipient completion and ticker proof using one
document; missing dated old/new CUSIP action bridge. No production LEG prior
version is invented. All 150 Q2 records, zero current Q3 coverage and canonical
687 remain unchanged; no aliases, score changes or verified promotions.

Synthetic complete merger proof can become ready for an explicit review,
while actual delisting day remains a separate UNKNOWN field. Readiness never
approves an identity or supplies a continuous historical interval. This lane
does not claim broader co-filer issuer-name support; that existing issue has
a separate followup assignment.

Run `python -m evidence.batch6_identity_20261014.replay_review` to reproduce the
saved report without network. Initial 45-case diagnostics preserve two fixture
errors from stale exact source references after fixture body changes. These
were fixed without relaxing provenance checks. A 47-case combined run passed;
the final completed-action/date gate has 17 passing changed-lane tests. No
full-suite, new Windows, OOS or specialist DONE acceptance is claimed.
