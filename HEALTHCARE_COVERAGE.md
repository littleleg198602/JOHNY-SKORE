# Kontrola zdravotnických identit

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
