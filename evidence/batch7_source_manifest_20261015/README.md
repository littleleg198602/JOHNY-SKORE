# Explicit prospective source manifest preparation

This helper prepares adapter configuration from explicit existing archive
paths. It never discovers files, calls sources, opens a database, creates a
forecast/label, attaches a label, changes scores or verifies historical
acceptance. The adapter and recorder still validate the exact persisted
forecast, prospective session and later label before their own publication.

API:

```python
service = ProspectiveSourceManifestService(existing_audit_root)
prepared = service.prepare(
    forecast_sources=[{
        "ticker": "AAPL",
        "benchmark_ticker": "SPY",
        "source_archive_paths": [explicit_relative_asset_path,
                                 explicit_relative_benchmark_path],
        # Optional: "snapshot_id": exact_stored_snapshot_id
    }],
    label_observations=[],  # Optional existing {snapshot_id, path} rows
)
service.publish(prepared)  # Immutable version only; no active change
service.publish(prepared, activate=True,
                expected_current_sha256=prepared["current_manifest_sha256"])
```

The manifest has exactly `schema_version`, `forecast_sources` and
`label_observations`, with schema `prospective_forecast_source_manifest_v1`.
There are at most 687 rows of each kind, 24 source paths per forecast and
512,000 manifest bytes. Source paths must be explicit root-relative accepted
archive filenames. The complete unique selected set is bounded at 64 MB;
individual price/run/label caps match existing loaders. Missing files,
symlinks/junctions, foreign or missing tickers, ambiguous scoped/unscoped
entries, unsafe whole bodies, changed hashes and future clocks fail before
manifest publication. Source files are never changed.

Preparation uses the recorder's actual source guards. Yahoo observations
must belong to the chosen asset/benchmark. A specialist source-run needs a
recorded ticker reference or an explicit shared/dataset context; a canonical
687-entry universe alone supplies no issuer evidence. Unknown Yahoo publisher
revision timing stays UNKNOWN. Selected label files retain their exact IDs,
hashes and physical clocks; structural selection is not label acceptance.

`publish` revalidates selected pins and clocks. It preserves immutable
`prospective_source_manifest_versions/prospective_source_manifest_<SHA>.json`
files with exact original bytes. Existing active raw bytes, including their
original formatting, are preserved before replacement. The active file uses
a separate inode. Activation requires explicit `activate=True` and an exact
expected current SHA (null only while no active file exists). A writer lock,
two CAS checks and atomic replacement protect competing publishers; busy or
stale writers fail without silently changing the active file. An existing
lock is not automatically reclaimed. A failed atomic replacement leaves the
old active file and a recoverable immutable new version.

One normalized, shared physical-clock high-water spans prepare, publish,
revalidation and pre-publication checks. It rejects a clock rollback before
version/active publication. The returned `published_at` is the checked clock
immediately before atomic publication, explicitly labeled
`PHYSICAL_CHECK_BEFORE_ATOMIC_PUBLICATION`; it is not sampled after replacing
the active file. `observation_clock` injection is solely for marked unit
fixtures, not a supported historical backfill mode. The earlier unqualified
implementation and the read-only peer diagnostic are retained in
`diagnostics/`.

The actual preview selects three retained originals: AAPL/SPY price
observations and the MSFT RSS source-run. Its two manifest rows and
509,173 unique source bytes produce raw manifest SHA
`e45df361919206e1c7930efefb6c6838180433663c209915e92c1e245c352130`.
Original capture clocks remain 14 October 2026 UTC. Preparation has its own
physical 15 October clock; `retained_preparation_current.json` is the final
producer's read-only validation. The earlier physical preparation and exact
unchanged preview are also preserved. No active manifest, database,
registration, forecast or label was created by this actual replay.

The final focused 31 tests cover price/run/label archive types, prospective
clock guards and false-history attempts, source scope, whole-body refusal,
path boundaries, hashes, publication failure recovery, original versions,
concurrent writers, stale CAS, separate inodes and clock rollback. All pass.
Their generated forecast bodies and temporary label/source files are
deterministic test fixtures, not live acceptance. No new Windows, source collector or OOS acceptance is
claimed by this lane.
