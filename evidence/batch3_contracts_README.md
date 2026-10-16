# One-award USAspending transaction history

The official `POST https://api.usaspending.gov/api/v2/transactions/` endpoint was
read for `CONT_AWD_70Z02321FAMH00300_7008_70Z02321DAMH00400_7008` on 2026-10-12.
The existing 2026-10-01 award-search archive identifies Sikorsky Aircraft
Corporation, UEI `UTJWTSLMFNG4`; the unchanged verified identity manifest cites
the independently documented LMT relationship.

The first probe and the finished collector each returned the same 6,220 response
bytes, SHA-256 `2e74f5f6960faf192b7f1c7256c9b174453bf4ab63d85e935b2ff3debed75447`.
There are 14 source transaction IDs, action dates, modification numbers and
reported federal action obligations. A single ascending-action-date page is
terminal. The source supplies no last-modified or publisher-release timestamps
in this response. `batch3_contracts_acceptance_20261012.json` records the actual
capture, stored findings and replay; `batch3_contracts_live_20261012/` preserves
unaltered body bytes and individual observation manifests.

The service accepts an existing, source-verified prime-contract finding and
revalidates its exact recipient and dated relationship against the manifest:

```python
from pathlib import Path
from market_checker_app.services.award_transaction_history_service import (
    AwardTransactionHistoryService, ArchivedTransactionsClient,
)

collector = AwardTransactionHistoryService(
    store, archive_directory=Path("data/award_transaction_captures"),
    max_pages=2, page_size=100, max_requests=2, max_run_seconds=30,
)
summary = collector.run(subject_id="LMT", award_finding_id=existing_award_finding_id)

replayer = AwardTransactionHistoryService(
    store, archive_directory=Path("data/award_transaction_captures"),
    client=ArchivedTransactionsClient([Path(p) for p in summary["capture_manifests"]]),
    max_pages=2, page_size=100, max_requests=2, max_run_seconds=30,
)
replay_summary = replayer.run(subject_id="LMT", award_finding_id=existing_award_finding_id)
```

Findings use source `usaspending` and stage `award_transaction`, with a stable
source object comprising UEI, generated award ID and source transaction ID.
Changed source rows preserve another immutable version; repeated rows and
LIVE-to-ARCHIVE_REPLAY observations add no duplicate finding. Page positions and
capture metadata are retained outside the immutable content hash. Raw response
objects are deduplicated by their byte hash; observation manifests retain each
capture's times. LIVE requests share the existing provider lease and daily quota
under a distinct transaction-history identity namespace. They write no normal
award-coverage checkpoint. Replays charge no HTTP quota and explicitly identify
their acquisition mode; fixtures remain unverified and cannot be replayed as a
LIVE archive.

`COMPLETE` means that this one traversal reached terminal pagination with valid,
ordered, nonduplicated rows. `PARTIAL`, `NO_MATCH` and `ERROR` remain separate.
An empty page means only that this endpoint reported no rows for this exact
award. A terminal traversal is not an immutable publisher snapshot, complete
historical correction/deletion history, company coverage, company revenue,
company exposure, evaluation acceptance or Windows end-to-end acceptance.

`published_at` is the generic store's required surrogate field and equals the
actual response completion time. Details explicitly mark that publisher release
time is unverified. Availability and observation cannot precede actual capture
or relationship knowledge; old action dates never backdate analyzer knowledge.
Source date ties preserve response positions without inventing a transaction or
source-revision order within a date. No cumulative award snapshots are
differenced, and no scores or trading behavior are changed.

Primary endpoint contract:
https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/transactions.md
