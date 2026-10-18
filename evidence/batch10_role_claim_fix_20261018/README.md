# Role-claim subject, index visibility and interpretation cutoff correction

The independent contracts peer found three reproducible failures in the first
role reviewer, SHA256 `eb4888df2ffbed6354334dab6b97e786dcc44ba87983e2e9b94180ee9d180b84`:

1. An unselected Amy Hood biography row inside the adjacent career table let a
   later selected CFO row donate its roles to the preceding Satya Nadella bio.
2. A hidden original index Filing Date still anchored source-relative `present`.
3. Interpretation completed two seconds after an explicit knowledge cutoff
   still returned source-supported role claims at that earlier cutoff.

These were actual synthetic reproductions, not source collection or acceptance.
The exact original owner source, tests, producer, v1 review, 44-case qualification
and complete three-failure independent peer diagnostic are preserved in
`before/`, with hashes and freeze clock in `before_freeze.json`. Original
`evidence/batch10_sec_20261018/` bytes remain unchanged. Its 44-case result
qualifies only that first source version and does not qualify the correction.

Version 2 now checks the complete career-table scope as well as the selected
cell. The selected role cell must contain that table's first visible content;
earlier unselected text, even an identical career clause, cannot donate a later
cell. Additional biography markers inside the career table make the subject
scope ambiguous. Blank layout rows/cells remain supported, including the real
Microsoft row2/cell5 layout. The exact whole-table context hash, byte spans and
first visible position are returned as compact provenance.

A separate bounded index parser requires the original Filing Date and Accepted
labels and values to be visible in their own div scopes. Hidden ancestors,
hidden values/labels, hidden children and unrelated equal visible text cannot
supply the source date. The original date and publisher clock qualifiers stay
unchanged; no UTC timezone is inferred.

An explicit requested cutoff is checked again at interpretation completion.
If completion is later, all interpreted role/source/account-comparison fact
objects are withheld, source support is false, the requested cutoff remains
recorded, and physical completion is the report's availability. Default `None`
still means a current physical interpretation available when it actually ends.

The additive actual v2 replay remains **PARTIAL** and preserves the same seven
named role claims plus one generic career-position assertion, all with literal
YEAR starts/ends. `present` remains anchored only to **2025-10-21**. The original
110-byte publisher index/served-body conflict, UNKNOWN publication timezones,
raw name-order discrepancy and person/account-CIK, legal-role continuity,
alias and instrument/CUSIP gaps remain. The actual Form4 is account scope,
not a second independent named-person identity source.

The new producer and `person_role_claim_review_v2.json` are versioned here;
`actual_review_plan.json` retains the same safe whole-original references and
request. Runtime API is unchanged: `PersonRoleClaimCapture` and
`review_sec_person_role_claims(primary_capture=..., index_capture=...,
account_packet_capture=..., selection=..., knowledge_at=None)`. The root
coordinator owns CLI/UI/runtime archival and publication.

`focused_fix_01.json` and the full original log qualify the current exact
source/test/producer hashes: **55 discovered=executed, plain OK, 7.922s**, all
failure/error/skip/expected/unexpected counts zero. These comprise 52 owner
methods and the three unchanged frozen peer methods. All original 44 owner
test method bodies are verified AST-identical; eight additional owner methods
cover donor rows, repeated prefix text, a trailing named subject, hidden index
contexts, unrelated visible dates, completion cutoff behavior, default current
completion and the actual table scope. Independent peer assertions were not
changed. Network is blocked in qualification.

The independent contracts peer separately ran the same three methods after
the correction: **3/3, all counts zero**, against the same frozen producer
SHA256 `4e7146c6776b2cce5d9a3393a1493c016753f13d586377257917405aa74bacb3`.
Its additive after proof is commit `43d71ca81505a32132e89d279dd7baf7ea27fe76`,
under `evidence/batch10_role_claim_peer_20261018/`. The before three-failure
commit is `cdcae54b3b1b599d19f76e54088482e6ce9d0b55`. The peer also independently
replayed the real retained source facts and checked the seven named roles,
YEAR precision, 2025-only present, and false identity/continuity/score flags.

No source request, DB/quota/lease write, registry adoption, model/score/budget
change, trading operation, withheld-body recovery or GitHub mutation occurs.
No full suite, user-Windows, historical availability, OOS or specialist DONE
acceptance is claimed. The adapter remains limited to the explicitly selected
DEF 14A adjacent-biography/year-range layout and explicit markup visibility;
it does not infer general CSS rendering, exact role dates or legal continuity.
