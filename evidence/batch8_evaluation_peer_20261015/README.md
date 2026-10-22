The root captured-market integration was reviewed through its source, isolated
AST expressions and initialization/registration paths. No normal pipeline,
model, fitter, source collector, actual agent execution or forecast was run.
Socket connections and native SQLite connections were prohibited throughout
the nine checks. Prior provider qualification and the sixteen capture readiness
cases were not repeated.

Two concrete defects were reported to the coordinator and fixed by that owner:

* Fixture-only provider/asset/benchmark provenance remained nested, while the
  history boundary reads the snapshot's top-level `evidence_origin`. The fixed
  pipeline propagates `DETERMINISTIC_TEST_FIXTURE` and `fixture_only=true` at
  that boundary without altering the nested original source pins.
* The UI could still enable and execute its MT5 watchlist read in captured
  mode. The fixed button and execution guard both refuse that mode. Native MT5
  construction itself is inert; its read methods perform terminal initialization.

The actual old pipeline bytes were frozen before the fix. The old UI bytes were
reconstructed by reversing only the two known MT5 guard changes; their whole-file
SHA256 equals the previously observed source pin exactly. `before/` retains those
versions and hashes. `after/` freezes the owner-fixed versions and source hashes.
None of these files is imported as an application or launched.

Before: nine executed, five failures and zero errors. The five failures are the
three independent fixture-marker origins and two MT5 guards. After: nine
executed, zero failures/errors/skips. These are source-boundary regressions,
not live forecasts, full pipeline qualification, PIT/OOS proof or Windows proof.
Both logs and `review.json` preserve the exact results.

Positive invariant checks also cover source-registration with all remote source
modules configured enabled and retained Scout findings present/absent. GLEIF
and remote source-producing agent constructors are forbidden; the orchestrator
and Scout store are spies, so no agents or database operations execute. The
captured constructor bypasses the online client and both persistent cache
initializers. Captured input selection leaves prospective auditing disabled.

Local retained Scout auditing can still perform its existing authorized history
work during a real normal run; this review does not claim that full normal
operation has zero DB writes. Independent explicit SEC/scout collection controls
are outside this normal-run boundary. Finalizer persistence timing was reviewed
separately by the contracts lane.

`reproduce_peer.py` evaluates the frozen provenance expressions/UI guards and
the frozen class methods under strict spies. It refuses to replace existing
source snapshots or logs. A new reproduction needs a fresh directory containing
the script and `before/` input snapshots, with the root source path adjusted to
the exact integration checkout under review.
