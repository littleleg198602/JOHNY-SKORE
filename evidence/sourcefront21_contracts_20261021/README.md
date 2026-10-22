# SWKS original wafer-capacity deposits and prepayments

`review_wafer_capacity_deposits(request, captures, *, as_of=None)` reviews one
retained original annual filing and its exact filing index. The leaf report
identifies `review_kind=WAFER_CAPACITY_DEPOSITS`; its successful status is
`CANDIDATE_REVIEWED`, with a source-only `claim`. Invalid or unavailable scope
returns `REJECTED` and no claim.

`captures` contains exactly `issuer` and `index`, each a pair of metadata and
whole original bytes (or unavailable bytes). The metadata must explicitly
record a complete response and its original hash, length, URL and aware capture
clocks. Each body is bounded at 4,000,000 bytes. There is no transport or store.

`request.selection` contains exact DEI issuer/context/annual-period fields,
the financial-asset claim role and consolidated reporting scope, complete
original pins for six contextual paragraphs/headings, and exactly two dated
balance groups. Each group supplies `as_of_day`, `context_id`, and `total`,
`current`, `noncurrent`; each component has its literal value and ordered
equivalent `fact_ids`. The actual plan in this folder is the precise payload.
Root can copy that plan to the shared plan directory without changing bytes.

The retained Note 11 states these USD million asset balances:

| Instant | Total | Current | Noncurrent |
| --- | ---: | ---: | ---: |
| 2025-10-03 | 26.4 | 7.7 | 18.7 |
| 2024-09-27 | 167.8 | 144.7 | 23.1 |

There are six displayed components, represented by twelve nested equivalent
iXBRL annotations. The reviewer validates their category, issuer, instant,
simple USD unit, scale 6, decimals -5, literal order and exact arithmetic. The
annotations must wrap the same single displayed balance and share the original
issuer-context commitments disclosure text block. Neither nested tags nor a
total and its components are additive. The separate fiscal2023 impairment
annotation is preserved in the whole passage and excluded from deposit totals.

The current-filing comparative is not a recovered prior as-filed version.
The index declares 1,708,601 primary bytes; 1,708,710 original served bytes were
retained. The 109-byte conflict remains explicit. The accepted display has no
inferred timezone, publication instant stays unknown, and no exact historical
as-filed version or continuous source/identity claim is made. New review
knowledge uses physical completion, separately from the October16 capture.
An explicit earlier cutoff, a backwards completion clock, or validation that
finishes after the supplied cutoff fails closed.

Financial deposits and prepayments do not establish named foundry identity,
wafer volume, reserved physical capacity, actual purchases or spend, remaining
purchase obligations, revenue, Apple allocation or direct-purchase share.
Those attribution/activation fields remain false and registry adoptions zero.

The original v1 run discovered/executed 32 new focused methods with one failure
and no errors/skips. The service rejected a plan locator that selected the
same company definition earlier in the Business section. Its whole producer,
assertions, plan, replay creator, failure log and actual rejection are retained
under `v1/`. The plan locator was corrected to the definition inside Notes;
no producer guard or assertion was relaxed. v2 passed all32. Three adversarial
mechanisms were then strengthened to use valid parallel-tag XML, select the
actual hidden/table inner paragraph, and exercise an explicit nil attribute.
The final v3 executes all32, with zero failures/errors/skips/expected failures
or unexpected successes, plus a separate real-clock retained-source replay.

`qualify.py` writes create-only versioned logs, complete result counts, producer
and dependency pins and actual retained reports. `replay_retained.py` performs
an explicit offline review of the exact original source references. Test
documents assembled from literal excerpts are synthetic mechanism controls;
they are never source captures or live acceptance. No prior suite, HTTP,
collector, SQL/database, quota, registry, score, model, fitter or trading action
was performed. Original durable USAspending history and broader award/UEI
coverage remain unavailable.
