# Original SEC index accession and observation-day guards

Peer review reproduced two source-validation defects in person-kind commit
`2ca2d7e77ccd411c633763331acf8aef03ed1d9a`. A changed index basename could
claim accession245151 inside the original245150 directory. A synthetically
rehashed index could label its filing2099 despite an original2026 capture.
Both incorrectly supported the narrow named-person fact. Person/CIK,
instrument, approved alias and historical identity flags stayed false;
the actual dated source replay was unaffected.

`original/` preserves the exact previous service/test bytes, pinned to that
commit in `original_source_pins.json`. `reproduce_prior.py` loads that
archived service, the unchanged original captures and bounded synthetic
index mutations. `prior_failed_invariants.json` physically records both
incorrect acceptances on October15,2026, with zero HTTP. Its command refuses
to replace the recorded evidence.

The service now requires the exact accession-derived index basename. The
filing day cannot follow either original physical observation day; a later
`known_at` or requested knowledge cutoff cannot extend that upper bound.
Original capture availability still gates the whole review. Two meaningful
regressions use mutations of the actual pinned index, including the case
where all input knowledge is later than the synthetic future filing.

The first fix qualification (`focused_01`) executed59 cases with one error:
an older synthetic same-accession fixture changed its directory but left
the old basename. The fixture now changes both together and preserves its
strict same-disclosure withholding assertion. `focused_02` qualifies the
final59 cases, all successful with zero failures/errors/skips/expected or
unexpected outcomes. Both exact-source reports/logs remain present.

The prior57-case report, original handoff, complete SEC bodies, literal
quotes, publisher110-byte conflict and original review/plan JSON remain
byte-identical. The fix has its own source/test hashes and qualification;
it does not relabel the earlier result. No new HTTP, registry change,
score, issuer attribution, identity promotion or full acceptance occurred.
The separate in-progress IR reviewer receives the same index invariants
before its own qualification and is not part of this fix commit.
