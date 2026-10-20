# Exact source-role/transaction temporal review

The new pure reviewer rebuilds the selected biography and account facts from
three retained, whole, hash-pinned originals: the Microsoft 2025 DEF 14A,
its original SEC index, and the complete 2026 Nadella/MSFT Form4 packet.
It accepts no caller-supplied role report or verified-identity flag. It performs
0 HTTP/API requests, 0 DB/quota/lease writes and 0 registry/score/model changes.
No old source, failure, test, evidence artifact or withheld body is modified.

The actual case selects the source clause **Chairman and Chief Executive
Officer (2021-present)** and original nonDerivativeTransaction ordinal 1.
The complete packet contains an exact 8,214-byte ownership XML document,
SHA256 `c71fd4237b551e70fd8054cfd54b9d101e63d689d0a26b10bb8bd123a0a4c810`.
The reviewer records original XML/packet byte spans for the selected row,
its whole original row hash and the exact transactionDate element quote/hash.
The declared transaction day is **2026-08-31**, DAY precision.

The proxy's source-relative `present` is anchored only to its **2025-10-21**
filing day. The requested actual transaction is later, so the temporal fit is
**OUTSIDE_SOURCE_ASSERTION_SCOPE**, with
`source_assertion_temporally_compatible=false`. The comparison itself is valid:
`source_temporal_assessment_supported=true` means the raw sources support this
negative comparison, not that the person held the role at the transaction.
Overall status stays **PARTIAL** and role-at-transaction status **UNVERIFIED**.

YEAR starts and stated ends are converted only to calendar precision
envelopes. January 1/December 31 are bounds of the stated year, never invented
appointment or cessation facts. A day within either endpoint year remains
uncertain. A day inside a coarse literal range may be compatible with the
assertion but still proves neither an exact legal interval nor continuity.
`Director Since:2014` has YEAR start precision; `present` and `since` do not
extend beyond the source anchor or establish a legal cessation date there.
Combined titles stay combined. The proxy financial report period is not used
as a biography validity interval.

The Form4 account remains raw **Nadella Satya**, CIK 0001513142, versus named
biography **Satya Nadella**. The declared account relationship/CEO title is
as-filed scope, not an independent natural-person alias or role-at-day proof.
Even a compatible hypothetical date retains missing independent exact identity
and role-interval gaps. No instrument/CUSIP/class identity, trade execution,
historical machine availability, OOS or full-specialist acceptance is claimed.
The original **110-byte** proxy index/served-body conflict and **UNKNOWN**
publication timezones remain; source acceptance displays are not reconstructed
as UTC instants.

One selected assertion is assessed; the seven-role inventory is not copied
again. Direct original transaction rows are bounded to 128 and must reside in
their correct XML table. Missing/invalid dates, before-period/after-filing
conflicts, absent rows, unreviewed date footnotes and a requested day that does
not match the original row cannot supply temporal support. A report period,
holding row, comment, or request hint never substitutes for a transaction date.

The new interpretation's physical start and completion are distinct. The
fresh source-role review must start and finish within the parent review's
physical interval. `known_at` and `review_available_at` are physical completion.
An explicit past cutoff is nonaccepting; if an explicit cutoff is crossed
during interpretation, all interpreted role/transaction fact objects are
withheld. Original capture clocks remain original; no newly read historical
source backdates machine knowledge.

API:

```python
review_sec_person_role_temporal(
    primary_capture=primary, index_capture=index,
    account_packet_capture=account_packet, selection=selection,
    temporal_request={
        "role_title_literal": "Chairman and Chief Executive Officer",
        "transaction_kind": "nonDerivativeTransaction",
        "transaction_ordinal": 1,
        "requested_transaction_day": "2026-08-31",
    },
    knowledge_at=None,
)
```

Module: `market_checker_app.services.sec_person_role_temporal_review`.
`PersonRoleTemporalCapture` uses the existing original metadata/body constructor.
`actual_review_plan.json` has kind `SEC_PERSON_ROLE_TEMPORAL`, capture keys
`primary/index/account_packet`, and request keys `selection/temporal_request`.
All references are existing safe whole bodies with POSIX repository paths,
sizes and SHA256; no new raw source body is created. The coordinator owns
dispatcher/UI/CLI and physical immutable archival.

`person_role_temporal_review_v1.json` is the actual physical interpretation;
`build_review.py` can reproduce it offline using its saved parent and child
clocks. `focused_01.json` and its complete original log qualify the exact
current source/test/producer/dependency hashes: **37 discovered=executed,
plain OK, 10.914s**, all failure/error/skip/expected/unexpected counts zero.
Controls cover actual original hashes/quotes and the negative temporal case,
start/end-year boundary uncertainty, coarse compatibility without acceptance,
the proxy anchor and next day, distinct same-day original rows, missing/conflict
and footnoted dates, bounded selectors/rows, caller-report rejection, whole-body
withholding, exact physical completion/cutoffs and pure offline operation.
Hypothetical packets are explicitly synthetic fixtures, not new live evidence.

No full suite or Windows acceptance is asserted. Independent exact person/account
identity, dated legal role interval/continuity, instrument identity and relevant
historical evaluation remain separate work. Full acceptance remains 0/21.
