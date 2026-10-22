# Original Everpure certificate specimen

One new SEC research request fetched the first image explicitly linked by the
retained EX-4.1 wrapper. The request was reserved before transport, bounded to
25 seconds and 4,000,000 bytes, with no redirect, retry or alternate route. HTTP
200 returned 917,915 original JPEG bytes on 2026-10-18 at 04:25:43.865560 UTC.
The original image is 1,978 × 1,318 pixels and has SHA-256
`c05da755dd60ea349f6368eb97e74cd07d93a3a6a176bd1d427467a9f7481f45`.
The second linked image was not requested. The wrapper/index were reused without
new HTTP and retain their original 2026-10-16 capture clocks and whole bytes.

The original image was visually inspected. It shows EVERPURE, INC., Class A
Common Stock, $0.0001 par value per share, and labelled CUSIP `74624M 10 2`.
Removing only literal spaces yields checksum-valid `74624M102`. It also shows
SPECIMEN and a blank Dated field. NUMBER, SHARES and holder are blank; the
consumer keeps these unknown, with no zero share count. The corporate seal's
October 2, 2009 date is not an action or issue date. Facsimile signatures do not
authenticate any person, issuance or transfer-agent registration. The printed
validity condition is not proof that this specimen was countersigned or issued.

`visual_measurement.json` is an explicit derived annotation of the unchanged
original image. It pins the source hash, dimensions, each field's original pixel
region, literal text and a separate physical measurement clock. The consumer
does not authenticate text from pixels or silently run OCR. The actual local OCR
execution and unmodified TSV are preserved separately; OCR misread the CUSIP
segment as `?74be4M`. The visual annotation is not a rewritten OCR or primary body.

`final_actual_review.json` records the final physical interpretation. Its exact
literal specimen class/CUSIP observation is useful source evidence. It provides
no dated PSTG-to-P continuity, effective action day, issued-instrument interval,
alias, Q3 coverage or verified identity. All nine blockers remain explicit,
including the original wrapper's index length conflict (1,252 declared versus
1,361 served bytes), acceptance/filing calendar-day conflict, unavailable reverse
image and unverified as-filed image version. The index's unqualified acceptance
display is retained without inventing a timezone or image publication timestamp.

The API is `review_sec_certificate_instrument(request, captures, *, as_of=None)`
in `market_checker_app/collectors/sec_certificate_instrument_review.py`.
`captured_review_plan.json` has kind `SEC_CERTIFICATE_INSTRUMENT` and exactly three
pinned capture descriptors. Root integration supplies their metadata and whole
bytes directly; no file discovery is needed. `certificate_image_references`
indexes only explicit visible same-directory image links. The consumer performs
no network request, filesystem write, collection, database, registry or score work.

The initial replay and producer are preserved. The existing bare class parser
safely rejected the whole sentence beginning FULLY PAID AND NONASSESSABLE SHARES
OF. The final consumer recognizes only that exact certificate sentence envelope,
retains the whole literal, and uses the unchanged strict class/par parser for its
field. `version_verification.json` confirms exact replay of the original version;
its injected clock is only historical-version verification. The final report
uses the later real physical reviewer clock.

The exact final source/tests passed 22 focused cases with zero failures, errors,
skips, expected failures or unexpected successes. The full log and qualification
JSON are pinned in `handoff.json`. They include actual original-image replay and
meaningful negative cases for source roles/links, withholding, clocks, CUSIP
uncertainty, original pixel bounds and class scope. Synthetic regression fixtures
are not live acceptance. No unchanged full suite was repeated. Integration CI,
user Windows end-to-end, historical availability and OOS acceptance remain open.

Registry adoptions and verified identities remain zero; canonical 687, historical
Q2 instruments 150, current Q3 verified coverage zero and full acceptance 0/21
remain unchanged. No collector API, quota database, model, fitter, score, budget,
trading, account or license changes occurred. Four withheld bodies remain absent
and were neither read nor restored.
