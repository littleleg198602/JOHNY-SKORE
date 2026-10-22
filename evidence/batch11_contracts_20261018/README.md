# Original USAspending prime scope review

This new consumer reviews one explicit recipient/query/selected-award scope
without importing findings or accessing any source, database or budget store.
It reads independently SHA-pinned whole original manifests and HTTP response
bodies supplied in memory. It checks request filters, fields, window, sort,
page lineage, capture/knowledge clocks, recipient/source IDs, unique awards,
terminal/next/previous metadata and stable reported totals. Body4MB,
manifest64KB, total16MB, five pages and500 observations are hard bounds.

Three completeness concepts remain separate:

- `observed_page_chain_complete` describes only the validated original offset
  page sequence through a terminal response with stable reported totals.
- `synthetic_page_chain_complete` belongs only to explicit fixture controls.
- `query_complete` remains false. Neither an offset terminal nor an arbitrary
  snapshot label proves a publisher-pinned snapshot, total/revision semantics,
  original full history, a genuine negative, deletion or first/last revision.

The `Last Modified Date` literal is retained with DATE_ONLY, TIMEZONE_UNKNOWN,
SOURCE_TIMESTAMP or UNKNOWN precision. Even a parseable UTC value does not
verify publisher revision time or ordering. Original response capture/knowledge
and later retained-artifact availability are separate; future artifact clocks
cannot hide behind an older response timestamp. Review knowledge is the actual
physical completion. An explicit past/future cutoff or completion past cutoff
cannot publish positive observations; rollback has no backdated knowledge.

The actual retained case is the unchanged whole
`evidence/usaspending_live_20261001.json`, SHA256
`57c1c8dfd8d33491765f2993eb99a3b1957ba91b93f0bb491c137123e1694cef`,
71683bytes, pointer `/cases/usaspending_positive_contract_uei/payload`.
It describes one Sikorsky UEI pilot:100 parsed rows, page1, hasNext=true.
It does **not** retain the whole original HTTP body/request manifest or a
terminal continuation. Therefore the current plan deliberately has
`query=null`; no filters, original request bytes or missing hash is invented.

The actual report is **WAIT**, with five exact missing-evidence descriptions:
the whole original request and the manifest/body for pages1/2. Unknown hashes
and request fingerprints remain null, with `hash_pin_known=false`. The parsed
legacy report count100 is separate from `reported_unique_awards=null` for
unavailable original pages. A missing selected award is UNKNOWN, never absence.
The request's UEI/name/award are literal scope selectors, not earlier UEI,
legal-issuer or verified prime-target adoption. Transaction14-row captures,
stored findings and replay labels cannot replace original prime pages.

`actual_review_plan.json` uses the captured-review plan schema and new kind
`USASPENDING_PRIME_SCOPE`; it references the existing whole report without
copying any source body. `actual_current_review.json` records the current physical review,
separate from the old embedded case clock. Root owns dispatcher/UI integration
and must use the4MB body budget and `as_of=None` without an explicit cutoff.
API: `review_usaspending_prime_scope(request, captures, *, as_of=None)`.

The current focused run is35/35 with all outcome flags0. The prior34-method
proof/report remains unchanged; the added regression checks the original
last-record cursor against the actual last row. Every generated page,
including LIVE-shaped branch controls, is an in-memory synthetic mechanism,
not new live evidence or a genuine negative. No database-using recovery suite
or baseline full suite was run. The pure-call regression forbids file/network/
SQL access and checks input immutability. Full console and current code/test
SHA are in `tests_current.log` and `tests_current.json`.

There were0HTTP/collector/SQL/quota operations,0 new/copy source bodies and0
registry adoptions. Missing original durable USAspending quota/history remains
a collection blocker stated by the task; this review does not inspect,
reconstruct or reset that state. Complete original prime pages, prior verified
UEI relationships, broader awards/source revisions, relevant687 coverage and
historical/Windows/OOS acceptance remain missing. All old evidence, score,
model/fitter, budgets, canonical687 and fullacceptance0/21 remain unchanged.

Offline replay from the repository root:

```
python -B -m evidence.batch11_contracts_20261018.replay_retained
```

The recorded report is write-once. Calling `run()` creates a new physical
interpretation without replacing the original saved proof.
