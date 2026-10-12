# EPA ECHO: datovaná vlastnická vazba zařízení

EPA ECHO identifikuje zařízení pomocí FRS ID. Název zařízení, regulační
status ani souhrn pokut nedokazuje, že veřejně obchodovaný emitent toto
zařízení vlastnil. Nový resolver proto přijímá samostatný, ručně prověřený
vstup. Žádná produkční vlastnická vazba nebyla touto změnou přidána.

## Zapojení

```python
from pathlib import Path
from market_checker_app.services.regulatory_identity_service import RegulatoryIdentityRegistry

# Bez doloženého souboru zůstávají všechny vlastnické vztahy nerozřešené.
identities = RegulatoryIdentityRegistry()
# Pouze pro skutečně prověřený soubor:
# identities = RegulatoryIdentityRegistry.load(Path(configured_manifest_path))
service = EpaEchoScoutService(store, client=client, identities=identities)
result = service.run(as_of=clock, universe=applicable_canonical_tickers)
versions = identities.identity_versions(store, as_of=clock)
coverage = store.specialist_coverage(
    "epa", as_of=clock, subjects=applicable_canonical_tickers,
    identity_versions=versions,
)
```

Nová verze scope platí i při prázdném vstupu. Koordinátor musí předat stejné
`identity_versions` výpočtu pokrytí a akceptace, jinak porovnává kontroly jiného
scope. Změna registru neobchází trvalou denní kvótu.

## Formát prověřeného vstupu

Kořen JSON je `{"schema_version": 1, "records": []}`. `load()` odmítne soubor
nad 5 MB, nesprávné schéma, opakovaný `record_id` nebo poškozené záznamy.
Prázdný seznam je platný a neposkytuje důkaz vlastnictví.

| Pole záznamu | Požadavek |
|---|---|
| `record_id`, `ticker`, `issuer_cik` | Jedinečný záznam, přesný ticker a desetimístný SEC CIK. |
| `source`, `kind` | `epa`, `epa_frs_facility_owner`. |
| `frs_registry_id`, `facility_name` | Přesné dvanáctimístné FRS ID a doložený název zařízení. |
| `owner_entity_name`, `relationship` | Doložený vlastník; `DIRECT_OWNER` nebo `INDIRECT_OWNER`, nikdy pouhý provozovatel. |
| `time_basis` | `OBSERVED_SNAPSHOT`: vlastnická vazba při pozorování, bez převzetí dřívějších právních účinků. |
| `effective_from`, `effective_to` | Data `YYYY-MM-DD`; začátek včetně, konec výlučně, `null` pro konec bez doloženého data. |
| `known_at` | Čas první dostupné znalosti s explicitním časovým pásmem. |
| `verification_status` | `VERIFIED` nebo `UNVERIFIED`. Druhá možnost podporuje pouze hledání kandidátů. |
| `reviewer`, `reviewed_at` | Povinné pro `VERIFIED`; revize po pořízení důkazů a nejpozději v `known_at`. |
| `evidence` | Nejvýše deset položek s `purpose`, veřejnou primární HTTPS `url`, přesným `locator`, konkrétním `supports` a `observed_at`. |

Prověřený záznam potřebuje všechny tři účely důkazů: `FACILITY_IDENTITY`
(EPA a přesné FRS ID v lokátoru), `OWNERSHIP_RELATIONSHIP` (EPA nebo archiv
SEC vlastního CIK emitenta), `ISSUER_IDENTITY` (archiv SEC vlastního CIK).
Revizor musí skutečně přečíst obsah, doložit vazbu konkrétního zařízení na
vlastníka a případný řetězec k emitentovi. Validátor kontroluje strukturu,
domény, CIK a čas znalosti; samotná oficiální URL není akceptačním důkazem.

## Chování běhu

- Dotazy zahrnují jméno emitenta a nejvýše čtyři další doložená jména zařízení;
  celkem nejvýše pět jmen na emitenta. Trvalé limity EPA zůstávají deset
  emitentů a deset HTTP požadavků denně. Těla odpovědí mají limit 2 MB a
  nejvýše 100 řádků. Běh zachovává vlastní časový a chybový limit.
- Vlastnictví se rozřeší pouze přes přesné FRS ID, doložený název, ticker,
  CIK, znalost a účinnou dobu. Více aktivních vazeb stejného FRS ID je
  nejednoznačných i tehdy, když se liší názvy nebo jeden vztah není prověřený.
- Nově známá vazba, počátek či konec účinnosti a konkurující vlastník změní
  scope dotčeného emitenta. Pořadí záznamů, heartbeat SEC ani cizí zařízení
  scope nemění. Staré kontroly se zachovají v auditu a nový scope je znovu due.
- Neúplná odpověď, chybné nebo duplicitní FRS řádky, neprovedené dotazy a
  chybějící prověřená zařízení dávají `PARTIAL`. Již získané nálezy se uloží
  i při přerušení dalšího dotazu. Neúspěšný dotaz bez jediné použitelné
  odpovědi nevytváří kompletní negativní kontrolu.
- Úplný nulový výsledek bez registru znamená pouze nulový výsledek omezených
  dotazů na jména. Netvrdí úplné skupinové pokrytí ani nepřítomnost
  environmentální odpovědnosti. Čerstvá částečná kontrola zůstává `PARTIAL`
  i při opakování před jejím dalším termínem obnovy.

Nález s prověřenou vlastnickou vazbou má
`identity_status=DATED_FACILITY_OWNERSHIP_VERIFIED`, přiložený datovaný
záznam a `issuer_facility_relationship_verified=true`. Samotný nález nadále
používá `verification_status=UNVERIFIED`. Konkrétní regulační akce zařízení
se nepřevádí na odsouzení či pokutu emitenta: příslušné právní a skupinové
příznaky i `scoring_applied` zůstávají `false`. Souhrnné pole pokut zařízení
není použito k emitentovu skóre.

## Důkazy a zbývající práce

`tests/test_regulatory_identity_service.py` obsahuje výslovně syntetické
vlastnické vztahy. Testy nedokazují žádnou živou identitu. Historický záznam
`evidence/epa_echo_live_20261002.json` dokládá pouze facility-name smoke a
FRS ID; neobsahuje doložené vlastnictví a nová implementace z něj vlastnictví
nevytváří. Zůstává pořízení a obsahová revize skutečných datovaných vazeb,
relevantní pozitivní a negativní živé případy, pokrytí kanonického scope,
koncový běh na Windows a historické vyhodnocení. Regulační specialista se
proto neoznačuje DONE ani live VERIFIED.

Primární popis FRS a rozsahu facility statistik:
[EPA All Media Programs Search Results Help](https://echo.epa.gov/help/facility-search/all-data-search-results-help).
Rozhraní sběru:
[EPA ECHO Web Services](https://echo.epa.gov/tools/web-services).
