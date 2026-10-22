# Independent wafer-capacity asset peer

The before producer is frozen from owner commit
`202303498cb68098247f2cb648e08cd99812d164`, SHA256
`8043382a12fed7575eec31b05f37e0fe4c26f8945a29a6417e25d523d5efbba8`.
Its complete original source was copied before any invocation. The peer owns
only its new test and this evidence directory; the producer was never edited.
The source-plan branch remains at `6239403a6e39fee8997b93d81962b260e9886d96`.

Twelve independent methods include the retained SWKS whole-source control and
synthetic semantic negatives. The before result is 10 passing methods,
2 failures and 0 errors/skips/expected failures/unexpected successes.
The complete test source, original full log, parsed diagnostics and actual
physical-clock control report are retained in `before/`.

The two failures are:

1. Fix the physical reviewer clock at `2026-10-21T08:00:00+00:00`, use the
   unchanged retained original inputs, and request
   `as_of=2026-10-22T08:00:00+00:00`. The producer returns
   `CANDIDATE_REVIEWED` with that future `as_of`, while `knowledge_at` stays
   on 21 October. The public review API does not reject a future requested clock.
2. In a synthetic in-memory copy of the original index, replace the unique
   Accepted text `2025-11-07 16:08:12` with `2099-11-07 16:08:12` and update
   its whole-body size/hash metadata. Keep the primary and all other dates
   unchanged. The producer returns `CANDIDATE_REVIEWED` and reports the
   year2099 acceptance display despite its original capture in2026. Unknown
   publication timezone does not resolve that impossible calendar conflict.

The other cases reject wrong outer nested unit or precision, a foreign entity
in the asset context, changed instant date, swapped current/noncurrent roles,
wrong issuer textblock context, wrong index basename and future input knowledge.
The old cutoff and validation completing after an explicit cutoff also reject;
the latter asserts that both physical-clock calls actually occurred.

The valid actual-source control preserves six components represented by
twelve equivalent tags, with the separate impairment tag excluded. It retains
USD/scale6/decimals-5, the two literal date instants, current/noncurrent split
and the known109-byte served/index size conflict. Asset balances do not become
purchases, physical capacity/volume, a named foundry, Apple obligations,
issuer adoption, continuity, execution or historical/OOS acceptance.

No HTTP/search/collector or SQL call was made. All producer invocations install
guards that raise if HTTP/socket/SQL is attempted. Mutated byte buffers are
explicitly synthetic derivatives, not new primary captures or LIVE evidence.
The twelve assertions are frozen for a separate after qualification; neither
the owner's32 tests nor any old/full suite was run.

The runner refuses to overwrite a phase's logs or frozen test source. A later
after run must point to a separately qualified producer root and its exact
expected source hash; it uses this same test file and frozen input plan.
