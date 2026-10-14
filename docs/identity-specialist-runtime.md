# Identity specialist: current registered classes

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
and historical acceptance are evidenced. The current live class gap is OZK;
access to the official Nasdaq symbol directory requires the pending environment
network setting for `www.nasdaqtrader.com` to be saved and published.
The collector and strict OZK parser are ready. After that setting is active,
run `python -m market_checker_app.nasdaq_listing_capture`. It retains original
directory bytes and requires the exact Bank OZK common-stock row, a unique
symbol and the independently corroborating fresh SEC CIK. Preferred, test,
ETF, future, stale and ambiguous rows grant no common-equity credit. Successful
captures are read by the default entity registry as an exchange observation;
they are never relabeled as SEC covers. Parser tests use controlled data and do
not establish that the currently blocked live request succeeded.
