# Original PSTG/P action-scope review

The current integration input is `final_captured_review_plan.json`, with kind
`SEC_SYMBOL_ACTION_SCOPE`. Call
`review_symbol_action_scope(plan["request"], captures)` where each selected
capture is `(metadata, original_bytes_or_None)`. Omit `as_of` for a newly recorded
review. An explicit earlier cutoff cannot backdate a new interpretation.
The review performs no HTTP, discovery, registry write or scoring operation.

Exactly six one-off primary SEC transports were reserved before HTTP, under the
prewritten `request_plan.json`: each had a 25-second timeout, 4,000,000-byte cap,
and redirects disabled. All six returned HTTP200 within those bounds. The
3,866,467 original response bytes and their original physical UTC15 clocks are
retained without transformation. No collector API/quota database was used and
no old source-request history was reconstructed. These are research captures,
not application LIVE acceptance. No known withheld body was read or restored.

| New original response | Actual scope |
| --- | --- |
| SEC submissions for CIK1474432 | Exact accession/form/primary-document rows and declared acceptance fields, observed 2026-10-15. It is not a historical machine-availability log. |
| March25 10-K, accession0001474432-26-000027 | Same-context CIK1474432, Everpure, PSTG and explicit Class A/par. Its February1 context anchor is not a legal-name effective day or continuous identity proof. |
| May1 DEF14A, accession0001474432-26-000041 | An independent same-context issuer-name snapshot. No ticker/action day is supplied by this name alone. |
| May27 8-K index, accession0001474432-26-000046 | Exact EX99.1 document row. The displayed acceptance time has no inferred timezone. |
| May27 EX99.1, same accession | Reports that the corporate brand transitioned and the ticker was updated to NYSE:P. The whole selected original paragraph has neither an exact effective day nor an explicit PSTG-to-P direction/local legal-name definition. The index declares319527 bytes but319645 were served; all118 differing bytes remain in the original body. |
| April29 Schedule13G, accession0002100121-26-000241 | Independent new issuer CIK1474432/CUSIP74624M102, generic Common Stock, eventMarch31. It does not supply Class A/par or a same-day symbol binding. The submissions renderer path differs from the raw XML path, and the missing original index remains explicit. |

`final_actual_review.json` is PARTIAL, with `effective_on=null`, no independent
completed exact-day symbol disclosure, no exact old/new instrument bridge, and
no dated class/CUSIP action-continuity proof. Original capture knowledge ends at
2026-10-15T13:40:58.490517Z; the new physical review is recorded on UTC16. It does
not move source clocks, infer an April day from a month or assign the May27
filing day to the action. Registry adoptions/currentQ3 coverage remain0. No alias,
Q2 extension, OOS, Windows or DONE claim is made.

Requests explicitly select scope, filing/source-proof descriptors, whole
original paragraph selections, same-context cover/name proposals and structured
instrument proposals. Action actor/name/CIK evidence is separately selected via
`issuer_source_key`, `issuer_context_ref`, `issuer_name_text` and
`issuer_reference_text`; a short reference needs its explicit local definition.
`NAME_ONLY` covers cannot supply instrument/symbol identity. Missing or generic
class/par, unknown dates, competing dates and issuer/control scope remain gaps.

The retained pre-clause producer8a24a55f incorrectly combined an issuer's dated
logo sentence with a different company's symbol change. The exact synthetic
case9114cc25 reproduced false READY. `peer_before_fix.json` and its whole original
producer/tests remain immutable. The corrected producer binds actor, owned
ticker object, verb, direction and date to one explicit clause; a continuity
sentence must immediately describe that same instrument. The identical case in
`peer_after_fix.json` is PARTIAL, effective day unknown, all three actions
unsupported. Independent healthcare peer replay confirmed both that rejection
and the unchanged positive synthetic control. Synthetic cases are not LIVE
acceptance. All prior logs/reports/producers, including the initial27-case fixture
failure and earlier30/34-case passes, remain historical diagnostics.

The corrected module passed38 focused methods with zero failures/errors/skips,
then the exact updated actual-plan case passed separately. No baseline/full
suite was rerun. The coordinator owns runtime dispatch, UI, shared status,
integration checks, GitHub publication and any later adoption decision.
