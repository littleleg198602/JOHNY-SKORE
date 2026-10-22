This lane adds an independent corporate-action bridge review to
`instrument_transition_review(..., corporate_action_bridges=[claim])`.
The output preserves the exact prior identity version and reports separate
action, issuer-name, CIK/CUSIP/class, ticker-context and effective-date claims.
Explicit no-par and USD par values remain distinct. Readiness for an explicit
review does not approve an identity, alias or interval extension.

Six new ordinary SEC HTTP requests returned complete public raw bodies. Four
public web discovery queries preceded them; the download script permits at
most six requests, one attempt each. Captures retain original bytes, official
URLs, SHA-256 and UTC observation clocks. SEC submissions acceptance times
are carried verbatim; they do not reconstruct prior source availability.
Any access-value body is withheld with metadata and cannot enter replay.

The actual XOM replay binds the old CIK `0000034088`, CUSIP `30231G102` and
June 30, 2026 Schedule 13/DEI context. The July 1 successor action describes
old no-par shares changing to new shares with $0.001 par value. A separate
July 31 filing binds `0002115436`, `XOM` and the new class; the capital-stock
exhibit corroborates the new class. The older July 7 no-par claim remains a
source conflict. A direct new-CIK/new-CUSIP instrument proof and dated
old/new-CUSIP action passage are missing. The bridge therefore remains
`PRIMARY_BRIDGE_PARTIAL`. All 150 historical Q2 versions, their knowledge
times, canonical 687 symbols and zero current-Q3 registry coverage persist.

Offline replay: `python -m evidence.batch5_identity_20261014.replay_bridge`.
The focused log records 111 passing checks before final strict name guards;
the final changed-module log records 12 passing checks. Hashes and limits
are recorded in `handoff.json`. No source scoring, historical OOS, Windows
acceptance or specialist DONE claim is made.
