# ClinicalTrials sponsor and record snapshot scope — 13 October 2026

The collector now validates publisher first-post, update-post and update-submit
days, preserves their day precision, and records our observation separately.
Malformed sponsor/intervention arrays remain partial checks. ClinicalTrials
scope fingerprints change to invalidate checks made by the previous resolver.
Newly known competing date-only links also invalidate pinned trial scopes,
because those older links can make the same exact object ambiguous.

New clinical trial identity records can pin a SHA-256 digest of the exact NCT,
lead/collaborator names, intervention names and publisher days. The digest is a
relationship snapshot, not an immutable publisher version ID. API projections
that omit the ACTUAL date labels produce the same relationship digest as the
full record. Existing date-only trial records remain compatible and are
explicitly distinguishable from pinned records.

`ClinicalTrialsClient(study_ids=(...))` accepts at most 20 exact NCT IDs. Such a
query always remains a partial broader issuer check, including a successful
zero-row query. It cannot establish issuer-wide study completeness.

## Actual primary responses

Ten original response bodies are preserved with request/observation times,
official URLs, byte budgets and original-byte SHA-256 hashes:

- An exact two-study Lilly sponsor traversal returned SUMMIT `NCT04847557` and
  SURPASS-2 `NCT03987919`. It required three pages: the publisher supplies
  `totalCount=2` only on the first page and a next token after the full second
  page, then returns a terminal empty third page.
- The exact same two studies with sponsor query `Pfizer` returned HTTP 200,
  `totalCount=0`, zero studies and no next token. This is a negative for that
  query scope alone.
- The full current published records confirm the displayed lead sponsor
  `Eli Lilly and Company` and Tirzepatide intervention. SURPASS-2 also includes
  Semaglutide; SUMMIT includes Placebo. Neither inherits the proposed
  Tirzepatide sponsor link or product ownership.
- Lilly's 2021, 2024 and 2025 annual filings independently contain the issuer
  name, CIK, common-stock ticker and Tirzepatide mention. Archived SEC submissions
  also supply the filing dates. A source-year filing is not continuous legal
  ownership evidence or a historical trial availability archive.

The capture driver never overwrites existing bodies. `--resume` validates and
reuses captured bytes; its conservative request reservations include those
replayed cache reads. Every newly finished response updates the capture index.
ClinicalTrials responses are bounded to 4 MB each, SEC HTML documents to 8 MB
each, and the archive driver to 12 unique captured responses. It uses the
existing ClinicalTrials lease/request budget for query calls in a temporary
diagnostic store, without writing a completed broad source check.

## Replay and proposal

`identity_proposal.json` supplies two event-specific, pinned sponsor links for
the single production registry owner to review. This branch changes no
production registry. Each link's one-day event scope is the displayed record
posting date; knowledge starts only after the actual 13 October review.
Sponsor identity does not establish product ownership, efficacy, financial
impact, or a group's complete healthcare exposure.

Run offline from the repository root:

```bash
PYTHONPATH=. python evidence/batch4_healthcare_20261013/replay.py
python -m unittest tests.test_batch4_healthcare
```

`replay_report.json` verifies both exact query outcomes, the pinned relationships,
before-knowledge denial, same-day changed-relationship denial, and an idempotent
collector replay. Two stored findings remain UNVERIFIED. The broad source has
zero complete checks and one partial check; scores stay untouched.

Remaining work includes substantially broader product/sponsor/CMS mappings,
relevant exposure denominators, continuously observed source history,
point-in-time labels/OOS evaluation, and a real user Windows workflow. This
branch does not supply full live acceptance for any specialist.
