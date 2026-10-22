# Bounded NHTSA model-year negative-case audit — 2026-10-14

The available gap was a genuine successful complete-negative case and a durable
runtime/report contract. This lane made exactly **8 actual recall API requests**
in the existing canonical task database, using the coordinator's central NHTSA
registration (4 make-subjects / 8 requests per UTC day). No new/reset LIVE database,
documentation HTML request, data purchase or production registry was used.
The ledger is prospective from actual UTC2026-10-14; prior research request
history is not claimed recovered.

All eight model/year scopes were independently checked against the exact raw
successful vPIC model-year catalogs already captured2026-10-13. These are make/
model/year source scopes, not issuer attribution or approved registry additions.
The run charged **8 requests / 3 make-subjects**. CT5, Optiq, Vistiq and Bronco
returned HTTP200 with respectively1,3,4,9 exact campaigns. Celestiq, XT5,
Corvette and Trax returned HTTP400 with Count0/results[] and a body success
message. Their original bytes remain unchanged; all four are **PARTIAL**.
There was **no genuine HTTP200 successful empty case** in this bounded run.
An API-wide claim that successful empty cases never exist is not made.

New production API: `NhtsaNegativeReviewService.run()` performs bounded reserved
I/O, archives immutable exact responses/review metadata, and stores case history
in the existing ScoutStore database. `report(as_of=..., limit=...)` returns only
reviews known at the cutoff; `ingest_capture(...)` permits explicit offline
replay, does not backdate application availability or credit replay as LIVE.
`documented_model_scope(...)` validates original catalog bytes/hash, authority,
request model year, exact make/model entry and observed UTC clock.

A complete negative requires HTTP200, exact unchanged query, an independently
documented model-year scope, valid complete publisher success envelope and
Count0/results[]. It is always `COMPLETE_NEGATIVE_MODEL_YEAR`, never issuer-wide
absence. Synthetic success fixtures exercise that contract without claiming
LIVE evidence. Unexpected HTML or a credential field cue is whole-body withheld,
with its original hash/size metadata and no sanitized/encoded/compressed body.
The previously rejected documentation capture remains withheld and untouched.

The ordinary `NhtsaRecallScoutService` now uses the **same central quota, provider
lease and cooldown** before every client request, so it cannot bypass the review
budget. Existing strict transport, row/date/manufacturer validation, versioned
monthly scope checks and preserved findings remain intact. Quota/lease stops are
WAIT_QUOTA/LEASE_LOST; prior completed rows/checks survive. A restart or scope
correction cannot refund attempts. Real I/O uses its actual UTC day; deterministic
archived tests explicitly identify their stub as offline and retry on later
synthetic UTC days. All prior raw-byte/hash assertions remain strict.

`live_run.json` preserves the actual run and durable report. `captures/*/response.json`
and `review.json` contain eight original API bodies plus metadata. `handoff.json`
links exact source, code and test hashes. The42 focused tests cover shared
cross-runtime exhaustion/restart/cooldown/lease behavior, original NHTSA transport
and historical capture boundaries, model documentation, immutable replay and
cutoff semantics. No Windows, full coverage, OOS, DONE, score or trading claim.
