# Prime-award archive recovery

No actual prime targets were recovered. An exact Git-blob review of public
base `cbc6c4a3f023f4f32778456ae4f73d6d2c91fcc6` examined all 506 published
JSON/JSONL/response paths, 8,447,190 bytes, with no oversized files skipped.
The two embedded prime pages are the original LMT 100-row positive with
`hasNext=true` and an empty control. NOC retains a partial summary and a
response hash whose exact body is absent. There is no complete original
prime capture manifest/body traversal. Copied prime rows and the genuine
14-row transaction capture cannot replace it. `corpus_review.json` records
original Git blobs, file hashes, pagination and actual importer rejections.

`review_corpus.py` reproduces that review from the fixed published tree:

```text
python -m evidence.batch6_contracts_20261014.review_corpus
```

The new offline API is:

```python
recover_prime_awards_from_archives(
    store, *, manifests, expected_manifest_sha256, subject_id, as_of,
    identities=None, maximum_pages=20, maximum_awards=2000,
    validate_only=False,
)
```

Pin-map keys are `str(manifest_path)`. `validate_only=True` accepts `store=None`
and never connects to SQLite. Each original manifest must use schema
`usaspending_prime_award_capture_v1`, original `LIVE` acquisition metadata,
the exact official prime endpoint and POST request, body hash/length/reference,
successful complete response and original aware capture clocks. Bodies are
bounded to 4,000,000 bytes, manifests to 64,000 bytes and rows to 32,000 bytes.
Unsupported encodings, withheld/replaced bodies, unsafe access values, duplicate
JSON keys and nonfinite numbers fail closed. Whole original bodies stay unchanged.

Every page from 1 through a terminal page must share the exact verified UEI and
bounded retrieval window; duplicate award IDs, changed scopes, overlapping clocks,
malformed rows or a relationship not known at original capture reject the batch.
Long-running contracts may start before the retrieval window; each start must
still fall inside the verified ownership relationship. Reported modified clocks
are kept exactly, with UNKNOWN/date-only/unzoned/source-time status and no publisher
revision assertion. A terminal traversal does not prove a stable publisher snapshot,
historical revision completeness, whole-company coverage or revenue.

All validation precedes a single `BEGIN IMMEDIATE` findings transaction. The API
does not run constructors or schema migrations, create checkpoint/quota state, or
make HTTP requests. Conflicting existing findings roll back the entire import.
Existing exact findings, including ingestion clocks and retrieved heartbeat,
stay unchanged on replay. Source-content hashes match the original prime collector.
Source capture clocks remain separate from physical current ingestion; `as_of`
cannot backdate first observation. Returned findings can become transaction targets
only when available by that actual ingestion time under the existing target gate.

Statuses are `RECOVERED`, `UNCHANGED`, `VALIDATED`, `COMPLETE_EMPTY`, or `REJECTED`.
The returned report is not automatically persisted; the coordinator can save it
under a content hash. The empty status covers only the selected UEI/query window
and writes no negative finding. No publisher-signed authenticity is asserted.

Focused mechanism tests use temporary generated LIVE-shaped fixtures; these are
explicitly not real captures, history or acceptance evidence. Genuine committed
incomplete/transaction archives are separately checked for rejection and unchanged
bytes. Source HTTP is zero. Fresh collection remains blocked by missing original
durable daily quota history; no quota reconstruction or new collection database.
Scores, registries, automatic trading, licenses, accounts, Windows/OOS acceptance
and the specialist completion status remain unchanged.
