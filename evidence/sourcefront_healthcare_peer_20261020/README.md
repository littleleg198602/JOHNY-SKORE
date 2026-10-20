# Independent CMS runtime attribution peer

The public96 runtime accepted both retained HCA projected owner rows as
`DATED_IDENTITY_VERIFIED` with issuer attribution enabled. The strict
original-byte reviewer withheld those same rows because original CMS response,
provider CCN/PAC crosswalk and issuer-cohort binding were unavailable.

The new independent peer has six methods. Its original run executed all six,
with six failing subtest entries across three methods and zero errors/skips.
The three passing controls preserved exact query leads, explicit wrong-object
and wrong-issuer denial, and an unrelated exact dated FDA identity.

The same test bytes, SHA256
`bef8f5403e33c721ae0b125a5eed45f736a87cd6c28b1f102f939dcd5c3470ba`,
passed once against healthcare owner commit
`d0fca0a2623919b908d6a6469a0e7b86d1e709f7`: six discovered/executed,
zero failures, errors, skips, expected failures or unexpected successes.
The qualified identity producer SHA256 is
`e56330143e716bbc3d86ee02ab3322a484e3f05af5e9133cb4385fc9b1b73192`.

Integrate the owner fix before the new test. This peer branch leaves public96
production and registry bytes untouched; the after runner imported the exact
separate committed owner tree. `before/` preserves the whole original
producers, registry, actual projected rows and frozen assertions. Full console,
counts and runtime diagnostics are saved separately for each run; the failure
record is retained.

`run_peer.py` verifies the explicit source commit, identity SHA and unchanged
assertions before importing only these six methods. It creates each evidence
filename exclusively to prevent accidental replacement of the recorded runs.
Ordinary later test discovery can run the new test without rewriting these
proofs. Source and registry hashes remained unchanged during both runs.

The same-day case shows that matching an observation date cannot replace a
missing original-body/object binding. The foreign-enrollment metadata and
CCN/PAC values are adversarial controls, never acquired captures or adopted
identities. Deterministic test cutoffs are mechanism inputs, not historical
availability or live acceptance evidence. No network, collector, SQL, database,
quota, registry, score, model or trading operation was performed. Complete
original CMS cases and durable source history remain missing.
