# Healthcare batch 3: archived CRL proposal and offline replay

Five proposed direct-issuer FDA letter relationships cover four applications and
three canonical issuers. The production registry is unchanged. Its sole owner
can review and integrate `identity_proposal.json`; `source_review.json` ties each
record to actual FDA response bytes and independently archived SEC fields.

| Issuer | FDA application | Product named in the letter | Letter date | Proposed record |
| --- | --- | --- | --- | --- |
| PFE / 0000078003 | BLA761081 | Trazimera (PF-05280014) | 2018-04-20 | `pfe-crl-bla761081-2018-04-20` |
| MRK / 0000310158 | NDA204569 | Suvorexant (MK-4305) | 2013-06-28 | `mrk-crl-nda204569-2013-06-28` |
| ABBV / 0001551152 | NDA216962 | Vyalev (foscarbidopa and foslevodopa), Solution for Infusion | 2023-03-17 | `abbv-crl-nda216962-2023-03-17` |
| ABBV / 0001551152 | NDA216962 | Vyalev (foscarbidopa and foslevodopa), injection for subcutaneous use | 2024-06-18 | `abbv-crl-nda216962-2024-06-18` |
| ABBV / 0001551152 | BLA761459 | AGN-151586 for injection | 2026-04-22 | `abbv-crl-bla761459-2026-04-22` |

The FDA source has arrays of applications and full letter text; it does not have
a `product_name` field in these rows. Product names come from that text. The two
NDA216962 rows share a PDF filename and remain distinct by letter date. A PDF
filename containing an approval year does not replace the signed letter date.

Each effective interval is the letter date through the next date, exclusively.
That interval bounds attribution to a single event; it does not represent the
actual beginning or end of application ownership. Every `known_at` is the real
review time, **2026-10-12T14:52:33.382658+00:00**. Per-request `observed_at` clocks
come from the completed HTTP captures. No first-publication time is asserted.

The independent SEC evidence is the PFE 2017 10-K, MRK 2012 10-K plus its 2013
second-quarter 10-Q, and ABBV 2023 first-quarter, 2024 second-quarter, and 2026
first-quarter 10-Qs. Legal issuer names and common-stock trading symbols are in
the archived forms. CIKs are in the official EDGAR archive paths; ABBV forms also
carry explicit inline CIK facts. No later parent cover is used to infer an older
subsidiary chain. No additional code-to-brand alias is proposed for AGN-151586.

## Query evidence

These are real responses from `https://api.fda.gov/transparency/crl.json`.
Completion concerns only the named query in the published CRL dataset snapshot.

| Capture | Search scope | HTTP | Rows / API total | Replay state |
| --- | --- | --- | --- | --- |
| `fda_crl_pfizer_exact_phrase` | `company_name:"Pfizer Inc."` | 200 | 1 / 1 | Complete positive query |
| `fda_crl_merck_exact_phrase` | `company_name:"Merck & Co., Inc."` | 200 | 1 / 1 | Complete positive query |
| `fda_crl_abbvie_exact_phrase` | `company_name:"AbbVie Inc."` | 200 | 3 / 3 | Complete positive query |
| `fda_crl_abbvie_partial` | Same AbbVie name, `limit=1` | 200 | 1 / 3 | Partial query; two rows remain outside that capture |
| `fda_crl_pfizer_2026_negative` | `company_name:"Pfizer Inc." AND letter_year:"2026"` | 404 | FDA `NOT_FOUND` / `No matches found!` | Complete no-match query only |
| `fda_crl_abbvie_2023_positive_control` | `company_name:"AbbVie Inc." AND letter_year:"2023"` | 200 | 1 / 1 | Real positive control for the year filter |

The `letter_year` field exists in these real API records, and its filter was
exercised by the positive control. It is absent from the captured public
searchable-fields documentation. The negative therefore describes the exact
executed expression; it is not a certification of all Pfizer exposure in 2026.
FDA documents infrequent updates and possible releases of archive batches. A
complete query cannot establish that every past or current issuer letter has
been published, nor cover recalls, devices, all subsidiaries, or all products.

## Archive and validation

`capture_index.json` inventories **18 actual bounded HTTP captures**: nine FDA
API bodies, two FDA documentation pages, six successful SEC filing bodies, and
one blocked SEC index response. Each request had a 20-second timeout and a
4,000,000-byte response bound. No pagination beyond the first page occurred.
Lossless gzip preserves HTML response bodies. Metadata includes separate hashes
of the original body and stored archive, request URL, status, and true request
and observation clocks. Nonessential `Set-Cookie` response values were omitted.

The optional PFE filing-index request returned 403. Acquisition stopped for SEC;
the blocked response remains archived and supplies no relationship evidence.
The Pfizer Ireland BLA761184 row in the broader Pfizer response is withheld:
its `c/o Pfizer Inc.` address does not prove dated legal ownership.

Run from the repository root:

```bash
python -m evidence.batch3_healthcare_20261012.replay --output evidence/batch3_healthcare_20261012/replay_report.json
python -m unittest tests.test_batch3_healthcare_evidence tests.test_healthcare_identity_service tests.test_healthcare_coverage_service tests.test_fda_recall_scout_service -v
```

`replay.py` feeds the actual archived body/status to the existing
`OpenFdaRecallClient`, its bounded pagination handler, and
`HealthcareIdentityRegistry`. It makes no network requests and writes no
runtime checks. The tests verify body tampering, source locators, exact objects,
first knowledge, wrong issuer/letter, exclusive event intervals, the shared
AbbVie filename, the withheld entity, and complete/partial/no-match separation.
The coverage diagnostic shows three documented object subjects, five historical
event links, zero runtime mappings/checks, and false acceptance flags. This work
does not certify live collector operation, issuer-wide completeness, financial
impact, scoring, trading, out-of-sample evaluation, or a Windows end-to-end run.
