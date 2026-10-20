# Explicit same-process Windows analysis capture

This adds one default-off callable for a selected normal analysis run. It is an
observational recorder, not acceptance of the 21 specialists. No genuine native
Windows pipeline ran during development. All populated analysis results in the
focused tests are explicitly injected synthetic test doubles using temporary
databases and the real production captured-data provider/persistence code.

```python
from market_checker_app.services.user_windows_analysis_capture import (
    UserWindowsAnalysisCapture, load_user_windows_analysis_receipt,
)

result = UserWindowsAnalysisCapture(enabled=True).capture(
    output_path=explicit_new_receipt_path,
    tickers=["AAPL"],
    db_path=explicit_analysis_database,
    market_manifest_path=explicit_captured_manifest,
    market_archive_root=explicit_archive_root,
    progress_callback=None,
)
```

Disabled calls perform no I/O. Actual native Windows, consistent native OS
facts, non-hosted execution and complete current captured inputs for the selected
canonical subjects plus all 12 benchmarks are required before production
pipeline/store initialization. Missing/unsupported input never falls back to a
network provider. Injected clocks/runtime controls require an explicit fixture
pipeline factory to execute and permanently mark the observation as synthetic.

The production route invokes `PipelineService.run` in this call/process with
default model settings and explicit captured input mode. RSS, MT5, Yahoo
metadata, persistent market caches and prospective recording are disabled.
Network entrypoints, including requests, sockets and the installed curl-backed
session, are denied. Default weights, thresholds, model/fitter code, original
config/hash implementations and the canonical 687/60-second budget are unchanged.
Operational flags and effective/default config hashes are recorded separately.

Real `SQLiteStore.save_run` calls are guarded immediately before persistence.
Their exact serialization payloads/returned IDs are bound to closed, bounded,
`mode=ro` rereads of the actual run and signal rows. The receipt records original
physical file/source/manifest/DB hashes, original source clocks, physical UTC and
monotonic completion clocks, exact persisted rows/counts and partial outcomes.
Existing journals prevent SQL opening/replay. Output paths, inputs and sources
are guarded against symlinks, junctions, hardlinks, escapes and overwrites.

Source, raw-parent replay, DB state and both clocks are checked again **after
temporary receipt fsync, before exclusive publication**. Drift cannot publish a
qualified receipt: it produces an explicitly unqualified snapshot retaining the
actual saved subset/counts. An exclusive-output collision preserves the winner
and returns `RECEIPT_NOT_PUBLISHED` with `receipt_created=false`, null path/SHA and
the unqualified observation in the result. It does not erase a saved run.

Published results use `receipt_path` and `receipt_sha256`, not `path`.
`scope=SAME_PROCESS_SELECTED_ANALYSIS_RUN`. A direct genuine native run may derive
`native_windows_selected_analysis_verified=true` only for that selected run.
`user_windows_end_to_end_verified`, full specialist acceptance, strict PIT and
historical OOS remain false. UNKNOWN publisher revision stays excluded. Analysis
signal rows are not claimed as genuine prospective forecast registrations or
labels. Hashes and origin fields are not independent publisher authentication.

`load_user_windows_analysis_receipt(path)` verifies content and scope only. It
returns `{record, content_hash_verified, loaded_json_is_not_execution_proof,
runtime_execution_reverified, native_windows_selected_analysis_verified}`. The
last two fields are always false on loading, even if the stored record claims
otherwise. Stored JSON cannot recreate same-process execution evidence.

## Actual result

`actual_linux_refusal.json` is a real physical observation at
2026-10-19T00:41:45Z, SHA256
`ee90438b2bba1ca84c0106db6ed90666823f15abab95e31b7d582b0f4f991919`.
It reports `UNSUPPORTED_PLATFORM`, actual Linux/Python 3.12.14 and fixed CI
booleans, with no injected platform/clock controls. Provider, SQL reader, store
initializer and network guard call counts are all zero. Market preflight and DB
observations are null/unobserved. No data absence or coverage is inferred. The
retained journal-bearing DB was not referenced, opened, copied, replayed,
hashed, reset or changed. No pipeline, forecast, registration or label ran.
The exact candidate producer is pinned; its new module was not yet in the
observed baseline Git tree, and the receipt says so.

## Qualification and preserved failures

Final producer SHA256:
`4b5a31392799b427f10d371009fe3d136fd31d546e6cd851da14b728d1537e43`.
Final test SHA256:
`89069e400360316265dcc1b64128d752a52c1bdb73f406bbade47f1c2188b201`.
`qualification_v3` contains the full final **37/37** log, 54.113601 seconds,
all other qualifiers zero. This is focused synthetic boundary qualification,
not a full-suite rerun or Windows run acceptance.

The initial 33-method successful producer/test/log are preserved. The subsequent
late-publication collision reproduction preserves its whole producer/test and
one actual failure/zero errors: the old callable raised after a real temporary
save instead of returning the saved subset. The fixed unchanged regression now
passes. Independent healthcare peer original before snapshots/logs are copied
byte-identically: 10 methods, four failure entries, zero errors on boolean/float
returned IDs and foreign returned signal run/id claims. The fix requires exact
integer run IDs and checks optional returned row identity claims against the
persisted identities. Independent after-fix qualification awaits this exact
owner commit and is not preclaimed here.

`verify_saved_output.py` verifies the final qualification, actual refusal,
preserved failures and all prior tracked production/test/evidence bytes against
baseline `6ba4f44…`. The exact nine configured CRLF batch launchers are compared
with Git's whole configured checkout output; no file is normalized or rewritten.
All prior 224 production files, 200 tests and 1,941 evidence files are unchanged.
Root owns separate CLI/UI integration, combined qualification and publication.
The preserved original peer log includes unittest's trailing space before a
subtest diagnostic. Its exact original bytes are retained; production and test
source whitespace checks pass independently.

Next work remains genuine complete current market inputs, an authorized
quiescent output DB and an actual native non-hosted Windows invocation. Later
prospective forecasts/labels and historical/OOS evidence remain separate work.
Full specialist acceptance remains 0/21.
