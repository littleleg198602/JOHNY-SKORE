`issuer_names` now pairs EntityRegistrantName, EntityCentralIndexKey and the
date in the same inline-XBRL context. The generic context identifier may
belong to a parent in a legal-entity dimension and cannot replace the explicit
co-filer CIK. Dimension labels never supply a guessed CIK or company alias.

Legacy fallback is preserved for a single unambiguous undimensioned registrant
whose context CIK matches the filing archive. Competing names/CIKs, malformed
or duplicate facts, unknown/typed/multiple dimensions, namespace rebinding,
unsupported transformations and invalid/future date scopes fail closed. An
explicit CIK in another context is never moved into a name context. The
existing four-field output shape and source ordering are preserved.

The complete original DOW body is read in place from the regulatory archive,
verified against SHA256 `7df203af3404625494916fe8685535eb3548c6b4c1bc8a6c04a6c7ed7c291122`.
Its c-2 facts bind Dow Inc. to `0001751788`; c-1 binds The Dow Chemical Company
to `0000029915`, both at the declared December 31, 2025 context date. c-1's
generic context identifier remains `0001751788` with a LegalEntityAxis member.
The original binder incorrectly assigned that child name to the parent.
The exact original binder code is a diagnostic fixture, which makes this
regression replayable without unpublished local Git ancestry.

The index's Accepted display is `2026-02-03 07:53:43` with unknown timezone.
No UTC publication time is manufactured. The actual document/index capture
clocks remain October 14, 2026. The approved original research capture was
bounded at 6 MB; this evidence-only source view preserves its complete bytes.
The bulk application's 4 MB SourceSnapshot cap and HTML element budget remain
unchanged. No withheld body is read or recreated, and no body copy is added.

The same real bug was found in the retained XOM June30 10-Q: its c-2 explicit
CIK is `0002115436` for ExxonMobil Holdings Corporation while the generic
context identifier is `0000034088`. Correct current review removes that
incorrect name/CIK pair from the old-issuer role; Exxon Mobil Corporation's
c-1/`0000034088` pair remains. The exact frozen batch5 report is preserved as
historical evidence. Its three XOM blockers remain unchanged and the current
bridge is still PARTIAL. Declared context dates do not supply corporate-action
effective days or continuous legal/instrument history.

Initial focused diagnostics preserve one obsolete frozen-XOM equality failure
in 20 cases; broader focus preserves the same single failure in 50 cases,
with the other 49 passing. Root owns the exact version-aware assertion repair;
this lane does not modify older tests or artifacts. The final 20 new co-filer
regressions pass on the committed source version. Original LEG artifacts and
all production registries/score remain byte-identical. No new HTTP, verified
identity promotion, aliases, Windows/OOS or specialist DONE acceptance.

Replay: `python -m evidence.batch6_cofiler_20261014.replay_pairs`.
`paired_identity_review.json` preserves the actual wrong/correct DOW pairs;
`xom_review_qualification.json` records the exact older/current XOM difference
and source context pairs. `handoff.json` versions the now-historical LEG
producer hash and records current code/test/source/evidence hashes.
