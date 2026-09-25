# Průběh implementace pátracích agentů

Aktualizováno: 25. 9. 2026. Rozsah požadavku: SC-00 až SC-35 v `AGENT_SCOUT_IMPLEMENTATION_TASKS.md` na dokumentační větvi. Tento soubor sleduje první pracovní PR; `IMPLEMENTED` znamená existující kód, `TESTED` místní nebo CI test, `MERGED` teprve po spojení do main, `LIVE_VERIFIED` po běhu proti skutečnému zdroji.

| Body | Aktuální stav | Důkaz a zbývající práce |
|---|---|---|
| SC-00 | IMPLEMENTED, TESTED lokálně | Nullable `pd.NA` rank ukládá do SQLite NULL, skutečná transakce v testu. Počkat na CI a spojení PR. |
| SC-01 | IMPLEMENTED, TESTED lokálně | 53minutový běh: důkaz z počátku i konce je platný; opravdu budoucí údaj je odmítnut. Ještě integrovat živý Windows běh. |
| SC-02 | WAIT_MERGE | Dosud není v main ani otestováno na uživatelském Windows. |
| SC-03 | PARTIAL | Stávající CSV drží 687 řádků a pořadí; načtení produkčního souboru nyní kontroluje SHA-256 normalizovaného CSV. Audit přímého XLS zdroje a změnový diff ještě chybí. |
| SC-04–07 | PARTIAL | 39 profilů výzkumu je strojově čitelných, verzovaných a ověřených proti produkčnímu seznamu; `UNKNOWN` není chyba ani záporný bod. Profilové metriky zatím neřídí skóre/konektory. Výzkum uvádí `P`, ale skutečné CSV místo něj obsahuje `OKE`; P je jen ve zdroji, OKE zůstává bez profilu do ověření. SEC konektor odmítá cizí hosty/cesty i přesměrování, RSS ukládá jen kandidátní veřejné HTTPS odkazy. Úplné identity a pravidla všech budoucích poskytovatelů čekají. |
| SC-08 | IMPLEMENTED, TESTED lokálně | SQLite fronta, dedupe, lease token, zámek transakce, historie pokusů, restart; test končícího lease. Schéma eviduje verze 1–3; další změny musejí přidávat vlastní verze. |
| SC-09 | PARTIAL | Denní Windows plánovač a týdenní dávka, max. 100 firem/den, backlog po výpadku. Chybí profilový scheduling a fairness pro jiné providery. Windows instalace neověřena. |
| SC-10 | PARTIAL | SEC client používá limit a retry včetně `Retry-After`; dlouhý požadavek i HTTP 403 zastaví dávku a uloží cooldown poskytovatele do SQLite. Fronta odděluje zdroje a obnovuje vlastnictví SEC během dávky. Chybí obecný circuit breaker pro ostatní zdroje. |
| SC-11 | IMPLEMENTED, TESTED lokálně pro scout | `findings_as_of` a `ScoutIndexAgent` odmítají data nedostupná před cutoffem; pro každou orchestraci s nálezem ukládá neměnný cutoff a přesný seznam použitých finding IDs. Ostatní vrstvy analýzy mají vlastní existující snapshoty. |
| SC-12–16 | PARTIAL | SEC index i omezený primární dokument mají samostatný otisk, URL/CIK/accession kontrolu, skutečnou dobu pozorování a otevřenou otázku; 8-K parser vytváří citované sekce Item do hloubky 1. RSS zprávy už vstupují jako časově omezené `UNVERIFIED` stopy s pouhým ticker hintem a otázkou na primární zdroj, nikdy do skóre; rozpočet jsou dva bezpečné unikátní odkazy na ticker a běh. Stavový cyklus leadů má časovou historii; závěr `VERIFIED`/`CONTRADICTED` vyžaduje zvláštní claim-level ověřený důkaz. Další primární zdroje a automatické ověření tvrzení chybí. |
| SC-17 | TODO | Bez schváleného poskytovatele a rozpočtu; deterministická cesta běží bez AI. |
| SC-18 | PARTIAL | Nová lehká cesta na SEC index bez XBRL downloadu a omezený primární dokument nejnovějšího podání z každé rodiny formulářů; existující fundamentální agent čte filings/XBRL. Potřebuje úplné historické pokrytí a skutečné živé ověření. |
| SC-19–30 | TODO nebo stávající základ | Existují analytické agentní moduly a některé SEC/RSS vstupy. Pátrač přidal SEC index i primární dokument rodin Form 4 a SC 13D/G, ale nerozlišuje zatím nákup/prodej od vestingu; SC-21 proto zůstává částečný. Specifické konektory FINRA, USAspending, FDA, FDIC, FRED a další podle backlogu ještě hotové nejsou. |
| SC-31 | PARTIAL, TESTED lokálně | Index i primární dokument předá `ScoutIndexAgent` do `SourceResolutionAgent` a `QualityGateAgent` bez změny skóre; test celé orchestrace. Neměnný scout as-of bundle se seznamem použitých ID je uložen a lze jej znovu načíst; další specialisté čekají. |
| SC-32–33 | PARTIAL | Streamlit ukazuje poslední SEC index/dokument, oddělené neověřené RSS stopy, otevřené i uzavřené otázky s citovanými finding IDs, chyby zdroje a další pokus, stav fronty i tlačítko dávky; Excel má při použitých nálezech list `ScoutEvidence` s as-of cutoffem a citacemi. Windows .bat a volitelná denní úloha; instalátor jednou vyžádá kontaktní e-mail SEC. Chybí skutečné ověřené odpovědi a provozní Windows zkouška. |
| SC-34 | TODO | Žádné nové feature zatím nevstupují do predikčního skóre; vyhodnocení přínosu začne až s podklady a dokončenými výsledky. |
| SC-35 | PARTIAL | Místní testy, kompilace a zkouška plánování všech 687 bez přístupu k SEC. Chybí live known-positive SEC, Windows a end-to-end kontrola 48/687 tickerů. |

Nepoužívat tento PR jako potvrzení dokončení všech 36 úkolů. Bez reálného SEC User-Agent se automatický sběr vrátí `WAIT_ACCESS`, nepředstírá nalezené podání. Zdroje s klíčem/licencí se nezapojují bez odpovídajícího přístupu. Žádné order API není přidáno.

GitHub CI základní verze prošlo (`Market Checker test agents`, run 252). Nový SEC obsah a historie leadů procházejí místními testy a čekají na vlastní CI. Výzkumné `P` nesmí automaticky nahradit produkční `OKE` ani se bez ověřeného časového aliasu sloučit s `PSTG`.
