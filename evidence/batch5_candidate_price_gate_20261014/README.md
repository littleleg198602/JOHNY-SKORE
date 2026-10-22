# Explicit recorded-price gate for candidate walk-forward evaluation

`evaluate_candidate_walk_forward_strict` requires an aware `as_of` cutoff and
uses bounded `prepare_label_observations` plus `audit_snapshot_label`. Every
training label is re-audited at its prediction clock and must be usable strictly
before that clock. Held-out labels must be usable by evaluation cutoff. Pending
prediction feature rows passed to the unchanged fitter contain no held-out
target value or target observation timestamp. Later corrections cannot rewrite
earlier training through a report-cutoff filter.

The candidate formula, feature paths, training selection logic, cost hurdle,
production ranking and activation behavior stay unchanged. Complete synthetic
price captures produce the same numerical metrics as the legacy evaluator.
Legacy calls remain compatible and are explicitly labelled
`legacy_label_timestamp_diagnostic`, with price/OOS verification false.

Strict reports use `candidate_walk_forward_recorded_price_gate_v1` and
`EVALUATED_EXPLORATORY`/`INSUFFICIENT_DATA`. They include per-fold training IDs,
actual label availability and price evidence hashes, held-out exclusion reasons,
bounded archive diagnostics and fixture origins. The query cutoff is `as_of`;
`generated_at` is the actual UTC report generation clock. These checks alone do
not verify publisher authenticity, feature provenance, OOS or activation.

`tests.json` and its exact log bind **46/46** focused Linux tests. They cover
same-clock exclusion, later conflicting versions, label-stripped fitting rows,
future held-out captures, absent benchmark, hash tampering, duplicate IDs,
naive times, bounded/disk archives and UNKNOWN revision metadata. The initial
20-case diagnostic preserves five fixture failures caused by missing SQL-layout
`feature_payload_json`; the fixture projection was corrected without changing
the fitter or price gate.

`lane.json` contains only explicitly synthetic mechanism cases. No genuine
historical prediction database or retained label observation was provided,
no price values were fetched, and **0 HTTP requests** were made. Genuine Yahoo
price captures remain a separate root integration check: unknown revision
provenance cannot be upgraded to a recorded eligible label. The local dependency
hash belongs to this lane's base; the coordinator tests its superseding provider
adapter and final combined code separately.

```python
report = evaluate_candidate_walk_forward_strict(
    snapshots, as_of=evaluation_cutoff,
    label_archive_directory=retained_label_archive_directory,
    maximum_label_observations=10000,
)
```

Actual prospective labels, known revision/action evidence, relevant history and
OOS/Windows acceptance remain open. No model is activated by this report.
