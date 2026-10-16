Actual AAPL and SPY Yahoo chart responses were captured on 14 October 2026
with two unauthenticated requests, no automatic redirects or retries. Original
raw bytes and receipt clocks are in `live_probe.json` and `raw/`.

Both HTTP200 responses contain 22 session timestamps, but the October 13 Close
is null. Normalization preserves the original price method and retains 21
usable closes through October 12. The newest exact target window stays missing:
`regularMarketPrice` is not substituted and its horizon is not shifted.

`reproduce.py` makes no HTTP requests. It replays the actual saved arrays with
the installed yfinance primary parsers, records current physical import/cache
clocks and validates immutable provider/cache content. Earlier replay versions
are preserved. The first replay's mistaken expectation of a complete newest
window is recorded in `initial_replay_assumption_failure.json`.

The separately declared complete earlier six-session window proves recorded
numeric/action inputs only. No historical forecast or prediction database is
created. Publisher revision timing remains UNKNOWN; strict PIT excludes both
roles with `MISSING_PRICE_REVISION_PROVENANCE`. An observed content change or a
cache extension is not a dated publisher correction.

The strict forward-label cache and genuinely annotated provider frames save
immutable versions. Unannotated legacy raw cache writes keep their existing
path and UNKNOWN provenance. Access-bearing whole inputs are withheld before
normalization or merge; no filtered body is exported around that gate.

Public interfaces remain compatible. `YahooCorporateActionHistoryClient` and
`YahooOhlcCacheStore` add optional `observation_clock` providers. Cache frames
carry `attrs['point_in_time_price_provenance']`; the label resolver preserves
content IDs, physical clocks and the exact requested action-input rows.

`handoff.json` records 84/84 focused tests, exact owned-file/artifact hashes and
the remaining gaps. The earlier full 1183 suite, unchanged 687/60-second gate,
Windows end-to-end, historical publisher versions and OOS acceptance are not
claimed by this lane. No scores, target formula, instrument registry, trading
behavior or canonical universe were changed.
