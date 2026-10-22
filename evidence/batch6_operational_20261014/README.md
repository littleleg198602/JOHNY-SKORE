# Literal operational metric review

One actual quantitative candidate was reviewed offline from the complete,
safe original DOW 2025 combined 10-K captured by the regulatory lane. Its
5,279,652 original bytes have SHA256
`7df203af3404625494916fe8685535eb3548c6b4c1bc8a6c04a6c7ed7c291122`.
This lane made zero HTTP requests and copied no raw source file. The existing
repository-relative body/metadata references and exact pins are in `selection.json`.

The literal passage is: "Collectively, the Company operates 91 manufacturing
sites in 29 countries." Only the stated **91 manufacturing sites** is selected;
no exposure percentage or market/revenue implication is calculated.

The DEI anchor is exclusively context `c-2`: **Dow Inc. / 0001751788**,
with its explicit January 1–December 31, 2025 fiscal context. Context `c-1`
separately reports **The Dow Chemical Company / 0000029915** and a legal-entity
dimension despite the context entity identifier being 0001751788. These are
never flattened into one issuer. The separate pinned scope passage defines
Dow/Company as the combined consolidated report. The metric scope is therefore
`COMBINED_REPORT_GROUP`; individual issuer quantity attribution remains withheld.
The report period is not an invented measurement date for the operating count.

The API is:

```python
review_sec_operational_metric(
    body: bytes, *, capture_metadata, expected_body_sha256,
    selection, as_of=None,
) -> dict
```

Selection requires the exact issuer context/name/CIK, fiscal period end,
metric kind/value/unit and original plain-text metric/scope byte spans plus
SHA256 hashes. It supports literal manufacturing-site, employee and production-well
counts, without inferred relationships or scaling. Results are `CANDIDATE_REVIEWED`
or `REJECTED`, with fixed rejection reasons. No database, registry, runtime,
prediction, score, activation or trading state is written.

Body reads are bounded to 8,000,000 bytes; metadata/selection/passage/XML structure
also have explicit bounds. Hashes, lengths, HTTP success, complete original
capture metadata, source authority, capture clocks, DEI namespaces/contexts,
periods and literal operator/value/unit/scope all validate. Cross-context joins,
unresolved segments/scenarios, duplicate facts, forward-looking or financial
passages, hidden/comment/script text, nonliteral values and unsafe whole bodies
fail closed. Whole-body withholding precedes parser access, and no cleaned,
wrapped or encoded substitute is accepted or exported.

Original capture was `2026-10-14T13:28:22.735778+00:00`; physical review ingestion
and knowledge have their own actual clocks in `review.json`. `as_of=None`
uses the same physical review clock. An explicit cutoff at original capture
rejects today's newly ingested review. Publisher publication time and historical
availability remain unknown. Current instrument/Q3 identity, asset ownership,
individual issuer attribution, Windows/OOS evidence and specialist completion
are not verified.

`review.json` contains the real candidate and four actual same-source rejection
controls: cross-context CIK pairing, Chemical dimension review, individual-issuer
scope relabeling and the older capture cutoff. Original body and metadata were
verified unchanged. Reproduction after regulatory files are integrated:

```text
python -m evidence.batch6_operational_20261014.replay_review
```

An explicit `--source-root` can point to the same original repository files.
`--output` creates a new file exclusively and refuses to overwrite a report.
41/41 focused checks passed, including 21 new generated mechanism cases and
the existing qualitative exposure/provenance regressions. Generated fixtures
are not LIVE or historical acceptance evidence. Existing prime recovery proofs
and prior qualitative XOM findings remain unchanged.
