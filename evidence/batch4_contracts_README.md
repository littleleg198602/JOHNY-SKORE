# Transaction capture observations and revision clocks — 2026-10-13

This lane adds a durable immutable observation audit for each exact captured
transaction row. Existing transaction finding hashes stay unchanged. Repeating
the same source capture, including LIVE-to-ARCHIVE_REPLAY, creates no duplicate
source observation. A fresh unchanged capture retains its new observation
clocks without adding a finding version. Changed rows with the same source ID
retain separate raw versions and their original capture metadata.

The observation identity includes exact capture metadata; differences in raw
headers or timestamp representations are preserved. Replay wrapper labels and
local filenames do not manufacture additional publisher observations. Queries
are limited to at most 500 entries and report truncation. Actual UTC ingestion
is independent of a requested historical `as_of`, so importing an older source
capture cannot backdate this ledger's local knowledge.

Optional modified fields retain their exact source values. Zoned ISO timestamps
are checked against the original response completion clock. Date-only and
unzoned fields remain unresolved, with no inferred timezone. Null or absent
fields remain unreported. Malformed or future zoned clocks force `PARTIAL` while
preserving the source row, original response bytes and valid preceding work.
HTTP Date/Last-Modified headers, action dates and modification numbers are not
promoted to transaction publication/revision timestamps. Terminal pagination
still describes this traversal, not complete source revisions or deletions.

`batch4_contracts_tests_20261013.json` and its hashed log record **63/63** focused
Linux tests, including the unchanged quota/lease and CLI behavior. The new tests
include a real archived 14-row upgrade: all prior immutable finding hashes and
details remain unchanged and the new observation ledger records each capture.
Synthetic source-clock scenarios are not new live evidence or Windows acceptance.

`batch4_contracts_revision_20261013.json` records an actual offline replay of the
committed 6,220-byte publisher body, SHA-256
`2e74f5f6960faf192b7f1c7256c9b174453bf4ab63d85e935b2ff3debed75447`.
It adds 14 audit entries and no new findings; the repeat adds neither. The
original source clock is 2026-10-12; the ledger ingestion clock is 2026-10-13.
The response has no modified/release timestamp. All referenced source captures,
replay manifests and test log hashes are preserved. No source HTTP request was
issued. Read-only inspection of the existing task database found no persisted
USAspending quota state or verified award target, so no replacement quota ledger
was seeded to request another capture.

Caller integration points:

```python
# The existing service/application receipt now also carries these fields.
summary["new_source_observations"]
summary["modified_clock_conflicts"]
summary["source_transactions"][0]["source_modified_clock_audit"]
summary["source_transactions"][0]["source_observation"]

# Read the persistent audit independently of the collector instance.
from market_checker_app.storage.award_transaction_store import AwardTransactionStore
audit = AwardTransactionStore(store).observations_for_award(
    subject_id="LMT", generated_award_id=selected_generated_award_id,
    as_of=actual_current_utc, limit=100,
)
```

`different_rows_in_prior_captures` counts distinct payloads observed in earlier
captures, not publisher revision order or obligation/revenue deltas. Broader
dated UEI/award coverage, incremental captures using the persistent shared quota,
publisher revision/deletion history, coverage denominators, OOS evidence and
actual user Windows acceptance remain outstanding. Scores, registries and
trading behavior are unchanged.
