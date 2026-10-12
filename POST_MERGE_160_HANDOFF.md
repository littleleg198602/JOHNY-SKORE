# Pokračování po sloučení PR 160 — 12. října 2026

PR [160](https://github.com/littleleg198602/JOHNY-SKORE/pull/160) je skutečně
sloučený. GitHub API uvádí `merged_at=2026-10-12T07:04:58Z`, tedy 09:04:58
v Europe/Budapest. Aktuální `main` je
`9995fa2a5a37201076d8607774c98887337ec8b2`. Sloučený HEAD PR je
`e0ce88621ddfe79cc32b90fb488c052bfb20c7ae`; jejich stromy jsou totožné.

Obě pozdější místní dávky agentů proto **nejsou součástí sloučeného PR**.
Jsou zachovány až po commit `f9d9f50eef94a02e842101a86df4623db9371a6b`.
Na novém `main` byla založena místní větev
`continuation/specialists-after-160-20261012` a obě dávky byly spojeny bez
konfliktů commitem `1d12b45a8783aa1fbad5441f4650e1d0f88aa907`.
Výsledný strom před tímto předávacím dokumentem je totožný s předchozí
integrační větví. Nic se nezahodilo ani znovu neimplementovalo.

## Kontroly a jejich hranice

- Program, testy, workflow a launchery jsou totožné s ověřeným kódem
  `2c8c4b685a63a3c23b91b49d6a4a32705bddd002`.
- Dosavadních **899 deterministických testů na Linuxu** prošlo bez chyb,
  selhání a přeskočení. Dnes byl obsahově ověřen původní JSON důkaz a SHA-256
  úplného logu; nepředstírá se nový testový běh.
- `git diff --check 9995fa2 HEAD` prošel. Před přidáním tohoto dokumentu
  obsahoval rozdíl vůči `main` 101 souborů, 17 784 přidaných a 457 odebraných
  řádků.
- Při načtení kontrol nového `main` byly Linux, UI a 687 tickerů úspěšné;
  hostovaná kontrola Windows ještě běžela. Jsou to kontroly sloučené starší
  verze, nikoli obou nezveřejněných dávek.
- Nové živé pokrytí, koncový uživatelský Windows a historická OOS akceptace
  se tímto krokem nepotvrzují. Inventář stále nemá žádného DONE specialistu.

## Konkrétní připravená publikace

Cíl je **nový pull request do `main` veřejného repozitáře
`littleleg198602/JOHNY-SKORE`**. PR 160 je uzavřený sloučením a nebude se
znovu otevírat. Zveřejněný obsah by zahrnoval obě již připravené dávky,
jejich kód, testy, primární zdrojové podklady, ověřovací důkazy a tento
předávací dokument. Obsah změn je popsán v
`PARALLEL_HANDOFF_20261011.md`, `PARALLEL_HANDOFF_SECOND_BATCH_20261011.md`
a `PARALLEL_SPECIALIST_WORK.md`.

Navržený název:

**Make specialist scans resumable and add dated identity and history workflows**

Navržený popis nového PR:

> Repeated bounded source runs could revisit the same subjects, lose saved
> work after a later failure or keep stale checks after identity changes.
> The collectors now preserve daily budgets and page checkpoints, refresh
> checks against dated identities and expose partial coverage in the app.
>
> Two integrated development batches add protected bulk SEC identity
> preparation, dated healthcare and EPA relationships, OFAC supplement
> provenance, durable Form 4 replay, USAspending revision diagnostics,
> independent SEC counterparty resolution and explicit source archive and
> historical evaluation exports. Windows launchers expose the diagnostics
> without requiring command-line entry.
>
> Validation: the unchanged Python, test and workflow tree has a verified
> 899-test deterministic Linux result with no errors, failures or skips.
> Integration onto merged PR 160 preserved the prior tree without conflicts;
> diff checks passed. Hosted CI of this new branch is pending publication.
> The SEC instrument registry remains 150/687; broader applicable live
> coverage, user-PC Windows and source-attributable historical acceptance
> remain open. No specialist is promoted to DONE and scores are unchanged.

## Zbývající blokace

Dřívější automatická kontrola odmítla zveřejnění kódu a důkazů do veřejného
repozitáře: pokyn pokračovat a následné „jo“ nepřijala jako uznaný souhlas
se zveřejněním. Samotné uživatelovo sloučení staršího PR nepotvrzuje
zveřejnění pozdějších dávek. Tento krok neprovedl žádný nový push ani zápis
do GitHubu a nepoužil jiný kanál k obejití odmítnutí.

Dřívější standardní HTTPS push měl navíc chybu chybějícího přihlášení;
to je samostatný technický problém, nikoli důkaz chybějícího propojení
GitHub aplikace. Po uznaném souhlasu bude třeba ověřit dostupný zápis,
zveřejnit připravenou větev, otevřít nový PR a ověřit jeho Linux i Windows
CI. Do té doby automatické pokračování ze vzdáleného `main` nesmí označovat
obě místní dávky za zveřejněné ani je znovu implementovat.

### Nový pokus po převzetí práce, 12. října

Po uživatelově odpovědi „No ted jsem to udelal ale necham to yase na tobe“
byl z commitu `b46ebad13a442f260fe1d1ea6c79023795e2138b` zadán standardní
push pouze do nové větve `continuation/specialists-after-160-20261012`.
Automatická kontrola ho znovu odmítla s odůvodněním, že odpověď je
nejednoznačná a výslovně neschvaluje přesné zveřejnění dosud neveřejného
kódu a důkazů do veřejného repozitáře. Push nebyl nahrazen jiným zápisovým
nástrojem. Nový PR stále neexistuje.

Místní obsahová kontrola tohoto commitu zjistila 102 změněných souborů
(včetně tohoto předávacího dokumentu), dohromady 2 080 409 bajtů jejich
úplného obsahu. Kontrola hledala řetězce GitHub tokenů, soukromé klíče a
dlouhé dosazené hodnoty přihlašovacích polí; nenašla žádnou shodu.
Jde o omezenou kontrolu vzorů, nikoli záruku absence všech citlivých dat.
Program, testy, workflow a launchery zůstávají beze změny proti ověřené
899-testové verzi; SHA-256 původního úplného testového logu znovu souhlasí.

Automatické pokračování bylo při kontrole skutečně vypnuté. Jeho pokyn
se opravuje na sloučený PR 160 a navazující nezveřejněné dávky; zapnutí se
nepředstírá. Po uznaném souhlasu je další krok zveřejnění připravené větve,
nový PR, jeho CI a navázání dalších specialistů z uložené fronty.
