# Independent prime-page lineage challenge

This peer is based on the exact committed contracts producer
`cc23d43adc44839a16d33464a7d8bc56a3700e8d`, source SHA-256
`2a05f8cc86bff37236102c1231cfb2a962f7203f0af4f460dfa29ba77c24722f`.
It did not test an evolving producer or change any production file. Four new
focused methods passed: 4 discovered/executed, plain OK in 0.010 seconds, all
failure/error/skip/expected/unexpected counts zero. Existing 34/35-method suites
and the full repository suite were not repeated.

The fresh adversarial scope is small and explicit:

- A valid two-page control has separate whole manifest/body pins, exact page-local
  last-row cursors, sequential capture clocks and stable total2. It is a traversal
  control, never complete query/history or legal/issuer evidence.
- A whole page2 response is placed under page1's manifest. Its new body/hash/length
  pins are internally consistent, but its original page identity conflicts with
  the manifest request. Both historical34 and current35 reject it.
- Identical or different arbitrary snapshot labels on separate pages cannot
  prove snapshot semantics, a complete query, full revision history or absence.
  A terminal empty LIVE-shaped control still proves no genuine negative or
  selected-award absence.
- Page2's cursor cannot borrow page1's last-row internal ID. The committed35
  producer rejects this mismatch while retaining its valid control behavior.

All generated inputs, including LIVE-shaped branch controls, are in-memory
synthetic mechanism fixtures. They are not captured HTTP responses or evidence
of any real award. A hash validates supplied bytes; this peer does not authenticate
the manifest as publisher-signed or prove an original durable collection ledger.

The known cursor chronology is preserved explicitly. The owner independently
added and qualified its 35th method/guard at 2026-10-18T19:33:44.006644Z, before
this peer executed on UTC19. The earlier saved34 qualification stays historical.
The owner's inactive exact34 reconstruction matches its earlier source/test
hashes and is stored separately. This peer only loads that inactive version for
comparison; the active35 source was never reverted. The peer did not cause or
claim credit for the owner's earlier cursor correction.

`version_comparison.json` contains full reports for the valid control, hash-valid
wrong-page splice and known cross-page cursor case on historical34/current35.
`candidate_snapshot.json` pins both versions and the owner's chronology.
`peer_tests.log/json` pin the current35 independent execution and unchanged
producer. Frozen copies of the exact source/test versions are under
`producer_versions/`. No new failure was observed in these selected current35
cases. This is a narrow result, not a general correctness or completeness claim.

The existing owner `actual_current_review.json` was read without rerunning it.
`retained_case_content_verification.json` confirms WAIT, unknown original query,
null original unique-award count and no promotion of an embedded legacy report
to original manifest/body pages, a genuine negative or complete history. It is
a serialized-content check, not a new primary capture or independent replay.

There were zero HTTP/source/collector calls, SQL/database/quota operations,
registry adoptions, source-body copies or model/fitter/score/trading changes.
No withheld body was read, restored or substituted. All earlier evidence and
production source bytes remain unchanged. Canonical687, currentQ3 coverage0 and
full analytical acceptance0/21 remain unchanged. Real original prime pages,
durable collection history, prior UEI/legal relationships and analytical
acceptance remain separate missing work.
