# Exact SEC supply-chain scope

One original NVDA 2026 10-K passage explicitly states that it uses Taiwan
Semiconductor Manufacturing Company Limited (TSMC) and Samsung foundries to
produce semiconductor wafers. The retained complete NVDA body is reused by its
original repository-relative path and SHA256; it was neither refetched nor copied.
The issuer anchor uses its same-context DEI **NVIDIA CORP / 0001045810**, with
the fiscal reporting period ending January 25, 2026. NVDA remains a canonical
ticker hint; no current instrument identity is inferred.

Three pre-reserved SEC GETs obtained complete original TSMC submissions,
2025 R1 cover renderer and filing index bodies (162,358 / 67,428 / 16,361 bytes).
Their actual capture clocks remain UTC October 14. Every request used a
4,000,000-byte cap and refused redirects before a second operation. The fourth
authorized attempt was unused. The index lists the 20-F primary as 10,355,816
bytes; it was not requested because the complete smaller R1 already supports
this bounded source snapshot. USAspending requests, quota reconstruction,
transaction-to-prime inference and production registry changes are zero.

R1's visible cover table independently reports the exact full legal name and
**0001046179** in the same December 31, 2025 date column. Exact field-row and
date-header bytes/hashes support that source snapshot. Its 39 hidden nested
taxonomy-reference tables cannot contribute facts. The snapshot does not prove
a continuous legal-effective interval or legal identity at the NVDA relationship
event day. No supplier-specific purchase amount, denominator, revenue share or
sole-source dependence is established. Two named foundries do not imply 50/50.

The new read-only API is:

```python
review_sec_supply_chain_scope(
    issuer_body, *, issuer_capture, selection,
    counterparty_body=None, counterparty_capture=None,
    counterparty_selection=None, as_of=None,
) -> dict
```

Selection pins issuer DEI/context/fiscal period, an explicit supply role,
counterparty name, object and original claim byte span/hash. Supported roles are
FOUNDRY_SUPPLIER, INPUT_SUPPLIER and CONTRACT_MANUFACTURER. An optional source
purchase share requires its own original passage, exact supplier/declared alias,
object, percentage unit, purchase denominator and fiscal year. No quantity is
selected in the actual NVDA case. The independent SEC_R1_TABLE selection pins
the visible table, one value column, name/CIK/period/year/document-type rows and
the date-column header. Names are not fuzzily expanded from an abbreviated
profile. Same-accession evidence cannot become an independent source.

Results separate source-supported relationship/object, optional literal
quantified exposure and independent legal source snapshot. A malformed optional
legal snapshot stays NAMED_ONLY; it never upgrades the supplier identity. Status
is CANDIDATE_REVIEWED or REJECTED. No database, graph, registry, score, fitter,
prediction, activation or trading state is written.

`as_of=None` uses actual physical review ingestion/knowledge. An earlier source
capture cannot backdate the review. Original NVDA publication clock conflicts
remain uncorrected, and publisher publication time remains unknown. Current
instrument/Q3 identity, historical availability, relevant 687 coverage, user
Windows/OOS and specialist completion remain unverified.

`selection.json` provides the root dispatcher packet and exact original paths,
metadata and hashes. `review.json` contains the actual bounded review and
same-source controls, with no new HTTP during replay. Reproduce after integration:

```text
python -m evidence.batch7_contracts_20261015.replay_review
```

The replay refuses to overwrite its report. 43/43 focused tests passed, including
19 new mechanism cases and existing supply/identity separation regressions.
Generated fixtures and offline redirect checks are not LIVE source evidence.
All original captures and prior recovery/acceptance evidence remain unchanged.
