# Original supplier revenue denominator review

The retained complete Skyworks Solutions, Inc. FY2025 10-K reports these
**consolidated net-revenue totals**, in USD millions, in its Consolidated
Statements of Operations. These are total SWKS revenue, not Apple purchases.

| Fiscal year | Exact duration | Displayed USD millions | Total USD from source scale only | Fact/context |
| --- | --- | ---: | ---: | --- |
| 2025 | 2024-09-28–2025-10-03 | 4,086.9 | 4,086,900,000 | f-61 / c-1 |
| 2024 | 2023-09-30–2024-09-27 | 4,178.0 | 4,178,000,000 | f-62 / c-7 |
| 2023 | 2022-10-01–2023-09-29 | 4,772.4 | 4,772,400,000 | f-63 / c-8 |

The exact standard concept is
`us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`, the unit is
`iso4217:USD`, scale6, decimals−5, no sign attribute, with the explicit
`ixt:num-dot-decimal` transformation. Each denominator context is the issuer's
undimensioned annual duration. Statement titles, units, complete table,
header/date/revenue rows and exact cells remain byte- and SHA-pinned.

The original policy passage says revenue follows ASC606, net of estimated
reserves, with returns, price protection, price adjustments and stock rotation
qualifiers preserved in full. The FY2024/2023 totals are comparative amounts
**as reported in this FY2025 filing**. Prior as-filed versions, restatement
history and unchanged definitions across earlier filings remain unverified.
If the selected policy mentions restatement/reclassification or a changed
revenue definition, the reviewer preserves the disclosed totals and qualifier
but withholds automatic denominator-basis association pending review.

The existing concentration reviewer is reused for its original source, issuer,
scope and period proof. Its Apple67/69/66 % statement has exactly the same
three periods and the reporting Company's net-revenue denominator. The new
consumer does not extract a new percentage or multiply any percentage by a
total. Generic `swks:CompanyMember` remains generic. The narrative includes
distributors, contract manufacturers and direct sales; neither a direct Apple
purchase share, physical quantity denominator, customer revenue amount nor
legal-counterparty continuity follows from these supplier totals.

There were **0 new HTTP/source/collector requests, 0 DB/quota changes, 0 copied
source bodies and 0 registry adoptions**. Complete original bodies remain at
their batch9/batch3 repository paths. The SWKS served original is1708710bytes
while the original index declares1708601bytes. This109-byte conflict stays
visible; exact historical as-filed bytes and publication instant are unknown.
The prior Apple API/index acceptance-clock conflict is not resolved here.
Old capture clocks remain unchanged, separate from the actual physical review
and knowledge clock in `actual_review.json`. No withheld body was accessed,
restored or substituted.

`actual_review_plan.json` uses the standard captured-review plan schema and
new kind `SEC_SUPPLIER_REVENUE_DENOMINATOR`; root owns dispatcher/UI integration.
It passes issuer/index and the existing optional Apple legal snapshot as
whole-byte pins, with no old `as_of` override. The pure API is
`review_sec_supplier_revenue_denominator(request, captures, *, as_of=None)`.
It performs no transport or persistence; explicit past cutoffs reject a new
physical review. All local evidence reports are write-once.

Offline commands from the repository root:

```
python -B -m evidence.batch10_contracts_20261018.replay_retained
python -B -m evidence.batch10_contracts_20261018.build_plan
python -B -m evidence.batch10_contracts_20261018.qualify
```

Calling `replay_retained.run()` returns a new physical review without replacing
the immutable recorded proof. The saved focused qualification is53/53, including
26 new methods and27 concentration dependency methods, with all outcome flags0.
Synthetic fixtures do not count as live or historical acceptance. Relevant687
coverage, full award/UEI/durable source history, current Q3/instrument identity,
user Windows acceptance and historical/OOS evidence remain missing. Canonical687,
score/model/fitter/budgets/trading and full specialist acceptance0/21 are unchanged.
