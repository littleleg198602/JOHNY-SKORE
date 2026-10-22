# FDA original structured application/product archive adapter

This batch implements a pure production adapter; it does not collect sources or adopt identities. The actual retained three-issuer corpus remains **PARTIAL / WAIT_DATA**. Nine original public captures were checked offline: three FDA CRL responses, five SEC issuer filings, and the independently captured Pfizer Exhibit21. The CRL responses contain five events for four distinct applications. None is an original DrugsFDA structured application/product response. Imported structured products, proposed applicant records, adopted records and new verified instruments are all zero. This is not acceptance of any of the 21 specialists.

`market_checker_app.services.fda_structured_product_archive` exports:

```python
FdaProductArchiveCapture(metadata: Mapping, body: bytes | None)
FdaStructuredProductSelection(
    application_type, application_number, sponsor_name, issuer_ticker, issuer_cik,
    product_number=None, dosage_form=None, active_ingredients=(), source_version=None,
)
review_fda_structured_product_archive(
    *, selection, structured_capture, issuer_capture, reviewed_at: datetime,
) -> dict
```

The selected application type is exactly `NDA` or `BLA`, independently pinned from the six application digits. Product numbers remain three-character strings; dosage form and every ingredient/strength pair remain exact structured source fields. These conservative import constraints preserve leading zeros and reject unsupported formats. A brand, manufacturer annotation, letter mention or product number appearing in text cannot substitute for structured `results[].products[]` rows. Unselected source products retain distinct forms, ingredients and product numbers. Duplicate JSON fields, application rows, product numbers or ingredient identities are ambiguous and rejected.

For a reviewable applicant snapshot, the exact application query must be complete; the selection must pin product number, form, all ingredient/strength pairs and publisher `last_updated` day. The original whole-body SHA-256 pins the precise observed version; `last_updated` is not asserted to be first public availability or a unique publisher version identifier. A separate original SEC submissions response must bind the exact requested CIK, ticker and named applicant in the same UTC observation cohort and be known **strictly before** the FDA request. Only letter case and surrounding whitespace are tolerated in the independent SEC name comparison; punctuation, entity aliases, ownership groups and brands are not normalized. A periodic cover or Exhibit21 cannot substitute for this direct applicant bridge. Earlier fiscal periods and present-day captures do not establish historical knowledge.

The resulting candidate is only an `APPLICATION_SPONSOR_APPLICANT` observation snapshot. Applicant identity does not establish a legal product owner, parent, manufacturer, intellectual property right, approval holder at a historical event, or a continuous ownership interval. Every result keeps issuer attribution, registry adoption, legal ownership, parent ownership, historical prediction use, live acceptance and issuer-wide completeness false. Source product facts can survive an unavailable issuer bridge; no candidate is produced without the bridge.

Original metadata and bytes pass a 4,000,000-byte whole-body cap, a 64,000-byte metadata cap, exact SHA-256/length checks, sensitive whole-body/decoded-JSON gates, explicit official HTTPS URL/query checks, no redirects, aware request/observation/knowledge clocks, a 64-container JSON nesting cap and bounded product/ingredient counts before interpretation. Unsafe or unavailable bodies are never cleaned, encoded, restored or substituted. This batch did not read the three known withheld IR/NHTSA bodies.

`retained_pfe_review_plan.json` uses `kind=FDA_STRUCTURED_PRODUCT`, `request.selection`, and `captures.structured/issuer`. Its absent structured body has `body_available=false`, `body_ref=null`, no fabricated HTTP clock, and `retention=MISSING_BODY`. The retained Pfizer annual raw body is pinned but does not establish a current applicant bridge. `retained_crl_diagnostic_plan.json` separately supplies the unchanged original CRL bytes and URL. The adapter rejects that dataset before JSON product interpretation. Neither plan invents a product number, form or strength from the trastuzumab letter mention.

Reproduce actual original-byte inventory without HTTP or database access:

```sh
python -m evidence.batch8_healthcare_20261015.replay_originals
python -m unittest tests.test_batch8_fda_structured_products -v
```

`source_review.json` preserves actual physical review/ingestion clocks, original source clocks, byte hashes, selected-source counts and exact blockers. Running the replay later creates a later review clock; it does not change source availability. Focused positive contract cases are explicitly synthetic and exist only in the test file. Actual CRL bodies only exercise the truthful wrong-dataset/missing-structured-source rejection. HTTP400/404 empties and an incomplete page are not accepted negative issuer coverage. The full1835 baseline suite was not repeated.

Official format references consulted read-only: [DrugsFDA overview](https://open.fda.gov/apis/drug/drugsfda/), [searchable fields](https://open.fda.gov/apis/drug/drugsfda/searchable-fields/) and [field reference](https://open.fda.gov/fields/drugsfda_reference.pdf). These document the application/products structure, sponsor and ingredient/strength fields. Documentation and examples are not source captures. No DrugsFDA data endpoint, new FDA/CMS/Clinical request, new/copy/reset quota database or alternate data channel was used. Original durable collector history remains unproven; the next source acquisition must use the coordinator's authorized retained ledger when recoverable. Seven production registries and the canonical687 universe remain unchanged; no score, model, trading or out-of-sample claim is made.
