# Literal named-person role claims, offline retained-source review

This component reads whole, hash-pinned originals already retained in the
repository. It performs **0 HTTP requests**, 0 source/API collection, 0 database
or quota writes, and 0 registry, model, score or trading changes. It does not
recover or replace any withheld body. All prior evidence remains unchanged.

The independent Microsoft 2025 DEF 14A, accession
`0001193125-25-245150`, names **Satya Nadella** in table 163 and gives an adjacent
issuer-scoped experience clause in table 164/row 2/cell 5. The new reviewer binds
both complete normalized clauses to exact original context byte spans and their
whole-body hash. Visible neighboring sibling tables, no intervening content,
the same-context DEI issuer/name/form/financial period, and the original dated
SEC index must agree. The original proxy SGML packet/header is not retained;
the inline DEI header and original filing index are the available scope.

The actual report adds seven stated role claims: Director Since 2014;
Chairman and Chief Executive Officer 2021-present; Chief Executive Officer and
Director 2014-2021; Executive Vice President, Cloud and Enterprise 2013-2014;
President, Server and Tools 2011-2013; Senior Vice President, Online Services
Division 2009-2011; Senior Vice President, Search, Portal, and Advertising 2008-2009.
One additional 1992-2008 clause is explicitly a **generic career-position
assertion**, and Microsoft Corporation 1992-present is company experience.
These claims all come from one biography disclosure.

Every stated start/end retains **YEAR** precision. No January 1, exact
appointment or cessation date is invented. Combined titles stay combined.
`present` is source-relative to the index filing day, **2025-10-21**; it does
not extend legal employment or role continuity through the October 2026 capture
or the later Form4. The DEI financial report period 2024-07-01 to 2025-06-30 is
separate from biography validity. A new physical review is ingested now;
neither a 2014 assertion nor a 2025 filing backdates its machine knowledge.

The separate retained 2026 Form4 packet, accession `0000789019-26-000157`, is
used only for an exact source-account comparison. Its header and XML validate
raw account name **Nadella Satya**, reporting-account CIK 0001513142,
issuer MICROSOFT CORP/0000789019 and officer title Chief Executive Officer.
Literal title-component agreement is shown only with the two source scopes.
It establishes no approved name-order alias, natural-person/account-CIK bridge,
legal role interval, continuity, or instrument/CUSIP/class validity. Two
distinct accessions mean two underlying disclosures, including only one named
biography disclosure; document blocks or eight career assertions do not create
additional independent natural-person evidence. A same-accession control is
counted as one disclosure.

The original proxy index declares 2,430,412 bytes; the safe served original is
2,430,522 bytes. The **110-byte discrepancy stays explicit**, and exact
historical as-filed version remains unverified. Proxy acceptance display
`2025-10-21 16:16:13` and account header display `20260901180827` retain
**UNKNOWN publication timezone**, with no reconstructed UTC instant. Original
HTTP clocks and capture dates are preserved in the actual replay artifact.

Runtime API:

```python
from market_checker_app.collectors.sec_person_role_claim_review import (
    PersonRoleClaimCapture, review_sec_person_role_claims,
)
report = review_sec_person_role_claims(
    primary_capture=primary, index_capture=index,
    account_packet_capture=account_packet, selection=selection,
    knowledge_at=None,  # actual physical interpretation; explicit past is nonaccepting
)
```

`PersonRoleClaimCapture` uses the existing standard original metadata/body
constructor. `actual_review_plan.json` has kind `SEC_PERSON_ROLE_CLAIM`, capture
keys `primary/index/account_packet`, and request `{"selection": ...}`. The
selection includes issuer CIK/name, person name, exact neighboring context
locators and complete quotes, plus an exact original Form4 account descriptor.
The plan references only safe retained whole bodies, with POSIX repository
paths, size and SHA256. The coordinator owns runtime/CLI/UI dispatch and
physical immutable archival; this component itself performs no file I/O.

Focused qualification: `focused_01.json` and the complete original
`focused_01.log`: **44 discovered=executed, plain OK, 11.650s**, all
failure/error/skip/expected/unexpected counts 0. The exact service, test,
producer, runner and dependency hashes are pinned. Tests cover actual original
replay and literal facts, structural person association, hidden/cross-issuer
contexts, DEI/date/precision conflicts, raw account descriptor drift, same
accession counts, whole-body withholding, hash/size bounds, physical knowledge,
and absence of network/DB/file I/O in the pure runtime reviewer. Synthetic
markup is labeled fixture control, never new live source acceptance. No full
suite or Windows/OOS/user execution acceptance is claimed.

Overall result remains **PARTIAL**, 0 new verified adoptions and no full
specialist acceptance. Further work needs independent dated legal
natural-person/account identity, approved aliases, actual role start/end and
continuity evidence, dated instrument identity, relevant historical coverage
and evaluation. The current adapter intentionally supports only this bounded
DEF 14A adjacent-biography/year-range layout.
