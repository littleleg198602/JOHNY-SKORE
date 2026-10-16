# Ford and GM NHTSA product-scope proposal

This reviewed proposal adds two exact 2026 vehicle scopes for coordinator review. It does not change the production manifest or any service implementation.

| Ticker | Exact recall manufacturer | Make / model | Year | Live campaigns |
| --- | --- | --- | --- | --- |
| F | Ford Motor Company | FORD / F-150 | 2026 | 5 |
| GM | General Motors, LLC | CHEVROLET / SILVERADO 1500 | 2026 | 3 |

`models.proposed.json` loads through the existing `load_verified_models` function. `proposal.json` contains the legal entity chain, primary-source locators, explicit review inferences, observed clocks and limitations. `capture_index.json` inventories all 24 one-shot official-source requests: 22 HTTP 200 responses and two HTTP 400 responses. Raw response bytes are preserved; SEC HTML is stored as deterministic gzip with both compressed-archive and original-response SHA-256 hashes.

Ford's own-CIK 2025 SEC 10-K identifies Ford Motor Company and common-stock ticker F, and describes F-150 wholesales. Exact NHTSA manufacturer ID 976 and make/model responses corroborate the selected entity and product. NHTSA's acknowledgement dated February 25, 2026 explicitly lists F-150 years 2021-2026; this proposal adopts only 2026.

GM's own-CIK SEC 8-K, filed August 11, 2026 for the August 7 event, explicitly identifies General Motors LLC as a wholly owned subsidiary of General Motors Company. Exhibit 21 independently lists that Delaware entity but its mixed subsidiary/joint-venture heading is not sufficient by itself to establish ownership. Exact NHTSA manufacturer ID 984 uses GENERAL MOTORS LLC. The November 25, 2025 Part 573 report and December 2 NHTSA acknowledgement use General Motors, LLC and explicitly identify 2026 Silverado 1500. Accepting the comma variant is a stated review inference for this exact scope; the service continues to match the literal comma-form manufacturer.

The knowledge clock is **2026-10-12T15:08:33.934642+00:00**, after source capture and review. Source dates and campaign production windows are preserved as facts, with no invented ownership interval or historical availability. The observations establish a documented product relationship; they do not establish listed-parent legal liability, a fine, financial impact, a complete vehicle group, scoring or trading authorization.

Two empty recall probes were HTTP 400, including vPIC-confirmed 2026 Cadillac CT4. They remain **PARTIAL**. The Ford F-650 SD probe also has an ODI/vPIC model-label difference. Neither failed probe is included in the candidate manifest, and neither establishes a complete negative.

`replay.json` records an offline replay of the actual captured responses through the existing scout: 5 Ford plus 3 GM findings, no duplicate after restart/monthly refresh, and no finding or complete check for either HTTP 400 probe. Its processing-clock controls are not historical evaluation. `validation.json` records 11 passing checks: seven new archive-backed validations and four existing service tests.

Run the checks from the repository root:

```bash
python -m unittest tests.test_batch3_regulatory_nhtsa_evidence tests.test_nhtsa_recall_scout_service -v
```

Production adoption belongs to the identity owner. Other models, years and manufacturer affiliates, complete-negative live acceptance, user Windows execution and all-universe regulatory completion remain unverified.
