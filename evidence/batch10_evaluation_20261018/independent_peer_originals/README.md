# Independent readiness publication peer

The candidate was frozen before reproduction: 22,140 bytes, SHA256
`a11ab873d2fdaa88bed98f7ad65beb717dd386fd5aa5001c191ecf04f2551f17`.
`candidate_before.py` is the exact original producer; its physical freeze
clock and source path are in `candidate_before.json`. The peer did not edit
the candidate or its owner tests.

The seven new methods run against the evaluation worktree selected explicitly
by `run_peer.py --candidate-root ...`. Every source and SQLite database is a
temporary synthetic fixture with canonicalized native paths. Connections are
explicitly closed. The runtime probe is marked injected and the Windows/CI
strings are synthetic. Network is blocked; no actual production DB, source
collector, quota, account, private raw body or historical run is touched.

`before_fix_01.json` and the complete `before_fix_01.log` preserve the actual
result: **7 discovered=executed, 6 failures, 0 errors/skips/expected/unexpected**.
The exact candidate hash before and after is identical. The failing assertions
are six reproductions of one material final-publication validation gap:

- A whole pinned producer file changes after the temporary receipt is fsynced.
- An existing synthetic DB commits new main-file bytes at that point.
- A new synthetic WAL sidecar appears at that point.
- A previously absent synthetic DB is created at that point.
- The injected wall clock rolls back at that point.
- The injected monotonic clock rolls back at that point.

Each test calls the real `os.fsync` first and asserts that its actual late
publication callback was reached. The candidate then publishes with `os.link`
without revalidating these inputs or clocks. An earlier unrelated refusal
cannot pass the regression. Expected corrected behavior is refusal before
publication, with neither a final receipt nor a leftover temporary file.

The positive control confirms that an absent DB/run and injected hosted
Windows remain fixture-only, with all user-Windows/PIT/OOS/full-acceptance
flags false. The defect is stale readiness provenance at publication; **the
peer did not find an acceptance promotion**. Existing nonacceptance flags are
preserved and must remain false after repair.

The evaluation owner was notified with all six exact triggers and owns the
production correction. The peer tests remain unchanged for before/after
qualification. No corrected-candidate success is claimed in this first
handoff; the preserved failures are not an acceptance result.
