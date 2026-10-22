# Explicit user-Windows readiness capture

This lane adds one default-off production recorder. It observes existing local
inputs and publishes an exclusive immutable receipt. It executes no pipeline,
forecast, registration, label resolution, source request or database migration.
Every receipt has scope `READINESS_CAPTURE`; user-Windows end-to-end acceptance,
strict PIT, historical OOS and full specialist acceptance remain false. This is
not completion of any of the 21 analytical specialists.

## Integration API

```python
from market_checker_app.services.user_windows_readiness_capture import (
    UserWindowsReadinessCapture, load_user_windows_readiness_receipt,
)

result = UserWindowsReadinessCapture(enabled=True).capture(
    output_path=explicit_new_receipt_path,
    tickers=["AAPL"],
    db_path=explicit_existing_history_db,
    market_manifest_path=explicit_market_manifest,
    market_archive_root=explicit_market_archive_root,
    maximum_rows=64,
)
receipt = load_user_windows_readiness_receipt(result["receipt_path"])
```

The constructor defaults to `enabled=False`. Disabled calls create no output and
inspect no clocks, provider or database. The manifest and archive root must be
supplied together, and the output must be separate from inputs. Subjects are an
explicit unique subset of the canonical 687; the actual captured provider also
checks all 12 benchmarks. Maximum sample size is 1–64 rows per fixed table.
No discovery, online fallback or persistent market cache is used.

Enabled calls return `status`, `scope`, `receipt_created`, `receipt_path`,
`receipt_sha256`, and `receipt`. Normal status values include
`UNSUPPORTED_PLATFORM`, `HOSTED_CI_NOT_USER_WINDOWS`, `WAIT`,
`READINESS_CAPTURED` and `FIXTURE_ONLY`. `readiness_status` is `WAIT` or
`READY_INPUTS_ONLY`; neither establishes a production run. Invalid paths,
collisions, input drift and clock rollback raise without publishing a receipt.
An existing receipt is never rewritten.

The receipt pins physical source/manifest bytes, actual native OS/Python,
physical aware UTC plus monotonic clocks, and fixed boolean CI indicators.
Environment values, private source payloads and unselected database JSON are not
exported. Existing DB access uses a bounded authorized `mode=ro` connection that
is deterministically closed. Existing journal files prevent SQL opening; they
are only byte-pinned and are never replayed or removed. Existing but unread
tables have `present=null`, `sampled_rows=null`, `observation_status=UNOBSERVED`.
The reader's platform does not establish where an earlier stored run executed.

Injected observation/monotonic clocks or runtime probes are explicitly
fixture-only. Marked synthetic market input is visible as fixture evidence and
cannot become native live acceptance. All paths, source files and receipt
contents are bounded and guarded against symlinks/junctions, unsafe bodies and
input/output collisions. Publication revalidates code, current exact captured
source/parent binding, DB existence/main/journals and both clocks **after** the
temporary receipt's fsync, immediately before its exclusive link.

## Actual retained input result

The two native Linux receipts preserve their true physical observation times.
The original `actual_linux_readiness.json` (SHA256 `13edc710…`) belongs to the
pre-null-semantics producer `0a2354b9…`; it is unchanged. The final-producer
receipt is `actual_linux_v2/actual_linux_readiness.json` (SHA256 `bf1691aa…`,
producer `28551e06…`). Both report `UNSUPPORTED_PLATFORM`, readiness `WAIT`,
zero usable market inputs and user-Windows verification false.

The explicitly selected failed-acquisition manifest is absent. Its consumed
source plan was not retried. The retained history DB is 294,912 bytes and remains
SHA256 `6d6a7450d9a66bb0fbc3c006deb3aca6c595fcdf1ca682561b476a2f4d9400af`.
Its existing 8,720-byte `-journal` is SHA256
`193c4c631c14924bad4d42edeaa320120125c6b674efc55da4cb015534573bb3`.
`WAIT_ACTIVE_DATABASE_JOURNAL` prevented SQL opening, so current table/forecast
counts are **unknown**, not proven zero. The final receipt represents those
facts with null metadata. No DB copy, initialization, journal replay/removal or
mutation occurred. Actual new forecasts, registrations and labels are zero.

The new module was not yet in the baseline Git tree when the actual receipts
were produced. They honestly record baseline HEAD `32adb8e…`, physical candidate
source hashes and `git_head_is_exact_pinned_source_identity=false`; they do not
pretend that the baseline commit contained the new producer.

## Qualification and preserved failures

`qualification_v4/tests.json` and its full log qualify the final producer:
**41/41**, no failures/errors/skips/expected or unexpected outcomes. Final test
source is SHA256 `572050d1…`. `peer_recheck_final` runs the independent peer's
unchanged seven methods against that same producer: **7/7**, all other counts
zero. Populated market data and stored rows in all tests are deterministic
synthetic fixtures; they are not genuine forecasts or acceptance evidence.

Earlier 35/41-method producer/test/log versions are retained. The independent
peer's original six post-fsync failures, whole before/after producers, seven
unchanged tests, runner and complete reports are byte-identical copies from Git
commit `02e3e6f773b8906893afe301d79e5e815c9cda26` in
`independent_peer_originals`. The failure was a real late-publication drift gap;
the correction is qualified by the unchanged regressions.

`verify_saved_evidence.py` checks the actual receipts, final qualification pins,
original failures and every prior tracked file in this lane. Its first version
incorrectly compared the nine existing CRLF `.bat` checkout files directly with
LF Git blobs; that whole verifier and exact diagnostic are retained in
`verification_v1`. The corrected verifier compares those exact nine files with
Git's configured whole checkout bytes and compares production/test/evidence
files with raw blobs. It normalizes or rewrites no file. The final verification
pins all 218 pre-existing production files, 188 tests and 1,578 prior evidence
files unchanged. Coordinator shared-file integration is separately owned and
must receive its own qualification. No full suite was repeated here.

Next work remains genuine complete market inputs, a quiescent explicit saved
database, a real native user-Windows same-process production run with its own
authenticated run receipt, later exact-session labels and eligible historical
evaluation. UNKNOWN publisher revision remains excluded from strict PIT/OOS.
This recorder never automatically starts any of those operations.
