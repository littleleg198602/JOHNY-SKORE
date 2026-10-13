# Kontrola zdravotnických identit

## Rozšíření 13. října 2026

První dávka rozšířila registr z původních 5 na 26 vazeb. Navazující společný
balík níže jej rozšiřuje na 519. Živý dotaz na CMS
přidal 18 přesných provider–owner vazeb HCA z první stránky 20 záznamů.
Kurzor `20` znamená pokračující stránkování; úplné pokrytí HCA není potvrzené.
Nové vazby platí jako pozorovaný snímek od 13. října, nikoli zpětně od data
asociace uvedeného CMS. Původní dva záznamy a jejich časy zůstaly zachované.

Živý FDA dotaz přidal dvě objektové vazby Lilly: BLA761306/lebrikizumab
a BLA761279/LY3074828. Přidal také přesný alternativní soubor dopisu
BLA761215/ORIGINAL2 pro Rezvoglar; jde o variantu dokumentu stejné události,
nikoli další produkt nebo nezávislé potvrzení. Všech pět vrácených FDA
dokumentů se přiřazuje pouze podle přesné aplikace, souboru a data dopisu.
Staré datum události není časem tehdejší znalosti: nové záznamy jsou známé
nejdříve od skutečného načtení 13. října 2026.

Důkaz a projekce primárních odpovědí jsou v
`evidence/healthcare_expansion_20261013.json`. Identity emitentů přebírají
již doložené SEC citace Lilly/HCA z původního registru; SEC filingy nebyly
znovu načteny. Rozšíření nepřidává nového emitenta, finanční dopad, změnu
skóre ani Windows/OOS akceptaci. Historické testy používají původní
pětizáznamový manifest jako pevnou fixture; nové testy kontrolují živé
objekty, datum znalosti, odmítnutí nesprávné identity a neúplnost CMS stránky.

Ve složce projektu dvojklikem spusťte `Zkontrolovat_Zdravotnicke_Identity.bat`.
Použije Python z existujícího prostředí aplikace, případně systémový Python.
Nespouští aplikaci ani internetové sběry. Report uloží do
`outputs/healthcare_coverage_latest.json`.

Čte existující `outputs/market_checker_history.db` bez zápisu a migrací.
Pokud databáze chybí, nezaloží ji: v reportu uvede `DATABASE_NOT_FOUND` a ukáže
samostatně doložené vazby ze zdravotnického registru. Použije soubor určený
proměnnou `JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE`, je-li nastavena. Jinak použije
`market_checker_app/data/verified_healthcare_identities.json`.

Pro vlastní cestu nebo čas můžete spustit:

```powershell
python -m market_checker_app.healthcare_coverage --db-path "outputs/market_checker_history.db" --output "outputs/healthcare_coverage_20261011.json" --as-of "2026-10-11T12:00:00+00:00"
```

Čas musí obsahovat časové pásmo. Výstup musí být samostatný JSON; zápis přes
databázi, nakonfigurovaný registr nebo produkční vstupy je odmítnut, včetně
odkazů na tyto soubory. Report se nahrazuje atomicky, takže chyba při přípravě
nezničí předchozí report.

Report používá přesný vstup 687 tickerů. Výzkumné oborové skupiny obsahují
111 kandidátů pro FDA, 19 pro nemocniční vlastnická data CMS a 49 pro současný
sběrač sponzorů ClinicalTrials. Oborová skupina sama nedokládá vztah firmy ke
konkrétnímu produktu, poskytovateli nebo klinické studii. FDA sběrač nadále
kontroluje všechny aktivní identity; tento report jeho běh nemění.

Pole `documented_issuer_object_subjects` počítá doložené datované vazby
v registru. `mapped_subjects` vyžaduje také odpovídající aktivní SEC identitu
známou ve zvoleném čase. `queried_issuers` a `current_complete_source_checks`
počítají uložené dotazy a dokončené kontroly současného rozsahu podle přesných
názvů. Dílčí stránky, změněný registr, zastaralý výsledek a chybná nebo budoucí
data jsou vykázána zvlášť. Prázdný dotaz podle názvu nedokládá úplnou absenci
událostí celé skupiny firem.

Historická vazba FDA nebo studie platí jen pro doložený objekt a datum.
CMS používá pozorovaný vlastnický snímek a zpětně nepotvrzuje řetězec
vlastnictví k původnímu datu asociace. Tento diagnostický export nepřidává
živé akceptační důkazy, historické vyhodnocení ani skutečný koncový běh na
uživatelském Windows.

## Společný CMS balík — 13. října 2026

Výchozí registr obsahuje 519 vazeb. Na předchozí dávku 26 záznamů navazuje
493 dalších CMS vazeb, při zachování původních záznamů a časů beze změny:

| Přesný dotaz vlastníka | Stránek | Vrácených řádků | Objektových vlastnických vazeb |
|---|---:|---:|---:|
| HCA HEALTHCARE INC | 3 | 254 | 254 |
| TENET HEALTHCARE CORPORATION | 1 | 87 | 87 |
| UNIVERSAL HEALTH SERVICES INC | 2 | 173 | 172 |

SEC ticker mapa a aktuální primární 8-K krycí stránky nezávisle potvrzují
CIK a obchodované symboly HCA, THC a UHS. Rozvinutý název Tenet je explicitní
na krycí stránce; není odvozen z podobnosti názvu. CMS role 43 u jednoho UHS
záznamu není role přímého/nepřímého vlastníka a nevstupuje do registru;
v původním capture a ScoutStore zůstává nepřiřazeným kandidátem.

Dokončené stránkování se vztahuje jen na tři konkrétní názvové dotazy.
Nedokazuje všechny dceřiné společnosti, celý relevantní sektor nebo
historický vlastnický řetězec. API klient nemá pinovaný neměnný snímek ani
garantované stabilní řazení mezi stránkami. Uložené scoped checks proto
zůstávají částečné, zejména kvůli dalším názvům emitentů; specialisté se
nepovyšují na DONE. Důkazy a projekce všech stránek jsou v
`evidence/cms_owner_bundle_20261013.json`. Kontakt použitý pro SEC požadavky
se do důkazů ani verzovaných souborů neukládá.

### Opakovatelné explicitní načtení CMS

Pro přípravu identit použijte z kořene projektu:

```bash
python -m market_checker_app.cms_owner_capture \
  --ticker HCA --owner-name "HCA HEALTHCARE INC" \
  --checkpoint-path outputs/cms_owner_captures/hca.json
```

Tento nástroj ukládá každou úspěšnou stránku a kurzor atomicky, čte jejich
obsahový hash a před I/O používá stávající sdílený SQLite budget a provider
lease. Chyba pozdější stránky zachová předchozí stránky. Jiný název nebo
velikost stránky vyžadují nový checkpoint; změnu rozsahu nelze použít k
obnovení denní kvóty. Jiný běh téhož subjektu smí pokračovat až další UTC
den. Dokončený checkpoint se přečte bez nových požadavků. Pro nový aktuální
snímek použijte nový datovaný soubor, v dalším povoleném dni; starý capture
se nepřepisuje. Výstup je pouze podklad k review identit, nikoli automatický
import, issuer-wide coverage nebo scoring.

CLI vrací 0 pro dokončené stránkování, 2 pro nedokončený/odložený běh a 1
pro chybu. Výchozí bounds jsou 100 řádků na stránku a 10 stránek na volání;
klient nevolá offset větší než 1000. Nástroj je explicitní přípravný postup;
běžný zdravotnický sběrač tím není automaticky nahrazen.
