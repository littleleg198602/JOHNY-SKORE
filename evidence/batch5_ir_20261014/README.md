This batch adds an explicit primary IR HTTP collector. Every feed, IR portal,
announcement and redirect hop reserves the central durable `rss` request ledger
before I/O. The provider lease, daily 3-subject/6-request limits, physical UTC
day, persistent cooldown and same-day subject retry restriction survive reloads
and scope changes. Completed original captures are immutable. A historical UI
cutoff never controls a LIVE request or local review availability.

The real Microsoft run used the existing application database on
2026-10-14, from 00:15:32.956308 to 00:15:47.562423 UTC. Three requests returned
HTTP 200. The feed and selected October 7 announcement are archived unchanged.
The IR portal contained an assigned access-value pattern, so its **entire body
was withheld**. Only its original SHA-256, size, safe URL and physical/header
clocks remain. There is no sanitized, encoded, compressed or replacement body.
The result is `PARTIAL / WITHHELD_SENSITIVE_BODY`, with **zero LIVE source
verification, zero new verified issuers, and zero new findings**. The ledger
contains three prospective reservations: `RETURNED`, `FAILED`, `RETURNED`; the
provider lease was released. Earlier one-off October 13 requests were not
retrospectively reconstructed or silently reset.

`runtime_report.json`, `ledger_proof.json` and capture receipts retain the actual
run. `build_review.py` checks all original hashes, complete-body sizes and
reservation/capture clocks, then replays a deliberate scoped negative control:
the fetched RSS feed cannot serve as the withheld IR portal. The exact RSS/page
title, quotation and publication clocks are still preserved, while this control
remains `PARTIAL`. It performs no HTTP and proves no complete negative issuer
search. The primary page's HTTP Last-Modified (13:17:44 UTC) and declared/RSS
publication time (20:17:43 UTC) on October 7 are retained as distinct clocks.

The collector imports a separate existing `ir_primary_source_review` only when
all required safe captures are present. A nested source-only positive remains
`UNVERIFIED` in the findings database; the original RSS lead stays unchanged.
Issuer attribution, instrument identity, claim truth and scoring remain false.
Positive runtime behavior in the focused tests uses explicit synthetic
transports, not LIVE acceptance. The completed LIVE capture preceded final
offline-control and socket-deadline refinements; it is not a final-HEAD
end-to-end acceptance claim. No Windows or historical/OOS evidence is added.

Runtime API:

```python
IRPrimaryScoutService(store, archive_root=None, client=None,
                      max_requests=6, max_run_seconds=60).run(
    ticker, feed_url=feed_url, authority_url=authority_url,
    publisher_hosts=("publisher.example",),
    candidate_id=selected_rss_finding_id, requested_as_of=requested_cutoff,
)
```

An explicit original URL in the bounded captured feed can replace the selected
candidate ID. Noncanonical ticker hints fail before quota/lease/HTTP. Archive
root defaults to `db.parent / ir_primary_captures`. The lane CLI is
`python -m market_checker_app.ir_primary_runner`; it lazily calls the
coordinator's `run_primary_ir` wrapper for immediate completed source-run
archival. It refuses a nonexistent database and exits 2 for partial/retry
states. `SOURCE_VERIFIED` is only source verification, and status fields retain
`WAIT_QUOTA`, `LEASE_LOST`, `BUSY`, `RATE_LIMITED` and `ACCESS_BLOCKED` separately.

Next available work is a primary IR portal whose entire original body can pass
the disclosure gate, followed by independently dated issuer/instrument identity
evidence and broader measured coverage. No source budget can be reset to obtain
that work within the same subject/day. Shared registry, scoring, trading and
the canonical 687 universe are unchanged.
