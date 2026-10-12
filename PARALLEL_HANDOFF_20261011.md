# Připravené paralelní změny — 11. října 2026

Šest samostatných implementačních oblastí je spojených v lokální větvi
`parallel/integration-safe-20261011`. Kompletní offline ověření prošlo:
**735 testů, 0 chyb, 0 selhání, 0 přeskočení**, Python 3.12.14, Linux.
Kód ověřeného celku má commit
`d10cc7d5fcfa05a4a6dfd7dd869afde47c262519`; důkaz společné kontroly je
`evidence/parallel_integration_20261011.json`. Následující commity pouze
zaznamenávají důkaz a tento předávací přehled.

| Oblast | Konkrétní výsledek |
| --- | --- |
| SEC registry | Hromadná příprava všech 687 pozic, immutable zdrojové cache, pokračování po ukončení procesu, samostatný posouzený export a ochrana všech produkčních registrů. |
| FDA / CMS / ClinicalTrials | Přesné datované vztahy ke konkrétní aplikaci, studii či poskytovateli; změna vztahu obnoví příslušnou kontrolu i zobrazené pokrytí. |
| OFAC | Zachování návazných poznámek a oddělených slabých aliasů; chybějící doplněk zůstává částečným výsledkem. |
| USAspending | Sdílený trvalý denní rozpočet, navazování stránek, zachování kladných nálezů při pozdější chybě a samostatné úplné/částečné pokrytí. |
| SEC Form 4 | Zachování emitenta, vlastníků, tříd, řádků, poznámek a revizí; využití uložených dokumentů i při vypnutém synchronním SEC. |
| Historické vyhodnocení | Samostatné tlačítko a JSON export, časově oddělená srovnání, skutečné počty archivních vlastností všech 21 specialistů. |

Stávající 13F registr má nadále 150 ověřených instrumentů a 537 nepokrytých
tickerů. Úplné oficiální vstupy globální SEC cache v této dokončovací dávce
nebyly staženy. Testy a fixture replay nepotvrzují živé pokrytí, skutečný
koncový běh na uživatelově Windows ani přijetí všech specialistů. Chybějící
licencované přístupy zůstávají otevřené. Skóre a obchodování se neaktivují.

## Připravené zveřejnění

Cíl: existující [PR 160](https://github.com/littleleg198602/JOHNY-SKORE/pull/160),
větev `fix/specialist-daily-capacity` veřejného repozitáře
`littleleg198602/JOHNY-SKORE`. Jeho dosavadní commit je
`e0ce88621ddfe79cc32b90fb488c052bfb20c7ae`. Je začleněný v lokální historii;
zveřejnění proto nevyžaduje force push. PR se tímto krokem neslučuje.

Automatická kontrola oprávnění push odmítla: pokyn pokračovat ve vývoji
nepovažuje za souhlas se zveřejněním kódu a důkazů do této veřejné větve.
Změny zůstávají v lokálních commitech a připraveném patchi. Po výslovném
souhlasu lze provést stejný push, aktualizovat popis PR a ověřit jeho CI.

Navržený název PR: **Make public specialist collection resumable and add parallel registry and history workflows**.

Navržený popis PR:

> Repeated bounded collector runs could revisit the same small identity scopes, lose useful work after a later page failure, or retain stale checks after registry changes. This change persists daily quotas and source checkpoints, distinguishes partial coverage, and refreshes checks against the dated identity scope.
>
> Six independently implemented lanes add global SEC candidate preparation with protected reviewed exports; dated FDA/CMS/ClinicalTrials object resolution; complete OFAC supplement provenance; resumable USAspending scans; durable Form 4 replay in the application; and explicit read-only historical diagnostics for all 21 specialists. The UI exposes quotas, partial coverage and a separate history report download.
>
> Validation: all 735 deterministic Linux tests passed with no errors, failures or skips; compile and diff checks passed. Individual lane and combined evidence are committed. The existing 150-instrument registry remains unchanged; global official-cache capture, applicable live coverage, actual user-PC Windows acceptance and sufficient source-attributable history remain open. No specialist is promoted to DONE/VERIFIED and no score or trading activation is added.
