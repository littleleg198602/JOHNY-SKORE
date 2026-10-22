# Skutečný Windows neúspěch osmé dávky

Tento adresář zachovává původní neúspěšnou kvalifikaci veřejného HEAD
`df6648b4cd4d856629eed5cd5db1321e53da3bed` / tree
`bd99dca493a4d6eca5d6c1fb6c9017995aedf292`. Run `38861160925`
skončil failure; Windows job `116843520437` a release gate `116855563520`
neprošly. Linux `116843520514` má obsahově ověřený2036 plain OK/219.838s;
UI `116843520638` a původní687/60s scale `116843520162` prošly.

Windows nejprve dokončil63 focused checkpoint testů/29.937s. Úplná sada
měla2036 discovered=executed/2241.765s,1failure,0errors/skips/expected/unexpected,
successful=false. JSON začíná2026-10-16T06:27:32.936762+00:00 a končí
2026-10-16T07:04:57.400085+00:00; Windows-2025Server-10.0.26100-SP0,
Python3.12.10. Log skutečně končí `FAILED (failures=1)`.

Selhala pouze metoda
`test_explicit_paths_refuse_escape_missing_and_parent_symlink_without_discovery`.
Mock testovacího parent symlinku nezasáhl canonical cestu provideru; místo
požadovaného `HISTORY_SYMLINK_REFUSED` se vrátil syntetický66-row fixture frame.
Původní traceback a závěrečný blok jsou beze změny zachované vedle úplného logu.
Samotný tento výsledek neprokazuje změnu produkčního guardu ani specialistickou
akceptaci. Oprava a její vlastní kvalifikace mají samostatné důkazy.

Skutečný ZIP artifact `12030449825`/62471bytes byl stažen a jeho SHA256
`bafec854986b27ab5e67f358842be7bf486afb4f04dfe568f111b00e21a9c394`
přesně odpovídá zveřejněnému digestu. Oba původní ZIP members byly skutečně
přečtené a zapsané jako původní bajty, včetně CRLF:

- JSON376bytes SHA256 `4fe3c913c1e70cf62816b84cfcbe6659034e0847921a7451903a331ed61d3a6b`.
- Testlog412149bytes SHA256 `b8532df1158a73ae0a4bad6d74303de880c842eef47a5f3fce184844e2a4bb32`.
- Decoded UTF-8 hosted Windows joblog SHA256 `27a081043cf2844177ab285727ee4041468e953ec67520b03fff86580af0f4d6`.
- Decoded UTF-8 hosted Linux joblog SHA256 `2ad9c929430fd9954bdf7cb22b64e06f3d919c248f80282eec837442927b737f`.

`content_verification.json` obsahuje kontrolu memberů, počtů, tracebacku,
digestu a skutečného failure footeru. `github_run_jobs_artifacts.json` zachovává
čerstvá metadata konkrétního run/head/jobs/artifact. `verify_original_artifact.py`
je skript kontroly původních bajtů; dočasná download URL se neukládá.
Původní `evidence/batch8_integration_20261016.json` a localJSON/console se
nepřepisují a nejsou úspěšnou Windows kvalifikací. Úplná akceptace zůstává0/21.
