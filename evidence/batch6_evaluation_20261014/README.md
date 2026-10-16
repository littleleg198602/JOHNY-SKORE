This batch adds `ProspectiveSpecialistHistory`, an append-only recorder for
already persisted forecasts and their exact later labels. It creates no
forecast, fits no model and changes no score or target formula.

The first registration reads the existing SQLite snapshot in `mode=ro`, checks
its original hash and freezes its features, baseline, source IDs and all six
NYSE sessions of the current five-session target. Registration must happen
before the first forward session opens. Explicit source and feature knowledge
cannot follow the original prediction; explicit training clocks must precede
it strictly. Replays preserve the first physical registration time. Changes to
the forecast or source pins are rejected.

A later attachment reads that same stored, resolved snapshot and its actual
immutable label sidecar. Both exact asset and benchmark windows must match,
recompute to the unchanged target and carry complete action provenance plus an
explicitly dated publisher version and source content hash. A missing endpoint
or middle session cannot shift the target. Publisher timing UNKNOWN or absent
remains excluded. The attachment preserves the whole original label sidecar,
its source hashes and its actual later attachment time. It cannot backdate
availability to a market close or an older file capture. Replays are idempotent
and conflicting rewrites are rejected.

`read_report(as_of=..., training_before=None, maximum=1000)` is bounded and
read-only. It returns safe diagnostic rows, real eligible snapshots, separate
fixture counts and explicit reasons. Training requires forecast registration,
prediction and attached label knowledge strictly before the new prediction.
Partial or corrupt history fails closed. Source pins are declared evidence
references; their existence does not independently prove authentic publishers
or complete feature coverage. All acceptance and activation flags stay false.

The actual retained runtime database has no `prediction_snapshots` table.
No authentic forecast is registered or synthesized for this handoff. Actual
registrations and attached real labels are therefore zero, with
`WAIT_FORECAST_CORPUS`. The 16 already captured AAPL/SPY price versions are read
and hash checked without HTTP; their publisher revision timing stays UNKNOWN.
The original two HTTP bodies and every older tracked evidence file retain their
exact original bytes. These prices do not create a historical forecast.

The positive and negative regressions use explicit deterministic unit fixtures.
One production-schema fixture exercises eligible-row behavior and remains a
fixture test. Test results are neither an actual OOS sample nor a new hosted
Windows result. The first development attempt had 50 fixture setup errors from
a missing `ensure_schema`; that setup error was corrected and is recorded
separately from the final captured results.

The earlier 85-test combined capture is preserved with its original code
hashes in `prior_85test_handoff.json` and `tests.log`. Final structural review
added consistency checks for the stored forecast source ID, exact source
archive filename and advertised price capture hashes, with three new negative
regressions. `handoff.json` and `final_validation_tests.log` qualify the final
57-method lane suite. Previously qualified unrelated tests are not repeated.
The original 50-method development success survives as an explicitly marked
tool summary; its interrupted scratch log and exact source hash are unavailable.
A later interpreter launch failed before executing tests; both that interruption
and the restored unchanged package environment are recorded separately.

Reproduce into a new evidence directory (existing outputs are refused):

```sh
python evidence/batch6_evaluation_20261014/reproduce.py \
  --output-directory /tmp/batch6-evaluation-reproduction \
  --db /path/to/an/existing/market_checker_history.db
```

Runtime integration belongs to the coordinator. Use the existing database's
parent `prospective_specialist_history` directory, register only a selected
authentic pending record, then attach its physically observed exact future
label. An already expired forecast cannot become prospective by running this
recorder now. Missing publisher revision history, source coverage, genuine
future observations and all-specialist acceptance remain outstanding.
