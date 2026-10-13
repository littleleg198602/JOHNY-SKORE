# Primary IR publisher review — 13 October 2026

This implementation verifies one RSS lead against captured original publisher
content. It performs no runtime HTTP, changes no provider quota/lease and grants
no issuer attribution or score. `verify_primary_ir_lead` accepts bounded
`CapturedIRDocument` objects for the feed, original announcement and IR portal,
an explicit publisher-host policy and a requested knowledge cutoff.

Four actual raw HTTP 200 responses were captured using **five requests**,
including the exact IR portal's `https://aka.ms/AA13ep0o` redirect. The shared
hard limit was six requests, two MB per response and a 15-second timeout.
`captures.json` pins actual URLs, resolved URLs, clocks, byte counts and hashes.
No source-only fixture is counted as a LIVE response.

The captured Microsoft press-release feed has ten entries. Two exact original
announcements passed source-only checks: the 7 October quarterly earnings
release-date announcement and the 15 September dividend increase. The first
is a prospective announcement, not already released financial results. Exact
RSS/page title, original/canonical URL, explicit publication clock, permitted
publisher hosts, IR link scope and quoted article-paragraph provenance are
checked. Header/script/footer text cannot establish an article claim.

The negative control uses the actual HTTP 200 IR homepage as the dividend
lead's alleged original page. It remains `PARTIAL` because the page binding,
article headline/body, canonical and publication checks fail. This is not a
completed negative issuer search or a claim of all-publisher coverage.

`source_review.json` is reproduced without networking:

```sh
python -m evidence.batch4_ir_20261013.build_review
```

The read-only helper reports `SOURCE_VERIFIED` only for captured publisher
content. It never verifies claim truth, dated legal identity, instrument
identity, price impact or trading. Publisher `MSFT` mentions remain as-named
claims, not independent issuer proof. Reported September/October publication
clocks remain distinct from actual 13 October capture availability; exact first
dissemination is unverified.

`NewsScoutService.verify_primary_candidate` imports the audit beside an
existing RSS lead. Every database RSS row remains `UNVERIFIED`, including a
successful source-only review. The original lead stays unchanged and open;
the new review cannot enter an issuer-scoped verified slot or change sentiment.
Repeated imports of the same captured evidence are idempotent.

The requested cutoff stays in the report. Local availability is independently
anchored to physical UTC import completion, with a floor at actual input
availability. Old archived cutoffs cannot backdate a new local verification.
`local_import_completed_at`, `local_verification_available_at` and the returned
`persisted_available_at` distinguish physical import from source/cutoff clocks.
Synthetic tests patch the physical clock; production callers cannot supply it.

The final targeted/news/runtime suite passed **44 tests** on Linux/Python
3.12.14, with no failures/errors/skips. It includes a real-style archived replay
that is invisible at the old cutoff and leaves its original lead unchanged,
plus future-cutoff/idempotent first-availability checks. Source hashes are
pinned in `tests.json`; the full output is `tests.log`.

No production registry, score, license or shared app/model changed. No Windows
end-to-end, OOS, complete coverage or specialist completion is claimed. The
remaining concrete task is quota-managed leased LIVE primary traversal and
independent dated issuer/instrument evidence before issuer-scoped acceptance.
