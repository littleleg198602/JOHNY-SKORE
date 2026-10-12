# Historical specialist diagnostics

Double-click `Vyhodnotit_Specialisty.bat` to create
`outputs/specialist_evaluation_latest.json` from the existing
`outputs/market_checker_history.db`. This does not fetch prices, news or scout
data, resolve labels, create a database or modify archived snapshots. Use the
existing label resolver first when mature predictions still have PENDING labels.

The report lists all 21 specialists, the archived features that actually exist,
missing or rejected features, unknown/unavailable labels and matched exploratory
model comparisons. `INSUFFICIENT_DATA` is a valid report outcome, not a failed
run or a specialist completion claim. `MARKET_CLOSED`, `DATA_GAP`, `PENDING` and
`UNAVAILABLE` label states are counted separately and never become wins/losses.
The current snapshot table normally stores the latter two states; the former
two are reported when present, without inventing them from weekends or blanks.

## Reproducing a report

```bash
python -m market_checker_app.specialist_evaluation \
  --db-path outputs/market_checker_history.db \
  --output-path outputs/specialist_evaluation_latest.json \
  --as-of 2026-10-11T10:00:00+00:00
```

Fixed snapshots, cutoff and options yield the same JSON. The report carries a
sorted snapshot ID/hash digest, feature paths, model artifact IDs, training IDs,
fold cutoff and latest training-label timestamp. CLI reading uses SQLite
`mode=ro` and explicitly closes the connection, including on Windows.

Snapshots must match the existing `feature_snapshot_v1` hash contract. SQLite
label-resolver hashes containing `created_at` and initial snapshot hashes
without it are supported. Invalid hashes, duplicate snapshot IDs, unsupported
contracts, future snapshots and premature labels are excluded or accounted for.
Feature `available_at`/SEC `availability_at` must be no later than the archived
prediction. Labels must be available strictly before each training cutoff.
Training and evaluation use at most one row per ticker/week and remove
overlapping five-session label horizons. A delayed resolver does not lengthen
that economic horizon, but its label still cannot enter training before its
actual observed-at time. Any stored collector observed-at time is also checked
against the snapshot cutoff.

Predeclared comparisons train a market-only logistic and an augmented logistic
on exactly the same training IDs, then score the same out-of-sample IDs. These
measure SEC fundamentals and archived regulatory/governance/forensic/claim
groups. Blank agent snapshots with zero numeric values count as missing when
their missingness flags say no evidence. Results include Brier score, ranking
IC, top-decile excess price return, directional accuracy and paired weekly
bootstrap differences. Default minimum history remains 200 training samples,
200 evaluation samples and 12 distinct evaluation weeks. Tiny histories show
descriptive metrics and suppress intervals until both the sample and week
minimums are met. Each interval also requires enough usable metric weeks.
The deterministic NumPy implementation uses the existing shadow model's fixed
logistic equations (learning rate 0.15, L2 0.05, bounded logits), with a parity
test for both the market-only and augmented probabilities. It does not choose
features or settings from measured performance.

The archived regulatory group combines contracts, regulation and guidance.
Its measured lift cannot be assigned to FDA, FDIC, 13F or another collector.
The report explicitly marks specialists without attributable archived features.
Each of its 21 specialist rows includes snapshot, observed-feature,
market-matched and usable-label counts, archived feature paths and its associated
feature-group coverage. Counts are unions of snapshot IDs, so a specialist with
two groups is not counted twice. Zero history is reported as zero; source
collectors without attributable fields receive no credit from an aggregate
group associated with a broader specialist.
Coverage is archive-feature coverage, not applicable-provider coverage. The
current canonical universe is not a historical survivor-free universe. Matched
evidence cohorts are selective, and all comparisons are exploratory.
No comparison changes scores, thresholds, ranking, orders, specialist inventory
or the completion rule. `acceptance.out_of_sample_evaluation_verified` remains
false: this report is a diagnostic for review, not a completion certificate.

## Application and service integration

In the application, expand the specialist status section and click
`Vyhodnotit uloženou historii specialistů`. The report is generated explicitly
from the selected database, then retained for summary tables and a full JSON
download. It is not fitted on every application rerender or collector batch.
Changing the selected database prevents a previous database's report from being
shown. The command-line launcher provides the same read-only evaluation.

After existing snapshot persistence/label resolution:

```python
from market_checker_app.services.specialist_evaluation_service import (
    build_specialist_evaluation_report,
)
report = build_specialist_evaluation_report(
    store.read_prediction_snapshots(), as_of=run_finished_at,
)
```

When integrating a separate scheduled history report, keep its execution
explicit and its completion status separate. Folds retain all training IDs for auditability, so large
histories can create large JSON downloads; summarize counts and metrics in the
UI instead of rendering every fold and sample. The application shows all 21
specialist counts and comparison sample/week counts; the download carries the
full audit.

Source-specific collector evaluation requires future immutable snapshots to
store their actual source-feature values, feature version, available-at time,
dated issuer/instrument attribution and explicit collector result/missingness.
Do not backfill earlier snapshots with current scout findings.
