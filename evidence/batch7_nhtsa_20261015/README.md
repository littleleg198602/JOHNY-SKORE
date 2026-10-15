# Actual bounded NHTSA model-year probe

Four newly probed, independently cataloged 2026 model scopes produced two
positive HTTP200 cases and two HTTP400 empty cases. **No genuine HTTP200
complete negative was obtained.** The two HTTP400 empties remain PARTIAL.

| Exact catalog make/model | HTTP | Review status | Validated campaign rows |
| --- | ---: | --- | ---: |
| CADILLAC Escalade IQL 2026 | 200 | POSITIVE_EXACT_MODEL_YEAR | 2 |
| FORD Mustang GTD 2026 | 200 | POSITIVE_EXACT_MODEL_YEAR | 1 |
| CHEVROLET BrightDrop 2026 | 400 | PARTIAL | Unvalidated |
| CHEVROLET Suburban HD 2026 | 400 | PARTIAL | Unvalidated |

The original responses total 4,232 bytes. Their requests and observations span
15 October 2026, 07:29:10–07:29:37 UTC. Each review's physical ingestion and
known-at clock follow its actual response. Old publisher campaign dates,
model years and the 13 October catalog capture dates do not reconstruct
earlier application availability.

The exact scopes were checked by `documented_model_scope` against the retained
HTTP200 vPIC catalog original bodies and metadata. No catalog, manufacturer,
documentation, web or unreserved NHTSA request was made. A catalog model/make
entry is research scope, not an issuer, legal ownership or product bridge.

`precommitted_plan.json` was saved before GET. The existing
`NhtsaNegativeReviewService.run` and `NhtsaRawRecallClient` performed exactly
four shared reservations, with the normal `PublicSourceBudget` and provider
lease in the original retained database. The transport refuses automatic
redirects; no retry or second hop occurred. Fairness ordering determines the
request sequence within the precommitted four scopes.

The genuine UTC15 ledger moved from 0 to 4 requests and 0 to 3 make subjects.
The daily limits remain 8 requests / 4 makes, leaving 4 request slots and
1 make slot at the recorded post-run snapshot. Same-day make retry gates
still apply. There were no additional calls after this four-request probe.
Unused quota did not trigger another probe.

The database was not copied, reset, reconstructed or replaced. All 11 prior
public-request rows match the coordinator's canonical row hashes; all eight
prior scope-review rows remain unchanged. The four new requests produced two
RETURNED and two FAILED request outcomes. FAILED does not refund quota.
The normal service persisted four immutable original-body/review pairs and
released its provider lease. A final read-only check also verified all 15
post-run request rows unchanged.

`capture_index.json`, `live_run.json`, `retained_ledger_before.json`,
`retained_ledger_after.json` and the hash-addressed summary review contain the
actual result and exact pins. The copied coordinator proof is JSON evidence
only, never a quota database or reconstructed runtime state. Existing CLI/UI
can read the four new stored reviews through `read_nhtsa_scope_reviews`.

Six focused offline checks passed: four exact original-byte replays,
reservation/history accounting, and the physical known-at cutoff excluding
all four new reviews. Replay does not initialize a live client, fetch, create
a store, or append DB rows. Its origin remains ARCHIVED_RESPONSE_REPLAY.
The verifier's initial launch error and exact source snapshot are preserved
under `diagnostics/`; it failed before test execution and caused no source or
quota retry. Production runtime and prior test suites were unchanged.

No registry is changed. There is no issuer-wide absence, liability, ownership,
new verified identity, scoring/trading, historical availability, OOS, Windows
or full-specialist acceptance claim. These four results also do not establish
that a genuine HTTP200 empty response is impossible elsewhere in the API.
