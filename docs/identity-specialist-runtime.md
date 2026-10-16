# Identity specialist: current registered classes

## User scope update, October 15

OZK and LEG are explicitly excluded from specialist 1 completion coverage from
the recorded user decision time. The canonical CSV and prior identity versions
remain intact. Current required issuer/class coverage is 685/685; excluded
instruments cannot pass the quality gate. Historical runs before the decision
retain their original scope.

The default counterparty registry now contains 683 distinct issuer legal
identities (two issuers have multiple watchlist share classes) and two dated
subsidiary observations. Each legal name is bound to its own CIK by validated
inline-XBRL contexts, including explicit CIK/name pairs in joint registrant
reports. Exhibit 21 rows have separate primary citations. The subsidiary rows
describe the December 31, 2025 snapshot only; they do not assert uninterrupted
ownership or transfer parent financials to the subsidiary. Default direct
issuer records expire at their declared effective end. Refreshing requires a
new primary review, and historical records remain dated.

Evidence labelled `VALIDATED_INLINE_XBRL_FACTS` is a named-field projection of
the original facts, rather than a verbatim sentence invented for the filing.
`SEC_EXHIBIT_21_ROW` retains the literal heading and selected row. Combined
subsidiary/joint-venture headings are not sufficient to identify a subsidiary.

Ten additional SEC documents were captured after freezing that registry.
Their original compressed bytes, hashes and publication/capture times are in
`evidence/identity_holdout_sources_20261015.json`. The unchanged resolver
correctly identifies five cases and abstains on five outside the curated date
scope. It makes zero wrong CIK or financial attributions and grants no issuer
attribution before registry knowledge. This bounded identity check does not
establish predictive return performance or complete historical coverage.

Run the same retained-evidence replay on Linux or Windows:

```sh
python -m market_checker_app.identity_specialist_acceptance --archived-cutoff --output outputs/identity-replay.json --database outputs/identity-replay.db
```

Use a fresh database path for every run. Omit `--archived-cutoff` for current
coverage, where stale captures remain unresolved. The report measures issuer
and class coverage separately from the 519 documented healthcare objects and
from gaps in subsidiary/product portfolios. It does not mark the complete
specialist accepted. The Windows CI job now executes this replay and preserves
its result, but a prepared job is not an observed successful Windows run.

## Baseline before the scope update (October 14)

The default entity registry now resolves exact issuer CIKs from a retained SEC
catalog and exact registered classes from primary cover contexts. The canonical
687 keys stay unchanged. In the October 14 capture, 685 have verified registered
class observations, LEG is inactive after its merger, and OZK has an issuer CIK
but no newly verified registered-class cover. Existing reviewed Bank OZK/FDIC
evidence remains available to the banking specialist; it is not relabeled as a
new SEC cover observation.

Class titles are literal disclosures with reporting dates, publication times,
observation times and source digests. These records do not establish a continuous
historical class interval or infer CUSIP, ISIN or LEI. The SEC catalog and class
captures expire after seven days. Explicit instrument registries are preserved.
Future, stale, ambiguous or mismatched evidence grants no new class credit.

Reviewed events retain XOM's old issuer history separately from its successor,
retain LEG's own legal identity separately from its parent, and resolve the
canonical PSTG key to the independently observed current symbol P. The precise
historical PSTG/P transition date remains unknown; the provider alias starts at
the review observation and requires a fresh SEC catalog.
For events disclosed with date precision, the previous identity ends at the
start of that date and the new state begins the next UTC day. The uncertain
transition day grants no identity credit.

## Refresh and review

From the repository root, provide the genuine contact User-Agent through
`JOHNY_SKORE_SEC_USER_AGENT`. Do not write it into repository files. Run:

```sh
python -m market_checker_app.issuer_catalog_capture
python -m market_checker_app.primary_listing_capture --tickers C,XOM,PSTG,LEG --workspace outputs/primary_listing_capture
```

The second command requests only the selected canonical keys, tries at most six
recent issuer reports and up to three explicitly linked securities exhibits per
report, preserves primary bytes in a checksummed archive, and stops on SEC access
or rate-limit blocks. Normal SEC size and TLS limits remain in force. It writes
`bindings.json` for the selected keys, with explicit unresolved/inactive lists.
Use `JOHNY_SKORE_PRIMARY_LISTING_REGISTRY` to select that immutable capture for a
run; select a complete reviewed registry when analyzing the whole universe.
`JOHNY_SKORE_ISSUER_CATALOG_SNAPSHOT` can select an immutable catalog for replay.
Neither capture command edits production manifests or changes scores.

Citi's registered securities are in Exhibit 99.3. Contexts may come from its
cover only when the cover explicitly links the exhibit within the same issuer
accession and both have the same publication time. Their hashes are separate.

## Remaining acceptance

The implemented issuer/class layer does not complete the wider subsidiary,
brand and product identity requirements. The inventory therefore remains
PARTIAL until those relevant identities, actual Windows end-to-end execution
and historical acceptance are evidenced. OZK is now waived by the explicit
user scope, so Nasdaq access is no longer a completion prerequisite.
The remaining Windows run is prepared in CI. Git transport works, but the
GitHub API request needed to manage/check the run is currently denied with
`Forbidden`. A draft adds only `api.github.com` to the existing custom egress
rules; saving the draft does not apply or publish that network change.

## Local regression verification, October 16

The complete Linux deterministic suite passed 969 tests with zero failures,
errors or skips; the durable report is
`evidence/identity_scoped_tests_20261016.json`. This includes the unchanged
60-second pipeline limit for the complete 687-ticker cached-data contract.
Progress updates now copy flat display rows directly and fully detach nested
rows, callback state and caller input. This removes repeated recursive copying
of the entire progress history without weakening snapshot isolation.

The separate current-cutoff acceptance replay also passed with 685 verified
registered classes and 687 persisted canonical identities. Neither Linux result
is evidence of an executed Windows job or complete relationship portfolios.
