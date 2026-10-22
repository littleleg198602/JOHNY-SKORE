# Exact aggregate SEC commitment review

The retained NVIDIA FY2026 10-K, Note12, reports **$95.2 billion** of
manufacturing, supply and capacity commitments as of **2026-01-25**. The
selected original paragraph says substantially all will be paid through
FY2027. It also says that, in certain instances, agreements are cancellable,
reschedulable or adjustable before firm orders and changes may incur costs.

The whole paragraph is pinned at original bytes1488427–1489351. Its literal
value is iXBRL `f-894`, `us-gaap:OtherCommitment`, context `c-177`, USD scale9,
decimals−8. The explicit dimension identifies a commitment category; it
does not identify a supplier. The consolidated-notes headings and exact
same-context issuer DEI name/CIK/fiscal period are independently pinned.

This is a consolidated category balance. It is not actual spend, revenue,
production volume, a total across every commitment category, or an amount
attributable to a particular legal entity or supplier. TSMC/Samsung names
in another paragraph cannot be joined to the anonymous category amount.
Supplier share, supplier identity at the obligation date, group membership
and continuous legal/instrument identity remain unverified.

Both complete original bodies remain at their existing repository paths;
there are no new source requests, collectors, quota stores or copied bodies.
The index declares1967816 primary bytes while the retained served original
has1967927 bytes. The111-byte conflict remains visible; exact as-filed bytes
and publication instant are unverified. The prior NVDA API/index acceptance
clock conflict is not resolved by this review. The old capture clocks remain
2026-10-12; the new review's actual physical knowledge is recorded separately
in `review.json`.

`selection.json` pins original files, metadata/pointers and four complete
visible blocks. `review_plan.json` is a standard captured-review plan with
`issuer` and `index` captures for the root-owned `SEC_PURCHASE_OBLIGATION_SCOPE`
dispatcher integration. It contains no old `as_of` override. The new service
is pure, does not persist, and defaults to physical ingestion now; an explicit
past cutoff rejects the new review.

Run the original replay from the repository root with the prepared Python:

```
python -B -m evidence.batch8_contracts_20261015.replay_review
```

The recorded report is write-once. Calling the Python `run()` function returns
a new review at its own physical clock without replacing the recorded proof.
`qualify.py` recorded95/95 offline focused tests, including27 new cases and
the actual retained replay. Synthetic mechanism inputs do not count as LIVE,
historical/OOS or Windows acceptance. There is no registry adoption, scoring,
model activation or trading change.
