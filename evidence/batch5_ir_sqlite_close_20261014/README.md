The Windows run reported by the coordinator (`38551067090`, published head
`e550b334`) executed 1371 tests and recorded 12 errors. Ten were
`TemporaryDirectory.cleanup` errors for the IR fixture's `shared.db` after its
functional assertions completed. The original Windows result is a failure;
the coordinator retains its raw artifact and handles the separate evaluation
fixture errors.

The IR test fixture used `with sqlite3.connect(...)` for a ledger reader and
the lease-loss write callback. SQLite's transaction context commits or rolls
back but does not close the file handle. Both now use one fixture-only helper:
the normal transaction context exits first, then `contextlib.closing` calls
`close()` on successful or exceptional paths. Existing quota, lease, exact
original-byte/hash, publication-clock and source/issuer assertions are retained.
Temporary-directory cleanup is unchanged.

Two new regressions hold strong references to tracking SQLite connections and
assert one explicit `close()` call per actual ledger-read/lease-write connection.
They also confirm closed connections reject further SQL. A real commit and an
exceptional rollback retain the correct persisted rows while all three
connections close. These checks fail on Linux when a connection is left open;
garbage collection cannot make them pass. No cleanup suppression, garbage
collection, retries or leftover-file deletion is used.

Production IR collection and CLI use `ScoutStore`, whose
`ClosingSQLiteConnection` already closes after transaction exit. Production
code is unchanged. The historical `capture_live.py` contains two read-only raw
SQLite contexts; that original diagnostic helper and every hash-pinned prior
evidence/helper file remain byte-identical. The helper is not executed in this
fix. Its connection lifetime remains a historical diagnostic limitation.

`before.json` records the exact pre-fix file hashes and all prior evidence
hashes. The new handoff records after-fix hashes, source/test totals and zero
HTTP. A new hosted Windows run is required to qualify the integrated tree; the
focused local checks do not claim that result or specialist acceptance.
