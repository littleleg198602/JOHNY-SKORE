Normal forecast paths previously had no explicit local captured-price provider:
even `--no-yahoo-metadata` still allowed price and twelve-benchmark HTTP. This
module supplies a Yahoo-shaped provider with no network, fallback, persistent
cache, database initialization, capture writes or forecast generation. The
coordinator owns all pipeline/config/CLI/UI wiring. Selecting this provider does
not enable prospective history and does not change a score or fitting formula.

The supported v1 input is a whole, content-addressed original
`yahoo_price_observation_v1` / `PROVIDER_RETURNED_FRAME` archive, provider
`yfinance`, interval `1d`, methodology `PROVIDER_VALUES_AS_RETURNED`, with
`auto_adjust_requested=false`. Original Open/High/Low/Close/Volume columns must
exist. Optional action columns must contain original finite nonnegative inputs;
`require_actions=true` additionally requires Stock Splits and Dividends. No
column, zero action, volume, price or missing session is synthesized. Merged or
adjusted cache variants are explicitly unsupported as raw normal inputs.

The exact manifest schema is:

```json
{"schema_version":"captured_market_data_manifest_v1","instruments":[]}
```

The empty list above is only a schema illustration and is not ready. Each row
has exactly `ticker`, `provider_symbol`, `provider`, `interval`, `methodology`,
`require_actions` and `source`. `source` contains `path`, `file_sha256`,
`file_bytes`, `capture_id`, `frame_sha256`, `kind`, `observed_at`, `captured_at`,
`available_at`, `first_session`, `last_session` and `row_count`. Source paths are
explicit relative paths within the configured archive root. Whole inputs are
gated before metadata projection. Source availability is its original physical
capture time, never the time the manifest is imported. Duplicate instruments,
unknown/foreign symbols, ambiguous versions, unsafe bodies, escaping/symlink
paths and mismatched bytes/hashes fail closed. Counts, rows and selected bytes
are bounded (699 instruments, 20,000 rows per archive, 64 MB selected total).

`build_captured_market_data_entry(archive_root=..., source_archive_path=...)`
describes one explicitly supplied safe archive without discovery or writes.
Describing an archive is not readiness approval. The sixteen `actual_plans`
files are real negative examples, with exact original pins; they are not
forecasts, labels or an accepted current input set.

```python
provider = CapturedMarketDataProvider(
    manifest_path=archive_root / "market_manifest.json",
    archive_root=archive_root,
)
preflight = provider.readiness(tickers, include_benchmarks=True)
# Only an explicit READY result permits the coordinator's normal run.
# This example does not run a pipeline or initialize a database.
```

The provider exposes `fetch_ohlc`, `fetch_ohlc_only`, `fetch_ohlc_batch`,
`fetch_snapshots` and `fetch_metadata`. Missing data return `None`/a local
`WAIT_DATA:<reason>` warning. Batch retry arguments cannot enable HTTP.
Metadata are explicitly missing: an empty `YahooSnapshot(..., {}, "missing")`
and `WAIT_DATA:CAPTURED_METADATA_NOT_SUPPLIED`. OHLC is not projected into
invented analyst, sector or current-price metadata. Performance uses the same
existing 7/14/21/63-row return formula on valid original Close values.

Every read rechecks whole manifest/source bytes and physical knowledge clocks.
Daily NYSE labels must be exact, ordered, unique, contiguous completed sessions;
UTC labels, consistent New York midnight labels and official NYSE-open labels
are supported without shifting their dates. All OHLCV inputs are finite and
consistent. At least 66 consecutive sessions must end at the current last
completed NYSE close. A read crossing a new close is assessed at completion.
There is no production historical/as_of override; the physical clock has a
monotonic high-water guard. Missing lookbacks beyond 66 remain reported.

READY frames have `attrs["captured_market_data_provenance"]`; its source pin is
exactly `readiness["rows"][i]["source"]`. `provenance_for(ticker)` revalidates
that same selected source. An instance never refreshes to a different valid
manifest/archive. `selection_provenance()` pins configuration without claiming
readiness. `revalidate_for_persistence(tickers, expected_readiness=preflight)`
rechecks assets plus all twelve benchmark sources, current freshness, exact
manifest SHA and the earlier ticker-to-source-pin map. A mismatch returns
WAIT_DATA before the coordinator persists predictions. Source clocks stay
unchanged when the physical read clock advances.

The original sixteen retained AAPL/SPY versions are read and checked anew in
`actual_readiness.json`. They remain WAIT_DATA with zero usable inputs. Their
short, old sessions and UNKNOWN publisher revisions are not current input,
strict PIT, historical or OOS evidence. No forecast or label is generated.
Positive 66-session cases and complete thirteen-instrument cases exist only in
explicitly marked synthetic unit fixtures. Production default-clock providers
reject any source marked `DETERMINISTIC_TEST_FIXTURE`/`fixture_only`; test clocks
are explicit and all such report/provenance objects remain `fixture_only=true`.
No synthetic positive is promoted into a genuine input. The previous feasibility report is
context; actual source pins, readiness and clocks are obtained from current
files. No old source or evidence is rewritten, and withheld bodies are neither
opened nor restored.

Qualification runs only this lane's focused suite, with network and SQLite
connections prohibited. It preserves the initial 31-test capture and records
current hashes plus all sixteen negative plans. It does not repeat the previous
full baseline, measure OOS performance or establish Windows acceptance.

```sh
python evidence/batch8_evaluation_20261015/reproduce.py \
  --output-directory /tmp/new-captured-provider-qualification
```
