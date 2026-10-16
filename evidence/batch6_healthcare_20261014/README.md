Batch6 implements `review_cms_provider_ownership` in the separate CMS review
module. It reads supplied original bytes and metadata, joins one enrollment to
its exact provider PAC/legal name/CCN, checks one organizational owner PAC and
direct/indirect role, and requires an earlier independent SEC named-issuer
capture in the same UTC-day cohort. It returns a detached proposal or explicit
rejection. It does not call HTTP, construct a store, write a registry, grant
issuer attribution, or change a score.

The original two HCA fixtures are **projected publisher fields**, as their
unchanged 11 October manifest explicitly documents. They lack the provider PAC,
CCN crosswalk and original HTTP request/response. Both exact enrollment reviews
are withheld. The projection's preserved byte hash is not an HTTP-body hash.
The old owner association days (2007-06-28 and 2011-08-05) are not publication,
historical availability or application knowledge dates.

One authorized independent SEC research GET succeeded on 2026-10-14 at
13:35:04.920566 UTC. Its entire safe 161899-byte JSON is preserved, without
rewriting it. It names `HCA Healthcare, Inc.`, CIK 0000860730, ticker HCA.
The retained CMS legal owner is `HCA HEALTHCARE INC`. No punctuation/brand alias
or former-name ownership interval is inferred. This later/current SEC capture
also cannot backdate the original incomplete 11 October CMS observations.
There are **0 proposed/adopted records**, 0 new CMS/Clinical collector API calls, and no
quota store creation/reset/copy. Current-day original CMS reservation history
was not proven, so the source status is `WAIT_QUOTA_HISTORY`.

The API accepts frozen `CmsProviderOwnerScope` and `CapturedSource` values.
Capture metadata requires `capture_form=ORIGINAL_HTTP_RESPONSE`,
`raw_publication_permitted=true`, integer `http_status=200`,
`complete_response_body=true`, the original `body_bytes`/`body_sha256`, aware
`requested_at`/`observed_at`, and `response_url`. If provided, `requested_url`
must equal `response_url`. Whole unsafe/unavailable bodies are rejected before
parsing. URLs are constrained before a pin can enter the output.

CMS supports the exact All Owners and Hospital Enrollments series endpoints,
with only `size` (1..100), `offset=0`, and `filter[ENROLLMENT ID]`. A full first
page is withheld because further mappings could exist. Projected/broad-name
queries, ambiguous provider/owner mappings, unsupported/non-numeric CCNs,
conflicting role/percentage/date fields, and later/unrelated issuer cohorts
remain rejected. A zero-row HTTP 200 exact query is only scoped absence;
HTTP 400 empties and partial pages are not complete negatives. The proposed
snapshot has one observed UTC-day interval and today's actual ingestion time;
CMS publisher snapshot/publication timing remains explicitly unavailable.

Reproduce the saved actual review without HTTP:

```bash
python -m evidence.batch6_healthcare_20261014.review_sources
python -m unittest tests.test_batch6_cms_provider_ownership -v
```

The review CLI reproduces its retained original physical ingestion clock. A
new runtime ingestion must supply its own actual aware `reviewed_at`; it must
not relabel these archived bytes as a current page. `capture_issuer.py` reuses
existing immutable metadata and issues no second request once it exists.

Positive tests use explicitly synthetic CCN/provider/issuer bodies. The raw
actual SEC capture and the unchanged projected CMS fixture have pinned hash
assertions. These tests do not establish LIVE positive/negative completeness,
group coverage, Windows end-to-end acceptance or historical/OOS evidence.
The original production healthcare registry and canonical 687 input remain
unchanged. The coordinator owns runtime/UI/report dispatch and registry review.
