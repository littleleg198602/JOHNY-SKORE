# NEW ANALYZER — checkpoint 1. 10. 2026

Pracovní větev: `fix/specialist-daily-capacity`, PR #160.
Autoritativní inventář všech 21 specialistů:
`market_checker_app/data/specialist_status.json`. Dosud **0/21 DONE**.
Tento dokument je uložený plán pokračování, nikoli tvrzení, že běží
samostatní vývojoví agenti nebo že již proběhla úplná provozní akceptace.

## Co je doložené v tomto checkpointu

- FDIC skutečně používá v financials jiné názvy než právní názvy bank.
  JPM/BAC/WFC/C mají přesné CERT a datované citace příslušného API názvu;
  neznámý název, chybný CERT nebo datum vytvářejí částečný výsledek.
- NHTSA živý zdroj vrací `DD/MM/YYYY`. Opravený parser a kontrola
  odmítnutých řádků brání falešně úplnému nulovému výsledku.
- CMS: publikovaný dataset hospital-all-owners, přesné organizační
  názvy vlastníků, role, association date a procenta; osobní vlastníci
  nejsou ukládáni. Dotazy patří jen 19 profilům HEALTH_SERVICES.
- ClinicalTrials.gov: NCT, přesný lead sponsor/collaborator a jejich
  oddělené role, poslední zveřejněná aktualizace a fáze. Dotazy patří
  26 PHARMA a 23 MEDTECH profilům. Není tím doložen vztah studie k emitentovi.
- CMS/ClinicalTrials mají nejvýše 10 firem a dvě stránky po 100 na běh,
  časový rozpočet 60 sekund a zastavení po třech chybách. Již zahájený
  HTTP požadavek může doběhnout do svého 20sekundového timeoutu.
  Nedokončená firma zůstává splatná; dokončené dřívější kontroly se uchovají.
- OFAC: celý oficiální SDN CSV export jedním omezeným stažením; pouze
  primární názvy organizačních entit. Parser ověřuje strukturu a ID,
  přijímá pozorovaný koncový DOS EOF a ukládá otisk snapshotu. Oficiální
  export má ověřenou cestu přes konkrétní distribuční host; jiné redirecty
  jsou odmítnuté. Bez aliasů, Non-SDN a vlastnického look-through to není
  úplné sankční prověření firmy.
- Nové zdroje jsou zapojené do denního runneru, politik uložení,
  průběžných souhrnů a UI. Pokrytí CMS/ClinicalTrials má sektorový
  jmenovatel aktivních SEC identit; OFAC se obnovuje po dni.
- FDA/CMS/ClinicalTrials/OFAC jmenné výsledky zůstávají `UNVERIFIED`.
  Association date, historický dopis ani poslední aktualizace studie
  se nezaměňují za okamžik našeho prvního pozorování.
- Uložené živé případy: `evidence/specialist_live_20261001.json` (13),
  `evidence/healthcare_live_20261001.json` (4),
  `evidence/ofac_live_20261001.json` (2). Všech 19 případů PASS na zdejším
  Linux hostu; nejde o Windows nebo o všech 687 firem.

## Zbývající práce podle specialisty

| Specialista | Další konkrétní krok |
|---|---|
| Identita | Datované aliasy, dcery, značky a produkty; zapojit do cen i sektorových zdrojů. |
| SEC | Živý běh celé fronty 687 firem, historie oprav a Windows akceptace. |
| Insider | Živé typy Form 4 a datovaní reporting persons/instrumenty 13D/G. |
| Kontrakty | Další ticker–dcera–UEI, modifikace zakázek a aktuální živé důkazy; SAM přístup. |
| Regulace | Další vozidla, OFAC aliasy/Non-SDN/vlastnictví, DOJ a EPA. |
| Dodavatelé | Z citovaných právních názvů vytvořit datované entity a vztahy; koncentrace a živé dokumenty. |
| IR a zprávy | Ověřené firemní IR kanály a spojení RSS s primárním oznámením. |
| Ceny | Živá akceptace 687 po close opravě; BRKB/PSTG/LEG a corporate actions. |
| Short interest/borrow | FINRA účet a schema; datovaný instrument; licencovaný borrow pilot. |
| FDA/medicína | Produkt/application/sponsor–emitent a širší události; dnešní schema není úplný crosswalk. |
| Komodity | EIA živě s klíčem, USDA/USGS, firemní expozice a historická dostupnost hodnot. |
| Banky | Další relevantní bankovní dcery z 22 BANK profilů, skupiny se nesmějí zaměnit za jednotlivé CERT. |
| Zdravotní služby | CMS owner–dcera–emitent/provider, payer exposure a datované změny. |
| Energie/utilities | Vlastní aktiva a expozice propojit s primárními tržními/regulatorními zdroji. |
| Obrana/aerospace | Programy, dcery a recipient identity mimo jediný Sikorsky pilot. |
| Ostatní sektory | Připojit konkrétní poskytovatele k otázkám 39 profilů a změřit relevantní pokrytí. |
| Instituce | Více doložených CUSIP, 13F amendments a živý SEC ZIP. |
| Konsensus/options/Level 2 | Poskytovatel, entitlement a měřený licencovaný pilot. |
| Alternativní data | Vybraný licencovaný pilot a změřená přidaná hodnota. |
| Ověřování/missingness | Doložit hledání, usable/failed stavy a nezávislé primární potvrzení tvrzení. |
| Evaluace/audit | Skutečné coverage/outage/evidence a historické out-of-sample vyhodnocení před změnou skóre. |

Na zdejším hostu nejsou nastavené SEC kontaktní User-Agent, FINRA credential,
FRED ani EIA klíč. To nevypovídá o jejich přítomnosti na uživatelově Windows
nebo v GitHub secrets. Windows koncový běh, datové licence a skutečnou
historickou výkonnost nelze nahradit syntetickými testy.

Uživatel požádal o oznámení až po dokončení všech specialistů. Kontrola
dokončení je nastavená a vyžaduje kompletní inventář i skutečně existující
důkazy; sama nepředstavuje pokračující implementační proces.

Žádná část tohoto úkolu nepovoluje automatické zadávání obchodů.
