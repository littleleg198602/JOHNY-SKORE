# Clinical sponsor observed-snapshot contract

The new `clinical_trial_sponsor_snapshot` kind uses `OBSERVED_SNAPSHOT`, separate
from the existing `clinical_trial_sponsor` / `EVENT_DATE` contract. An original
body SHA-256, exact source observation clock, sponsor role, intervention and
relationship digest identify one captured object. Its closed UTC-day cohort
cannot be extended or replaced with the publisher's old posting day.

`build_clinical_trial_snapshot_record(...)` reads original clinical/SEC JSON
bytes and checks their hashes, hosts, capture clocks and review availability.
It requires a same-cohort independent SEC submissions response binding the exact
sponsor name to CIK and ticker, acquired before the object's observation.
Later issuer captures cannot backdate that bridge. `& Co` is not normalized into `and Company`.
Annual DEI period anchors cannot replace this bridge. Selected interventions
must have an explicitly captured EXPERIMENTAL arm relationship; actual active
and placebo comparators do not inherit it. No product ownership is established.

The registry resolver requires the exact observed capture clock for this new
kind. The collector carries original-byte HTTP provenance outside the JSON
payload, dispatches observed attribution separately, and emits explicit false
historical/product-ownership flags even for source-only rows. Unchanged source
content remains an idempotent heartbeat. Coverage distinguishes archived
observed snapshots from current snapshots and historical event records.

## Actual evidence and gaps

The existing 13 October SURPASS-2 and SUMMIT full records and SEC submissions
bytes are reused, without changes or new requests. SEC's body names CIK
`0000059478` / `LLY` as `ELI LILLY & Co`; ClinicalTrials names its sponsor
`Eli Lilly and Company`. The issuer was also captured after both objects'
original observations. Both actual candidates remain WITHHOLD. There are
**zero proposed or adopted production records**. The old two historical event
proposals remain withheld and immutable.

No current 14 October clinical page was obtained. The old capture driver's
temporary quota ledger was not recovered; an empty current table does not
restore historical reservations. Acquisition remains `WAIT_QUOTA_HISTORY`.
This lane makes zero new ClinicalTrials or other source requests and creates no
new LIVE quota store. Production's 10 healthcare records stay unchanged.

Positive unit fixtures use explicitly synthetic exact-name issuer JSON and
provide no LIVE bridge. Actual-byte negative tests preserve the name mismatch,
comparator roles and original hashes. Historical ownership, full healthcare
coverage, OOS and user Windows acceptance remain unproved.

## Runtime API and offline replay

- `ClinicalTrialsClient.study(nct_id)` reads one full exact object and exposes
  `last_page_provenance`; its caller must reserve the existing durable source
  quota. It performs no historical version lookup or automatic quota reset.
- `build_clinical_trial_snapshot_record(...)` produces a record only after the
  byte/content/time gates pass; registry adoption belongs to its sole owner.
- `HealthcareIdentityRegistry.resolve(... kind="clinical_trial_sponsor_snapshot",
  observed_at=..., event_date=observed_at.date(), as_of=...)` retains the exact
  observation and independent knowledge clock. It cannot resolve the old
  publisher event day as an observed issuer day.

Run from the repository root:

```bash
PYTHONPATH=. python evidence/batch5_healthcare_20261014/review_sources.py
python -m unittest tests.test_batch5_healthcare_snapshot
```

The review reuses its saved actual UTC review clock on subsequent replay.
`source_review.json`, `identity_proposal.json`, `focused_tests.log` and
`validation.json` record the exact results and remaining gaps. Withheld or
incomplete bodies fail before attribution; sanitizing or encoding them does
not create an acceptable primary source.
