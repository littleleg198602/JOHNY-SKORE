# Regular NHTSA client redirect guard

The regular recall client previously used the default urllib opener, which could
follow a redirect before validating the final URL. That second transport operation
had no separate shared quota reservation. The local `module.urlopen` transport seam
now builds an opener with a redirect handler that refuses automatic redirects.
It initializes only when collection actually requests a response. Existing mock
targets, exact HTTP200/URL/content-type/byte/JSON checks, monthly scope keys and
source matching stay intact; no old test assertions or captured data changed.

`offline_probe.json` uses real urllib redirect/error processing and a synthetic
HTTPS handler that completely replaces socket I/O. Both HTTP302 and HTTP307
default-policy simulations perform two transport operations. The guarded regular
collector performs one operation and records one failed shared reservation, no
finding and no complete check. Same-day retries make zero further operations and
retain the charge. The synthetic envelopes are mechanism diagnostics, not live
publisher acceptance or genuine complete-negative evidence.

Four new regressions also cover redirects to the same exact official query and
another HTTPS host, preservation of a prior valid finding/check when the next
model redirects, and exhaustion of eight shared reservations before an
unattempted make can begin transport. `tests.log` records 52 passing focused
NHTSA tests: four new redirect regressions and 48 existing source/runtime/report
regressions. No existing transport patch target needed modification.

This output performed zero real HTTP requests. The canonical ledger was opened
read-only for the proof and remained at eight requests and three make subjects;
all live collection had already exhausted that day. Temporary databases contain
only synthetic mechanism tests. No source budget, schema, registry, score or
trading policy expanded.

The first and read-only handoffs/logs/captures remain byte-identical. Their source
hashes and results describe their historical versions. `handoff.json` identifies
the superseded regular-client source hash and the new guarded source hash. Earlier
coordinator full-suite results are diagnostics of their pre-fix version, not
acceptance of this new source version. Root owns final combined and Windows
checks. Genuine live complete-negative, wider relevant coverage, historical OOS
and specialist completion remain unverified by this output.
