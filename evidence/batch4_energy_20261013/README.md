# Historical filing commodity provenance — 2026-10-13

This concrete change distinguishes explicit company input/output disclosures
from a macro price or availability sentence in an annual filing. Input language
must be close to the named resource; unpunctuated HTML lists cannot borrow input
cost language from a distant section. New crude-oil/natural-gas output discovery
requires explicit first-person production/sales language, excludes negated and
prospective statements, and requires an exact SEC accession. Resources before
the output verb are not turned into outputs. Separate input/output directions
are retained without inventing quantities or revenue dependence.

Automatic CommodityEnergyAgent discovery validates the official unchanged
issuer archive path, ticker context, captured content hash and any matched SEC
filing-document metadata. Conflicting CIK/accession/report-period/acceptance
metadata is rejected locally; other completed filing observations survive.
Legacy generic-archive SEC scout questions keep their unverified candidate mode;
they cannot supply a new exact-accession output or precise acceptance claim.

Three actual public SEC responses were captured within a three-request budget
and 8 MB per-response byte cap: XOM's **historical CIK 0000034088** 2025 10-K,
its filing index, and that old CIK's submissions JSON. The index's accepted
2026-02-18 16:06:52 Eastern agrees with submissions **2026-02-18T21:06:52Z**;
both identify accession **0000034088-26-000045**, report period **2025-12-31**.
The primary file explicitly describes the registrant's principal business as
exploration/production of crude oil and natural gas. The bounded production
client/discovery/agent replay records exactly those two commodity outputs.

Source publication, reporting period, actual source observation, exposure
observation and knowledge cutoff are separate metadata. An independently cited
SEC acceptance clock can refine a filingDate-only midnight; a precise conflicting
clock cannot. Captures from today are not available at a historical cutoff.
Current production filing documents lacking acceptance metadata remain explicitly
unverified for precise publication time; this lane does not invent that value.

Provenance reaches resource exposures and their reference documents. It remains
scoped to the historical filing issuer: no current legal/instrument attribution
is assigned, no ownership interval is inferred, and the unresolved current-Q3
XOM successor/instrument issue is untouched. The production registries and
canonical 687-ticker file are unchanged. No price series is attached to these
company facts, no quantity/revenue share is inferred, and output sensitivity
remains unsupported by the existing input-cost scenario formula. Scores,
directions and risk contributions remain zero; no OOS, Windows, coverage or
specialist-DONE acceptance is claimed.

`capture_index.json` records all source URLs, clocks and exact archive/raw hashes.
`replay_captures.py` invokes the production ShortReportClient extractor, discovery
service and CommodityEnergyAgent using the original captured bytes without HTTP.
`replay.json` holds their exact result. Ten new regressions and the applicable
existing filing/network/scenario/SEC-scout/pipeline tests are logged in `tests.log`.
The branch proof is `evidence/batch4_energy_20261013.json`.
