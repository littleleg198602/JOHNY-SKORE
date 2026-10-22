Normal weekly/UI finalization previously persisted a forecast without enrolling
it into prospective history. This adapter connects that existing persistence
to `ProspectiveSpecialistHistory` through an explicit local opt-in. It produces
no forecast, changes no model/fitter/score and performs no source request or
source-file discovery.

Enable with `--register-prospective-history --prospective-audit-archive-root
/path/to/audit-root`, or the equivalent `AppConfig.prospective_history_enabled`
and `prospective_audit_archive_root` fields. Both default to disabled/unset.
The default finalizer does not construct an adapter, add an audit summary field
or perform audit I/O. The coordinator owns config/UI integration and excludes
only these two audit settings from the model configuration fingerprint.

The configured root must contain `prospective_source_manifest.json` with this
schema (the empty arrays are a schema example, not a source acceptance claim):

```json
{
  "schema_version": "prospective_forecast_source_manifest_v1",
  "forecast_sources": [],
  "label_observations": []
}
```

Each `forecast_sources` row specifies `ticker`, `benchmark_ticker`, optionally
the exact `snapshot_id`, and a list of `source_archive_paths`. Each label row
specifies `snapshot_id` and `path`. Paths must be explicit relative paths under
that root, pointing to existing content-named `yahoo_price_<hash>.json`,
`specialist_source_run_<id>_<hash>.json`, or
`prediction_label_<snapshot-id>_<hash>.json` archives. No glob, folder crawl,
fallback download or label-version selection occurs. Duplicate/ambiguous
entries, escapes, unsafe whole inputs and oversized manifests are refused.
Forecast/source/label counts and archive sizes remain bounded.

The hook runs immediately after the real `store.save_prediction_snapshots`
returns and rereads the authoritative existing rows in read-only SQLite mode.
Its clock comes from current physical UTC, never snapshot `as_of`. A source
must already have been available at prediction time; first registration must
precede the first forward NYSE session open for the exact frozen five-session
window. A partial store interruption still preserves only truly committed
rows. Replays retain original immutable registrations; a missing audit binding
can be completed after restart. Missing inputs/clocks/sources report WAIT or
PARTIAL and do not change the stored forecast or ask a provider for data.
The adapter and recorder share a physical-clock high-water mark: a backward
clock between manifest observation, registration and label attachment is
refused before a new history record is published. Replays keep their original
physical timestamps; a restarted process also checks saved settings clocks.
Malformed per-row archive clocks stay isolated to that row, so a later invalid
source cannot erase the truthful count of earlier immutable registrations.

Exact safe manifest bytes, hash, separate audit settings and real first
observation clocks are recorded in immutable `adapter_audits/settings_*.json`.
Immutable binding files link those inputs to the original registration ID/hash.
These records sit under `<audit-root>/prospective_specialist_history` and do not
enter model inputs or its configuration hash. A later settings observation does
not replace the original availability timestamp inside an older source archive.

After optional existing label resolution, only explicitly listed existing label
sidecars can attach. UNKNOWN/absent publisher revision timing, incomplete action
histories, wrong/missing exact sessions and future label clocks stay excluded.
The adapter neither discovers new sidecars nor enables label price loading.
Results appear only when enabled under `prospective_forecast_history` with
`registration`, `label_attachment` and bounded `history` diagnostics.

The normal-pipeline integration cases are explicitly synthetic, with injected
fixture clocks and offline OHLC. They verify real normal forecast persistence,
ordering, default behavior, immutable model/signals, a partial commit/restart
and explicit later label clocks. Initial failed fixture attempts are preserved:
the first incorrectly forbade the normal pre-persistence OHLC fetch; the second
froze `datetime` but missed the producer's `utc_now`, so the real source clock
guard correctly refused registration. No clock/hash assertion was weakened.
Two additional peer-found regressions cover a late manifest followed by a
clock rollback and a content-addressed source with missing/non-string clocks.
Both remain fixture-only inputs; the latter is also exercised through normal
two-forecast persistence so its PARTIAL report preserves the first saved row.

The actual retained runtime database is inspected in `mode=ro` only. It has no
`prediction_snapshots` table; authentic forecast registrations and real eligible
labels remain zero. No synthetic forecast is inserted into that database or
used as acceptance. Older evidence, publisher clocks, prices and labels remain
byte-identical. Current Windows, genuine forward observations, dated publisher
revisions, historical/OOS evaluation and all-specialist acceptance remain separate.

Reproduce into a new directory, without HTTP or replacing existing outputs:

```sh
python evidence/batch7_evaluation_20261015/reproduce.py \
  --output-directory /tmp/batch7-forecast-adapter-qualification \
  --retained-db /path/to/an/existing/market_checker_history.db
```
