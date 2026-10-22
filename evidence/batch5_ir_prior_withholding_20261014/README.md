On 2026-10-14 the current whole-body disclosure gate was applied to the retained
October 13 IR portal capture. It detected assigned access-value patterns. The
exact original authority body (286802 bytes; SHA-256
`deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1`)
was preserved privately outside every repository, with restricted filesystem
permissions and its full hash checked. Only that complete body was withdrawn
from the new working tree. No values were printed, no HTTP performed, and no
cleaned, encoded, compressed, empty or replacement body was published.

`withholding.json` pins its original capture URL, publication/header/capture
clocks, size and hash. It also pins the other three original raw-body hashes and
the unchanged historical `captures.json`, `source_review.json` and
`handoff.json`. The old source-only positives remain historical source-version
reports. They are not current acceptance after a required body is withdrawn.
Git history is not rewritten by this tree deletion.

The captured-review loader checks retention metadata before opening the known
authority pathname. It returns an `UnavailableIRDocument`, which has no body
attribute. It never reopens even an accidentally restored or replacement file.
The three safe originals still require their exact full-byte hashes and sizes.
Absent retention metadata also results in an explicit missing-document state;
it cannot recover source verification.

The runtime verifier checks unavailable required inputs before calling RSS or
HTML parsers. It returns `PARTIAL`, no quote or publisher-link proof, and no
issuer/instrument/scoring attribution. The original body hash stays metadata.
Unavailability has its own physical knowledge clock; a report cannot expose
that later review at the original October 13 cutoff. A separate RSS review can
be stored as `UNVERIFIED` at actual import time without altering the original
lead or provider quota/lease state.

`current_review.json` is the additive, reproducible diagnostic for the two safe
announcement captures and the unavailable historical portal control. All three
are now partial. Re-running the old `build_review` entry point writes only this
additive output and refuses to overwrite an existing report. It cannot write
the immutable historical source-review file.

The focused regressions cover body absence, no forbidden body reads/parser
calls, exact original hashes, accidental path restoration, missing metadata,
metadata/hash drift, immutable historical documents, physical availability and
retained strict positive synthetic contracts. No specialist, Windows,
historical/OOS, issuer or scoring acceptance is claimed.
