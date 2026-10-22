# Independent captured-runtime persistence review

The root finalizer checked captured input readiness on entry, then performed
macro/counterparty work and candidate preparation before the actual forecast
writes. Independent tests reproduced three failures against the exact frozen
source bytes in `current_source_pins.json`:

| Trigger after a successful entry check | Original persisted fixture rows | Required rows |
| --- | --- | --- |
| Next NYSE close completes; selected data lacks that session | baseline1; reported SUCCESS | baseline0 |
| Explicit manifest changes during macro processing | baseline1; reported SUCCESS | baseline0 |
| Manifest changes during candidate preparation | baseline1, candidate1 | baseline1 unchanged, candidate0 |

`prefixed_confirmed_tests.json` and its original log retain5 executed tests,
3 failures and0 errors/skips. Two positive checks passed: an initial WAIT_DATA
stops before weekly store initialization, and a normal captured fixture keeps
missing metadata, exact source pins, UNKNOWN revision timing and strict_PIT=false
while live-provider and persistent-cache constructors are forbidden.

Root added a guard immediately before each baseline/candidate persistence call
and requires the earlier READY input binding to exist. The same five tests,
with identical source SHA, then passed5/5 with0 failures/errors/skips. The
previously committed baseline hash remains identical when the later candidate
write is rejected. `postfixed_tests.json` and `postfixed_source_pins.json` pin
the exact owner code that passed; the original failing code is not replaced.

`source_snapshots/` holds exact original/fixed code bytes for this review.
These are code diagnostics, not copied market/source capture bodies. No core
file was edited by this peer agent. `run_review.py` runs only the five independent
regressions against an explicitly selected source root and refuses changed
code during qualification. It records each phase write-once.

The earlier `prefixed_tests` log contains5 fixture setup errors: the production
provider correctly refused fixture-origin records without its explicit fixture
clock, and the initial weekly fixture omitted required arguments. That log is
preserved and is not defect evidence. The fixture was corrected before the
confirmed reproduction; no production guard was loosened. An earlier attempt
also aborted before tests because evaluation's provider bytes had changed; both
versions remain pinned separately.

All data and SQLite databases used by the tests are temporary synthetic unit
fixtures with normal closing connection handles. There were0 source HTTP
requests and no production DB reads/writes, quota changes, actual forecasts,
price-label histories, registry adoption, scoring or trading changes. No full
baseline suite or hosted/Windows/OOS qualification is claimed.
