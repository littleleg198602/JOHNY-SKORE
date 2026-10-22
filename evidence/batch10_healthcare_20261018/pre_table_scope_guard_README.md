# Exact FDA applicant versus dated subsidiary roster

This lane adds the pure `FDA_LEGAL_APPLICANT_BRIDGE` review. It reads five explicitly pinned original captures and never collects data, opens a database, changes a registry or attributes a product to an issuer.

```python
from market_checker_app.collectors.fda_legal_applicant_bridge_review import review_fda_legal_applicant_bridge
result = review_fda_legal_applicant_bridge(request, captures, reviewed_at=aware_source_read_cutoff)
```

`captures` maps the five request keys to `(metadata, original_bytes_or_none)`. `retained_review_plan.json` contains the actual bounded, immutable-dispatch-compatible request, metadata and body paths/hashes. `reviewed_at` is the cutoff before source reads. New review/ingestion/knowledge clocks are the physical time of this review; a caller cutoff never backdates them.

The original Pfizer 2017 10-K links its already captured Exhibit 21 under the literal label `21`. A separate filename ending in `dex21.htm` has label `2.1` and is excluded. The original index request returned HTTP 403. No request was retried and no new source request was made. The only explicitly linked Exhibit 21 had already been captured, so the conditional permission for one previously unattempted link did not apply.

The complete Exhibit 21 contains 468 entity rows in 11 tables at December 31, 2017. Table 5, row 6 states `Pfizer Ireland Pharmaceuticals` and `Ireland`. It does not state the current Purple Book applicant `Pfizer Ireland Pharmaceuticals Unlimited Company`. The roster explicitly omits some subsidiaries; absence is not proof of non-ownership. An unrelated repeated `Pfizer Limited` row is recorded without erasing the unique selected row. A duplicated target entity remains ambiguous.

The retained Purple Book object still has its two original products `001`/420 MG and `002`/150 MG, and its incomplete outer container remains `PARTIAL`. The Drugs@FDA company field `PFIZER INC` is a separate source role. Neither a short-name match nor this company field establishes the legal applicant's parent or product ownership. The 2017 point, 2019 approval, October 2026 capture, and current ingestion clocks remain distinct. There is no inferred ownership start, continuous/current control, direct parent, approved alias or product assignment.

`source_review.json` is the physically current offline result. The earlier diagnostic and pre-resumption result are retained separately. `focused_tests.log` is the complete 25-test successful focused run; positive fixture contracts are synthetic and are not live evidence. `handoff.json` pins the current producer, tests, original captures, actual plan/result, limits and unchanged protected inputs. No full suite or previous CI run was repeated.

Useful next evidence is an independently dated disclosure of the exact applicant legal entity and its issuer relationship at the relevant object time, plus any actual direct-parent or product assignment documents. This lane makes no registry proposal or acceptance promotion: healthcare remains 10 canonical identities and full acceptance remains 0/21.
