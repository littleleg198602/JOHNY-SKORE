# Exact award capture revision and presence audit

The read-only comparison reopens one to five manifests per side, verifies the
unaltered source body hashes, exact award/query/acquisition scope and original
capture clocks, and revalidates terminal pagination. A receipt's COMPLETE label
cannot replace these checks. Existing findings, source bytes and per-capture
observation/first-ingestion clocks are preserved.

Shared transaction IDs with changed payload hashes are `PAYLOAD_CHANGED`.
Their reported modified values remain distinct from capture time and publisher
revision time. A row absent from a later fully validated traversal is
`NOT_REPORTED_IN_LATER_TRAVERSAL`, with the traversal's observation window.
The API's offset pages do not guarantee a publisher snapshot. This observation
does not establish a deletion or its date; `publisher_revision_at` and
`publisher_deleted_at` remain null. Incomplete/ambiguous pages yield
`PRESENCE_UNRESOLVED`. Wrong scopes, unordered windows, invalid bytes and
withheld bodies cannot provide a negative or source revision claim.

The actual old probe and collector requests both returned the same 6,220-byte,
14-row body on 2026-10-12, SHA-256
`2e74f5f6960faf192b7f1c7256c9b174453bf4ab63d85e935b2ff3debed75447`.
Their original capture times remain 14:42:14.095355 UTC and
15:12:09.077127 UTC. The legacy probe's schema is annotated by an explicit
ARCHIVE_REPLAY wrapper, with its original metadata/body hashes and successful
Content-Length/byte-count provenance recorded in `probe_import_receipt.json`.
No original bytes, dates or metadata were rewritten.

`lane.json` records 14 unchanged rows across those two captures, no actual
changed/absent transaction, and `SAME_SOURCE_CAPTURE` for replaying the collector
capture. The audit timestamp is actual 2026-10-14 UTC; the caller's `as_of` does
not backdate it. The verification reconstructs the committed old 15 findings and
14 observations for offline read-only checks, and verifies that their full
content and first-ingestion clocks remain identical. It does not create or
reset a source request ledger.

The original task databases were inspected read-only. They contain no durable
USAspending quota tables or verified prime-award targets for additional live
requests. **0 new source HTTP requests** were made. Wider UEI/award coverage,
prospective captures using the original shared lease/quota, publisher revision
history, relevant denominators, OOS and actual user Windows acceptance remain
open. This is not company-wide coverage, revenue or specialist acceptance.

`tests.json` and `tests.log` bind the final code/test hashes and **86/86** focused
Linux tests with no failures, errors or skips. The initial 85-case diagnostic
retains one fixture failure: its capture windows touched instead of overlapping.
The fixture now overlaps by half a second; the strict time gate was unchanged.
Synthetic changed/absent cases are not new live evidence.

Runtime hook (no comparison archive is written by this method):

```python
report = collector.audit_capture_versions(
    subject_id="LMT", award_finding_id=selected_verified_award_finding_id,
    baseline_manifests=[baseline_manifest_path],
    current_manifests=[current_manifest_path],
    as_of=requested_utc_cutoff,
)
```

The caller may persist this report as immutable JSON alongside its actual audit
timestamp and referenced manifest hashes. It must retain the explicit unknown
revision/deletion claims. Manifest paths, rather than observation IDs alone,
are needed to verify the whole traversal. Only names recorded for the selected
award should be offered in UI/runtime selection.

Return statuses: `INVALID_COMPARISON`, `INCOMPARABLE_CAPTURE_SCOPES`,
`SAME_SOURCE_CAPTURE`, `UNORDERED_CAPTURE_WINDOWS`,
`COMPLETE_CAPTURE_COMPARISON`, `PARTIAL_CAPTURE_COMPARISON`.
Events are bounded to one million serialized bytes; truncation is explicit and
prevents a complete comparison status. No network, registry, score, trading,
license or account behavior is added.
