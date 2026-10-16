# Bounded primary legal-counterparty proposal

This is an evidence proposal for independent registry-owner review. The lane
wrote no production registry. Its five runtime-shaped records were validated
with `DatedCounterpartyIdentityRegistry.load` and replayed against the actual
archived primary bytes. They establish exact names and identifiers; they do not
establish supplier/customer exposure, concentration, financial impact or a
counterparty rating.

| Exact legal entity | Identifier | Proposed role | Source event/snapshot day |
| --- | --- | --- | --- |
| Microsoft Corporation | SEC CIK 0000789019 | DIRECT_ISSUER, MSFT | 2026-07-29 |
| Lockheed Martin Corporation | SEC CIK 0000936468 | DIRECT_ISSUER, LMT | 2026-01-29 |
| Apple Inc. | SEC CIK 0000320193 | DIRECT_ISSUER, AAPL | 2025-10-31 |
| NVIDIA Corporation | SEC CIK 0001045810 | DIRECT_ISSUER, NVDA | 2026-02-25 |
| SIKORSKY AIRCRAFT CORPORATION | GLEIF LEI 54930054HRQM5ZB1WZ32 | PRIVATE_LEGAL_ENTITY, issuer fields null | 2026-10-12 |

The four public entities are in the existing 687-ticker canonical universe.
Sikorsky is also an existing named legal recipient in the LMT UEI pilot. Its
independent GLEIF record reports entity `ACTIVE`, LEI registration `LAPSED` and
conformity `NON_CONFORMING`. These are retained as recorded; the LEI is not
represented as renewed or conforming.

Each `effective_from` is the declared source event/snapshot day. Each
`effective_to` is the following date, an exclusive day-granularity schema gate.
This gate is neither a documented corporate expiry/change nor proof of a
continuous legal-effective interval. `legal_effective_interval_verified=false`
is retained on every row. `known_at` is the actual local completed review clock
after byte capture, so no row becomes known at its historical source date.

`withheld_subsidiary_candidate.json` preserves the separate exact GLEIF entity
proof, LMT issuer proof and the exact Sikorsky row under the title of LMT's
2025 Exhibit 21. The register snapshot day is 2026-10-12 and the parent exhibit
report date is 2025-12-31. These do not establish a common effective day or a
continuous/current ownership interval. The candidate has no `records` list,
cannot load as a runtime registry, and explicitly denies issuer and financial
attribution. The runtime Sikorsky proposal has only its independent register
identity and no public issuer link.

All 15 captures contain unmodified HTTP response bodies from `www.sec.gov`,
`data.sec.gov` or `api.gleif.org`. `captures.json` preserves each URL, actual UTC
retrieval/observation times, HTTP status, content type, Date, Last-Modified, byte
length and SHA-256. The capture used ordinary declared project identification,
no credentials/cookies, a two-million-byte per-record bound, same-publisher
redirects and no retry. A 401/403/429 would stop that publisher; none occurred.
The configured application SEC client guard was unchanged.

SEC `filings.recent.acceptanceDateTime` was captured with its explicit `Z`.
The original index Accepted text and HTTP Last-Modified remain separate. The
index does not explicitly label a timezone; an America/New_York interpretation
is labelled as such solely to expose clock conflicts:

| Issuer | Reported API acceptance | Index Accepted text | Index HTTP Last-Modified |
| --- | --- | --- | --- |
| MSFT | 2026-07-29T20:08:01.000Z | 2026-07-29 16:08:01 | Wed, 29 Jul 2026 20:08:01 GMT |
| LMT | 2026-01-29T22:18:08.000Z | 2026-01-29 17:18:08 | Thu, 29 Jan 2026 22:18:08 GMT |
| AAPL | 2025-10-31T14:01:26.000Z | 2025-10-31 06:01:26 | Fri, 31 Oct 2025 10:01:26 GMT |
| NVDA | 2026-02-26T02:42:19.000Z | 2026-02-25 16:42:19 | Wed, 25 Feb 2026 21:42:19 GMT |

AAPL/NVDA conflicts are preserved, not corrected by guessing. Their evidence
`published_at` is the actual first-observed availability bound, separately
labelled; the API timestamp remains `reported_acceptance_at`. For MSFT/LMT the
field retains the reported SEC acceptance event, expressly distinguished from
first renderer dissemination. GLEIF retains the reported golden-copy snapshot
publication event. No evidence claims a verified first publisher publication:
`publication_time_verified=false`, `verified_first_publication_at=null`. HTTP
Last-Modified is not used as first publication. Actual local `known_at` controls
resolution in every case.

NVIDIA's R1 XBRL registrant name is abbreviated `NVIDIA CORP`. Its full cover
independently renders `NVIDIA CORPORATION` as the exact charter name. The cover
also contains the explicit inline CIK fact and the common-stock ticker in the
column labelled `Trading Symbol(s)`. The proposal uses those exact cover
fragments and the same-column ticker transcript, not an inferred alias. The
service change accepts only that additional literal parenthetical plural
label. Exact symbol, legal identifier, independent citation and dated matching
requirements remain enforced; `NVIDIA CORP` is not a disclosed-name alias.

Offline verification reads the frozen proposal and primary captures:

```bash
python -m unittest tests.test_sec_counterparty_identity tests.test_batch3_sec_counterparty_proposal tests.test_supply_chain_evidence_service tests.test_filing_exposure_discovery_service tests.test_company_network_stage3 tests.test_counterparty_health_service tests.test_sec_document_scout -v
```

All 64 tests passed. New replay coverage includes primary-byte hash/length
integrity, exact SEC and GLEIF field locations, the NVIDIA cover column,
conflicting publication clocks, absent aliases, time cutoffs, snapshot-day
expiry, same-document/query independence failures, the withheld parent bridge,
and disabled financial attribution. `tests.log` and `verification.json` retain
the result and hashes.

`build_proposal.py` can rebuild from archived bytes without network access or
production writes; rebuilding records a new actual review/knowledge time.
`capture_primary.py` refuses replacement of existing captures and only appends
planned missing sources with `--append-missing`. Offline replay requires no
fresh requests. Fresh live exposure ingestion, broad historical/legal
continuity, a date-aligned Sikorsky ownership bridge, user Windows and financial
impact are unverified.
