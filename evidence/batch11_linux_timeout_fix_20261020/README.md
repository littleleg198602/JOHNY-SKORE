# Linux CI job headroom only

The only workflow change is `jobs.contract-agent.timeout-minutes: 10 -> 20`,
with a comment explaining the observed cancellation boundary and its limits.
Windows remains 60 minutes. Platforms, Python, dependency installation, compile,
SEC checkpoint, full-suite commands, test selection/assertions, canonical
687/60-second test budgets, always-upload and release gate are unchanged.

The coordinator supplied the actual current-run facts: run `39391828283`, Linux
job `118524938257`, started `2026-10-20T08:13:19Z`, completed/cancelled
`2026-10-20T08:23:35Z` (616 seconds), 2,576 started test headings, no final
footer/result JSON, and `The operation was canceled.` in the decoded log. No
explicit timeout annotation was observed. A timeout near the configured
10-minute job limit is a **duration-based inference**, not a proven annotation.
These incomplete results establish no full Linux test acceptance. The original
cancelled log capture belongs to the coordinator; this worker made no GitHub
requests or new log download. The coordinator subsequently preserved its decoded
log in `evidence/batch11_linux_cancelled_20261020/`: 641,826 bytes, SHA256
`118ff7acae6dff100f0450a73b643490062d9e35aa410640400a7bc0cdbbfc87`.
Its offline verification found 2,572 strict original OK blocks and 205/212 new
methods with original OK; seven new-method headings are absent. These are
partial-log observations, not final failure/skip counts or full acceptance.

The coordinator's separate local 2,688/2,688 plain-OK result (446.920 seconds)
was already qualified for an identical baseline source tree. It was not rerun
here and does not qualify this new workflow HEAD. No action was taken on the
Windows job `118524938510`, which was running at assignment. Its later state
must be checked by the coordinator, not inferred from this proof.

Baseline public commit is `b24a59563d8284daf8d38df20568fc76d4f03b60`, tree
`9e03573df7cfd65c2f878bc2ad65348851cc238e`, sole parent `6ba4f44…`.
The original workflow is frozen byte-for-byte in `original_workflow.yml`:
4,573 bytes, SHA256
`49126bef29f9f5034077ffe6b5aa0f043c69b8feb6182969093f99424f4ecc38`.
The revised workflow is `revised_workflow.yml`: 4,878 bytes, SHA256
`c8d1f21085815a92923b6312f55e1bc4da176195cfb04c5b42f551678d08ebf4`.

`verify_workflow_change.py` passed and wrote `qualification.json`. It parses
both versions with duplicate-key rejection and GitHub's `on` key preserved as a
string, rather than YAML 1.1's boolean interpretation. Recursive comparison
finds exactly one semantic delta: Linux timeout integer 10 to 20. Exact raw-byte
comparison permits only that scalar replacement and the explanatory comment.
All job-step hashes and parsed semantics outside the one key match.

The verifier also checks every prior baseline file. All 230 production files,
209 tests and 2,210 prior evidence files match their raw Git blob bytes; all
other old files remain unchanged. The exact nine existing CRLF batch launchers
are compared with Git's configured whole checkout bytes. No file is normalized
or rewritten. No applicable local `AGENTS.md` was found. No source/test/status,
model/config/registry, production DB or quota change occurred; no SQL, source
HTTP, native analysis, full suite or remote write ran.

This proof is intentionally scoped to the worker lane: workflow plus this new
evidence directory. Coordinator cancellation archives/docs and final-tree
publication require their own integration proof. After publication, the new
exact HEAD still needs its hosted checks; existing or in-progress CI is not
claimed as qualification for this repair.

Reproduce locally with the prepared interpreter:

```bash
PYTHONDONTWRITEBYTECODE=1 /workspace/scratch/334b3a149083/johny-tests-20261011/bin/python evidence/batch11_linux_timeout_fix_20261020/verify_workflow_change.py
```

The command preserves the existing qualification rather than overwriting it;
for an additional read-only check import the module and call `verify()`.
