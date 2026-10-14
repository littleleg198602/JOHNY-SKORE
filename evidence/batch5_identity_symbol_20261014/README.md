The same-CIK symbol lane reviews `PSTG -> P` through
`instrument_transition_review(..., corporate_action_bridges=[claim])`, with
`bridge_kind="SAME_CIK_SYMBOL_RENAME"`. The independent XOM new-CIK
contract keeps its existing output. This reviewer never adds a canonical
symbol, creates an alias, approves an identity or extends a Q2 interval.

All inputs are retained primary bytes. This output makes zero HTTP requests.
The selected archive contains four filings and their exact SEC submissions
metadata. Publication times remain the archived acceptanceDateTime values;
their October 13 observation clocks are distinct from report/event dates.
Replaying today does not reconstruct earlier knowledge or source history.

The February 23, 2026 cover binds `PSTG`, CIK `0001474432`, Everpure, Inc.
and Class A Common Stock with $0.0001 par value. Independent May 3 and
August 2 covers bind `P` under the same exact CIK/class/name. These dates
form an observed bracket and do not establish a corporate-action effective
day. The May 3 filing states only "In April 2026" and contains no directed
`PSTG -> P` passage, old/new class/CUSIP bridge or exact effective day.

The retained Schedule 13 identifies CIK `0001474432`, CUSIP `74624M102`,
Everpure Inc and unclassified "Common Stock" at March 31, 2026. That class
cannot establish the independent Class A binding. No independent
post-rename Schedule 13 or dated symbol/CUSIP action is present. The Q3 list
and current catalog supply proposals, not missing historical evidence.
There is no PSTG production version among the preserved 150 Q2 records;
the old tuple explicitly remains an unverified candidate. Current Q3
coverage stays zero and canonical input stays 687.

The actual report is `PRIMARY_BRIDGE_PARTIAL` with nine explicit blockers.
Synthetic complete proof can become ready for an explicit review but still
cannot approve an alias/identity. Regressions preserve competing legal
names, par terms, symbol anchors, exact document URLs and later knowledge
clocks. Duplicate names, disconnected/reversed symbols and a guessed first
day of a month do not satisfy the bridge.

Replay without network:
`python -m evidence.batch5_identity_symbol_20261014.replay_symbol`.
The log of an initial 28-case run preserves one fixture failure caused by
the terminal-period symbol boundary; the boundary was corrected. A
subsequent 128-case combined run passed. Final changes to document URL
independence and preserved-registry clocks have 19 passing changed-lane
checks. No full-suite, Windows, historical OOS or specialist DONE acceptance
is claimed.

`handoff.json` names the first output's immutable artifact/code version and
the current replacement module hash. The original XOM handoff, captures,
proposal and report remain byte-for-byte unchanged and replay unchanged.
