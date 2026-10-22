The failed Windows run is preserved by the coordinator under
`evidence/batch7_windows_failure_20261015/`. Its original CRLF JSON/log and ZIP
were read and hashed. It executed all 1,833 cases and failed nine assertions
across three source-manifest fixture methods; it was not an acceptance.

The fixture constructed selected paths from the lexical `RUNNER~1` temporary
name while the unchanged service constructed paths from its resolved audit
root. The reparse mocks compared those different spellings, missed their
intended paths, and allowed the first ephemeral manifest publication. Later
checks then encountered CAS conflicts. The original test bytes are frozen here.
`reproduce_alias_failure.py` reproduces the same nine failures on POSIX using a
real non-reparse `root/../root` alias; this is not native Windows execution.

The correction resolves the fixture root before constructing any selected path.
All 106 original assertion calls and all 31 original test methods are preserved.
The three original mocked tests additionally assert that the intended canonical
path was reached. Production guards, budgets and source data are unchanged.

Two new methods exercise canonical mock targets after alias resolution and real
directory reparse rejection. POSIX uses actual symlinks; Windows uses actual
junctions, whose creation/rejection must succeed without a skipped test. Native
Windows short-name discovery is attempted there. If 8.3 names are unavailable,
the explicitly distinguished parent-roundtrip alias still exercises the guard;
this fallback does not establish native 8.3 availability.

`validate_fixture_fix.py` recorded 33/33 focused methods and 5/5 methods under an
injected distinct temporary-root spelling, with zero failures, errors or skips.
The latter includes the three formerly failing methods and both new methods.
The frozen pre-fix reproduction still records its original nine failures.
The earlier passing focused log before the optional short-lookup fallback is
retained separately as an intermediate diagnostic, not the final qualification.

This is offline fixture evidence. No HTTP, source collection, registry adoption,
database, real forecast, label generation or score change was performed. A new
hosted Windows run of the integrated commit is still required; no Windows
success or complete-specialist acceptance is claimed here. All new files should
be retained byte-for-byte with the original Windows failure evidence.
