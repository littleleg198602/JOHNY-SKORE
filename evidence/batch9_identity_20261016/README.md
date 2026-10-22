# Original registered-security class review

`final_actual_review.json` is the final producer's physical review, recorded on
2026-10-16. It records two independently pinned Section 12(b) registered-security
rows for CIK 1474432, symbol P, Class A Common Stock and $0.0001 par value. The
original cover contexts anchor those declarations on 2026-05-27 and 2026-08-26.
These dates do not establish a legal-name effective date, the first effective
PSTG-to-P day, a historical machine observation, or an issued-instrument interval.

The independent Schedule 13G/A declares CUSIP 74624M102 and generic Common Stock
for the event day 2026-03-13. Its class/par and same-day symbol bridge remain
unresolved. The original index identifies Vanguard as the reporting filer and
Everpure as the subject; its raw XML link uses the reporting-filer archive route,
whereas the captured response came from the subject route. This conflict is
preserved. The EX-4.1 response is only a wrapper for two uncaptured images. Its
index declares 1,252 bytes versus 1,361 served bytes and displays an acceptance
calendar day different from its filing day. The review preserves both conflicts.

All nine remaining blockers are explicit. New verified identities, production
registry changes, current Q3 verified instrument coverage and adoptions are zero.
The August cover is one Q3 source anchor, not instrument coverage or acceptance.

The six new SEC research requests exhausted the assigned budget. Every original
response has a prior reservation, a 25-second timeout, a 4,000,000-byte cap,
physical UTC clocks and exact SHA-256. All six returned HTTP 200, totaling 91,010
bytes. The seventh plan source reuses an unchanged prior submissions capture,
without a new request. The two certificate images were not requested. No
collector API, quota database, credentials, withheld body, forecast or score is
created or changed.

The importable API is
`review_sec_instrument_class_scope(request, captures, *, as_of=None)` in
`market_checker_app/services/sec_instrument_class_scope_review.py`.
`registered_security_tables(original_bytes)` exposes whole row pins and physical
column spans for explicit selection. `captured_review_plan.json` uses the existing
`captured_identity_review_plan_v1` format and kind `SEC_INSTRUMENT_CLASS_SCOPE`.
It has seven exact metadata/body descriptors and no explicit `as_of`; integration
should use the physical default. It requires no file search or source collection.

`initial_focused_tests.log` preserves the actual 19-test passing run for the
unchanged final module/test hashes pinned in `handoff.json`. The real replay also
passes original byte, row, context, source-role, budget and clock checks. The new
module has no Windows or full-suite acceptance yet; those belong to integration.
Synthetic positive/negative fixtures are regression checks, not live acceptance.

The initial exchange-format rejection remains in `initial_replay.log` and its
producer snapshot. Only the explicitly namespace-bound SEC exchange-name
transformation was enabled; class/ticker transforms and rebound namespaces remain
rejected. The first successful report and its producer remain immutable in
`actual_review.json` and `producer_versions/first_actual_instrument_scope_service.py`.
`version_verification.json` confirms an exact replay of that original version.
The injected historical clock is used only for that deterministic verification;
the final report records the real later physical reviewer clock.

For a fresh offline replay from the repository root, use the prepared interpreter
with `PYTHONPATH=.` to import `build_inputs()` from `replay_instrument_scope.py`
and call the review API. Existing output names use exclusive creation. No replay
script performs network requests.
