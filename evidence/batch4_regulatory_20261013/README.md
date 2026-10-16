# NHTSA exact-scope response validation — 2026-10-13

The concrete code change prevents unsuccessful or ambiguous response envelopes
from being persisted as complete monthly recall checks. Empty results require
the exact publisher success message. Explicit publisher error metadata also
invalidates nonempty envelopes. The real HTTP client requires HTTP 200, JSON,
the unchanged official route and exact query, unique JSON keys, and a 4 MB byte
bound. Existing exact-row matching, bounded model requests and partial findings
are preserved. A scope version plus manufacturer and product-evidence fields
invalidate previously accepted empties and checks after a scope correction.

Eight actual public requests are recorded, with per-request clocks and original
byte hashes. Seven exact model/recall response bodies remain publicly archived.
The documentation HTML is explicitly `WITHHELD_SOURCE_CONTAINS_ACCESS_VALUE`:
repository secret scanning detected an embedded access value. Its whole body is
excluded from the public tree, with no modified or compressed substitute. Only
its original URL, clocks, size and hash remain; it is not public raw evidence and
cannot supply acceptance. Four recall queries concern real model-year entries, with exact
publisher catalogs rather than an invented model name. CADILLAC CT4 2026 has a
fresh vPIC Model_ID 26602 entry. FORD F-650 SD 2026 appears in the prior-day
recall product catalog, `batch3_regulatory_20261012/raw/nhtsa_models_ford_2026.json`.
It is not present in today's separate vPIC response; this difference is
preserved rather than resolved by an invented identity relationship.

The exact CT4 and F-650 SD queries still return **HTTP 400** with Count 0 and
results `[]`. Neither is a successful complete negative, despite the body saying
`Results returned successfully`. Two other exact queries return HTTP 200:
CHEVROLET TRAILBLAZER 2026 has one campaign and FORD MUSTANG 2026 has five.
The bounded search did not establish any real HTTP200 complete-negative case.
The new synthetic successful-empty test validates a code contract only.

`replay_captures.py` replays the exact stored HTTP status and body through the
production HTTP client and collector without a network request. It uses local
request envelopes in a temporary database, records no complete check for either
HTTP400 response and preserves the successful positive campaigns. These local
query envelopes are not issuer/product proposals. No production registry was
changed; there is no new model for adoption, no broader issuer-negative claim,
no reconstructed historical availability, no Windows acceptance, no OOS claim
and no score/trading change.

The adjacent `capture_index.json` contains every actual URL, clock and byte hash;
`replay.json` contains the persisted collector results. The branch proof is
`evidence/batch4_regulatory_20261013.json`. Targeted validation includes all prior
NHTSA tests and all prior batch3 capture assertions; its complete log is stored
as `tests.log`; that original 22-test result is preserved as pre-withholding
history. Current seven-body and strict withheld-record validation is recorded
separately in `tests_publication_withholding.log`.
