# Certificate interpretation completion clock

The original `ab38e341` producer sampled the physical clock only at the start
of a review. Image decoding and the new derived interpretation happened later,
while `review_recorded_at` and `knowledge_at` still reported the earlier start.
It also could not reject clock rollback or completion after an explicit cutoff.
The original producer/tests and all 21 earlier certificate artifacts remain
preserved and pinned by `before_snapshot.json`.

The corrected API keeps source selection at the start cutoff. It reports
`review_started_at`, `source_as_of` and `as_of` at that start. After all image
decoding and derived field construction, it samples a new aware physical clock
and uses the completion for `review_recorded_at` and `knowledge_at`. Completion
must not precede the start, source knowledge or visual measurement. A completion
after an explicit cutoff is rejected before returning the interpretation. No
partial success or backdated availability is returned for these cases.

The seven new clock methods genuinely failed against the original producer;
`before_tests.log` preserves seven failures and zero errors/skips. The unchanged
new tests plus all 22 original methods then passed: 29 discovered/executed, plain
OK in 1.058 seconds, all failure/error/skip/expected/unexpected counts zero.
The new cases cover sequential clocks, a decoder and derived class parser that
advance the clock without sleeping, rollback, explicit cutoff overrun, an equal
start/end with an aware offset cutoff, unavailable-image partial interpretation,
and an invalid naive completion clock. These injected times are synthetic
regressions, not primary observations or historical acceptance.

`actual_review.json` is a fresh real physical replay of the original retained
three-capture plan. It records start 2026-10-18T14:13:03.840182Z and completion
14:13:03.991877Z. Source knowledge stays 04:25:43.865560Z and the original visual
measurement stays 04:29:39.896536Z. All original data fields and nine blockers
are exactly unchanged; only the interpretation timing, new start/cutoff fields
and resulting report digest differ. `actual_replay_verification.json` verifies
that every original image/OCR/plan/report/producer artifact remains byte exact.

The importable API and inputs are unchanged. Root can reuse
`evidence/batch10_identity_20261018/captured_review_plan.json` with its physical
default; an explicit earlier cutoff cannot make the newly interpreted output
available earlier. Annotation remains derived, without automated pixel/text
authentication. The blank Dated/number/shares/holder fields remain unknown;
the seal and facsimile signatures establish no action day or issued validity.

No HTTP, second image, remote OCR, collector API, database, quota, registry,
model, score, fitter, test budget or trading action occurred. Original source
clocks and bodies were not rewritten. Verified identity/adoptions and current
Q3 coverage stay zero; full analytical acceptance remains 0/21. Integration CI,
user Windows, historical availability and OOS remain separate work.
