# Independent denominator basis challenge

The unmodified contracts candidate `ebe8679`, producer SHA-256
`280f13da28042d536ee7e306c041963d15ba337a5cffb32cddf142f7088b419f`,
has one confirmed overclaim: it treats matching issuer, annual period, context,
unit and displayed revenue facts, plus absence of selected change words, as
positive support for an identical concentration denominator basis.

The independent synthetic challenge retains those exact source tuples and full
byte pins. It adds this whole visible qualifier to the selected policy paragraph:

> The 2024 and 2023 net revenue totals in these statements exclude subsidiary sales that remain included in the customer concentration percentages.

The candidate still reports `SAME_ORIGINAL_SCOPE_PERIOD_AND_BASIS_SUPPORTED`
and `same_original_period_and_denominator_basis_supported=true` for all three
periods. This contradicts the explicit difference in source scope. Postposed
language saying the definition of revenue was revised, and saying prior-year
amounts were adjusted to conform to the current presentation, also evades the
existing change-word diagnostic. Broadening that word list would not establish
positive basis identity.

This finding is limited to basis support. Customer amounts, customer purchases,
physical quantity, current instrument/Q3 identity, legal continuity, historical
as-filed comparability and OOS flags correctly remain unverified. The peer does
not claim that any actual Skyworks filing contains the adversarial qualifiers.
All challenged bodies and injected review clocks are synthetic mechanism
fixtures, not live captures or historical availability evidence. No source GET,
database or registry operation occurred.

The frozen producer, owner tests, base concentration fixture and concentration
dependency remain in `producer_versions/`. `frozen_before_diagnostic.json`
contains all five original-version reports and exact request/source pins:
unchanged policy, three adverse qualifications and rounding-only control.
`before_peer_tests.log` preserves the real failing run: three methods executed,
three failure entries (one method and two subtests), zero errors/skips, and the
rounding control passed. No expected-failure decorator or weakened assertion is
used. This is observed failure evidence, not successful qualification.

The independent test requires the basis join to stay unproved for contradictory
or changed scope. It permits a rejected review or retained source quantities
with an unknown/withheld basis. Exact period/issuer/value facts may remain
supported. Source rounding must never produce a customer amount or historical
identity claim. The owner is responsible for the production correction; this
peer changes only its new test and evidence.

Run the peer test by loading its explicit file with `importlib` from the contracts
candidate root and setting `PYTHONPATH` to that root. The standalone
`reproduce_basis_scope.py --mode frozen --output <new-name.json>` reproduces the
preserved pre-fix producer; `--mode current` challenges the current owner version.
Outputs use exclusive creation. Neither path performs HTTP, database work or
candidate source writes. The owner fix and subsequent qualification belong to
separate immutable evidence, preserving this original diagnostic.
