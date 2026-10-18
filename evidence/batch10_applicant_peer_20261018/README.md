# Independent applicant table-scope peer

The peer confirmed one material scope defect in the original candidate:
an exact applicant name and Ireland row in an explicitly unrelated license
counterparty table inherited the earlier subsidiary roster's heading, date and
columns. With `selected_rows=[]`, that foreign row was reported as
`LISTED_SUBSIDIARY_OF_REPORTING_ISSUER_GROUP` as of 2017-12-31 and
`exact_applicant_name_match_supported` became true. Current-control, direct-parent,
product-owner and attribution flags remained false. The finding concerns a false
dated roster-point fact; it is not an observed ownership promotion.

The source/test/plan/actual-review/log candidate snapshot was frozen before
diagnostics. `foreign_table_probe_before.json` and the clearly synthetic HTML
fixture preserve the exact initial trigger. Seven new independent peer methods
then ran against that exact frozen producer:

| Producer | Peer result | Interpretation |
| --- | --- | --- |
| `bf9f1afb16c29c0c0272229e5567ed5448e19db0d4fe4684908dd419af3b59a0` | 7 executed, 4 failures, 0 errors | Foreign repeated-header/inherited-header/selected-row/short-name contexts expose the same table-scope gap |
| `e4ca4ffd9da51add2bef4b5c1020d62c581d51c9e3a8bfd3e6105e5598a35b8d` | 7 executed, 7 passed, all other counts 0 | Same peer test bytes pass after the healthcare owner's fix |

The owner fixed its own producer. The peer never edited the candidate. The fix
requires a complete roster-only body/table scope and rejects unrelated visible
headings or table captions instead of carrying a global roster declaration into
another source scope. The owner's actual retained replay still reports 468 entity
rows across 11 continuation tables, no exact Unlimited Company match and `PARTIAL`.

Three independent controls passed both versions: a borrowed valid CIK cannot
reroute the original filing/exhibit; the actual unclosed Purple Book object plus
a forged caller-selected name stays withheld; and a legitimate exact 2017 roster
point with a 2019 product approval never proves an ownership interval. Synthetic
fixtures and clocks are contract tests, not primary acceptance evidence.

`before.json`/`before.log` and `after.json`/`after.log` preserve complete counts,
original console output and source/test hashes. The test source is unchanged
between the two runs. `candidate_snapshot.json` and
`candidate_after_snapshot.json` pin exact source versions and actual plans.
The snapshot copies are evidence, not alternate production modules.

The focused runner uses the healthcare worktree through `PYTHONPATH` and loads
only the new peer class. After integration, the peer test file imports the
integrated producer normally. No owner suite or full baseline suite was repeated
by this peer. No HTTP, database, quota, registry, shared-file or GitHub operation
was performed. Four whole-withheld bodies were neither read nor restored.
