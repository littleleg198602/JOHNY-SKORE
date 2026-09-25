# Průběh implementace pátracích agentů

Aktualizováno: 25. 9. 2026. Rozsah požadavku: SC-00 až SC-35 v `AGENT_SCOUT_IMPLEMENTATION_TASKS.md` na dokumentační větvi. Tento soubor sleduje první pracovní PR; `IMPLEMENTED` znamená existující kód, `TESTED` místní nebo CI test, `MERGED` teprve po spojení do main, `LIVE_VERIFIED` po běhu proti skutečnému zdroji.

| Body | Aktuální stav | Důkaz a zbývající práce |
|---|---|---|
| SC-00 | IMPLEMENTED, TESTED lokálně | Nullable `pd.NA` rank ukládá do SQLite NULL, skutečná transakce v testu. Počkat na CI a spojení PR. |
| SC-01 | IMPLEMENTED, TESTED lokálně | 53minutový běh: důkaz z počátku i konce je platný; opravdu budoucí údaj je odmítnut. Ještě integrovat živý Windows běh. |
| SC-02 | WAIT_MERGE | Dosud není v main ani otestováno na uživatelském Windows. |
| SC-03 | PARTIAL | Stávající CSV drží 687 řádků a pořadí; načtení produkčního souboru nyní kontroluje SHA-256 normalizovaného CSV. Audit přímého XLS zdroje a změnový diff ještě chybí. |
| SC-04–07 | TODO/PARTIAL | SEC nález má zdroj a časy, ale úplné identity, 39 profilů a source-policy kontrakt čekají. |
| SC-08 | IMPLEMENTED, TESTED lokálně | SQLite fronta, dedupe, lease token, zámek transakce, historie pokusů, restart; test končícího lease. Chybí verzované migrace pro případ dalších změn. |
| SC-09 | PARTIAL | Denní Windows plánovač a týdenní dávka, max. 100 firem/den, backlog po výpadku. Chybí profilový scheduling a fairness pro jiné providery. Windows instalace neověřena. |
| SC-10 | PARTIAL | SEC client používá limit a retry; databázový zámek zabrání souběžným dávkám SEC ze dvou procesů. Chybí přesné `Retry-After` a obecný circuit breaker. |
| SC-11 | PARTIAL | `findings_as_of` a `ScoutIndexAgent` odmítají data nedostupná před cutoffem; test. Pro celou analytickou identitu ještě archivovat přesný seznam finding IDs v snapshotu. |
| SC-12–16 | PARTIAL | SEC index umí vytvořit nález a otázku, deduplikuje ji, drží hloubku nejvýše 2. Zatím nestahuje celé podání a sám neřeší navazující otázku ani další zdroje. |
| SC-17 | TODO | Bez schváleného poskytovatele a rozpočtu; deterministická cesta běží bez AI. |
| SC-18 | PARTIAL | Nová lehká cesta na SEC index bez XBRL downloadu; existující fundamentální agent už čte filings/XBRL. Potřebuje kompletní coverage, historical submissions a skutečné živé ověření. |
| SC-19–30 | TODO nebo stávající základ | Existují analytické agentní moduly a některé SEC/RSS vstupy. Specifické nové konektory FINRA, USAspending, FDA, FDIC, FRED a další podle backlogu ještě hotové nejsou. |
| SC-31 | PARTIAL, TESTED lokálně | Indexové SEC záznamy předá `ScoutIndexAgent` do `SourceResolutionAgent` a `QualityGateAgent` bez změny skóre; test celé orchestrace. Chybí extrakce obsahu a detailní as-of bundle. |
| SC-32–33 | PARTIAL | Streamlit ukazuje poslední podání, stav fronty a tlačítko dávky; Windows .bat a volitelná denní úloha. Chybí detail leadů a výpadků u každé firmy. |
| SC-34 | TODO | Žádné nové feature zatím nevstupují do predikčního skóre; vyhodnocení přínosu začne až s podklady a dokončenými výsledky. |
| SC-35 | PARTIAL | Místní testy, kompilace a zkouška plánování všech 687 bez přístupu k SEC. Chybí live known-positive SEC, Windows a end-to-end kontrola 48/687 tickerů. |

Nepoužívat tento PR jako potvrzení dokončení všech 36 úkolů. Bez reálného SEC User-Agent se automatický sběr vrátí `WAIT_ACCESS`, nepředstírá nalezené podání. Zdroje s klíčem/licencí se nezapojují bez odpovídajícího přístupu. Žádné order API není přidáno.
