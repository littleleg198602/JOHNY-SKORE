# Additive temporal boundary correction

Independent peer commit `390d713e8ab056759eec0dfa0128039b34a12340`
reproduced two failures in the frozen owner producer from `202303498c`:
an explicitly future review cutoff returned a candidate, and a correctly
rehashed synthetic index with Accepted in2099 was accepted despite its original
capture in2026. Its twelve-method run had ten passing methods, two failures and
zero errors. Exact before producer, owner assertions, peer assertions, full log,
case diagnostics and original capture plan are retained under `before/`.

The correction rejects an explicitly future cutoff against physical entry.
The existing earlier-cutoff and physical-completion checks remain. Default
`as_of=None` still records actual completion as new knowledge.

The original index Accepted display is parsed as an unqualified calendar
datetime. A necessary conservative bound rejects a display later than the
earliest original source/index UTC capture plus a full day. This bounds an
impossible future display without selecting a publisher timezone or verifying
an acceptance instant. Accepted and Filing Date are not required to have equal
calendar days or a universal calendar ordering. Neighboring calendar-day cases
remain possible; the original literal, unknown timezone and unknown publisher
publication instant remain explicit.

Only five new temporal methods were executed here, all plain OK with zero
failures/errors/skips/expected failures/unexpected successes. They cover a
future requested cutoff, the2099 Accepted reproduction, a display already
before review but after original capture, unequal accepted/filing days, and a
neighboring capture-calendar-day control. The original32 methods were not
rerun; their class AST is identical to the committed original. A separate real
physical-clock replay still returns the six source asset components represented
by twelve nested equivalent annotations, with the109-byte size conflict intact.

The independent peer will qualify its unchanged twelve assertions against the
exact committed corrected producer. That result belongs to a separate peer
artifact; this owner handoff does not claim an unexecuted peer pass. Earlier
owner and peer failures remain immutable. No original source body, registry,
model, score, quota, SQL/database or network operation was changed or performed.
