# Hromadná příprava SEC 13F identit

Ruční přidávání pěti identit na commit nahrazuje nástroj pro přípravu celé
fronty **687 kanonických tickerů**. Seznam cenných papírů 13(f) a SEC katalog
tickerů se načtou jednou. Nástroj poté pracuje s uloženými zdrojovými soubory,
bez samostatného síťového dotazu pro každý ticker.

Jde o přípravu a validaci hromadného návrhu, nikoliv o dokončené rozšíření
produkčního registru. V této změně se žádná nová produkční identita nepřidala.

## Spuštění dvojklikem

Spusť `Pripravit_SEC_Registry.bat`. Použije stejný Python a uložený
`JOHNY_SKORE_SEC_USER_AGENT` jako existující pátrací agenti. Žádný další
kontakt se nezadává a adresu nástroj nevypisuje. Při prvním spuštění stáhne
dva oficiální SEC zdroje pro zvolený kvartál, výchozí je dosavadní **2026 Q2**.

Fronta se ukládá do `outputs/sec13f_identity_bulk/candidates.json` po
**každém tickeru**. Dávky mají standardně 100 položek a pokračují až do
dokončení všech 687. Opakované spuštění použije stejné zdroje a původní čas
pozorování a naváže na uložený index. Při změně zdrojů, rozsahu, původního
registru nebo času se checkpoint odmítne; pro novou revizi použij nový
checkpoint/workspace.

```bash
python -m market_checker_app.sec13f_identity_bulk prepare --capture-global
python -m market_checker_app.sec13f_identity_bulk prepare --one-batch --batch-size 100
```

Výsledek obsahuje `CANDIDATE_ONLY`, `scoring_allowed: false`, odlišuje
chybějící ticker/CIK, nejednoznačný katalog a kandidáty k posouzení. Přesná
shoda názvu i návrh z podobného zkráceného jména slouží k hledání podkladů.
Tickerový katalog nedokazuje historickou platnost tickeru ani jeho třídu.
GOOG a GOOGL proto zůstanou oddělené až do doložení konkrétní třídy.
Nástroj nemění produkční registr, skóre, uložené nálezy ani stavy specialistů.
Kanonický klíč `BRKB` zůstane ve výsledku; existující provider alias `BRK-B`
se použije pouze při přesném vyhledání SEC symbolu. Řádky CALL/PUT v seznamu
SEC mohou používat nestandardní kontrolní číslici: načtou se jako neakciové
řádky a nikdy nevstupují do kandidátů ani exportu. Ostatní CUSIP vyžadují
platnou kontrolní číslici.

## Podklady pro hromadné ověření

Každý nový záznam musí mít vedle kvartálního seznamu dva další podklady:

1. Surové SEC Schedule 13D/G XML s přesným `issuerCik`/`issuerCIK`,
   `issuerCusip`/`issuerCUSIP`, třídou a datem události. Použij skutečný
   `primary_doc.xml`, nikoliv vykreslenou cestu `xslSCHEDULE_...`.
2. SEC podání emitenta s inline XBRL, ve kterém `dei:TradingSymbol` a
   `dei:Security12bTitle` mají **stejný** `contextRef`; tento kontext musí
   obsahovat správný issuer CIK a datum. Samotné jméno firmy, manažerův
   13F řádek nebo současný katalog tickerů tento důkaz nenahrazuje.

Podklady stáhni příkazem `capture`, s jejich skutečným časem zveřejnění
z filing metadata SEC. Příkaz vypíše jejich `snapshot_id`:

```bash
python -m market_checker_app.sec13f_identity_bulk capture --kind schedule13 --url "https://www.sec.gov/Archives/edgar/data/.../primary_doc.xml" --published-at "2026-07-20T12:00:00+00:00"
python -m market_checker_app.sec13f_identity_bulk capture --kind ticker_filing --url "https://www.sec.gov/Archives/edgar/data/.../issuer-quarterly-report.htm" --published-at "2026-07-20T12:00:00+00:00"
```

URL a datum v příkladech jsou **zástupné hodnoty**, nepředstavují provedené
živé kontroly. Čas stažení nástroj zapíše sám a uchová původní bajty i SHA-256.
Dokument má limit 4 MB, jedna vybraná sada podkladů 100 MB a dávka nejvýše
1000 tickerů. Síťový přístup přebírá existující omezení tempa, timeout,
retries a ukončení při zákazu přístupu SEC.
Přerušení stahování zachová již dokončený zdroj; další `prepare --capture-global`
stáhne pouze chybějící globální vstup. Bez `--capture-global` je příprava čistě
z uložených zdrojů. Identické poslední časy odlišných katalogů se odmítnou
jako nejednoznačné. Pro takovou revizi použij nový workspace s výslovně
vybraným katalogem.

## Posouzení a export

Recenzent připraví JSON pole navržených záznamů. Pole registru mají stejný
formát jako `market_checker_app/data/verified_13f_securities.json`; navíc
každá položka obsahuje:

```json
{
  "review": {
    "approved": false,
    "reviewer": "",
    "reviewed_at": "SKUTECNY_CAS_POSOUZENI_S_TIMEZONE",
    "dated_binding_note": "Doklad platnosti presneho tickeru/CIK/CUSIP/tridy v omezenem intervalu; vcetne kontroly zmen emitenta.",
    "list_snapshot_id": "SHA256_ID_KVARTALNIHO_SEZNAMU",
    "instrument_snapshot_id": "SHA256_ID_SCHEDULE_13_XML",
    "ticker_snapshot_id": "SHA256_ID_PODANI_EMITENTA"
  }
}
```

Po zdrojové revizi nastav `approved: true`, skutečného recenzenta a čas.
`known_at` nesmí předcházet stažení žádného podkladu ani posouzení.
`effective_from` a `effective_to` musí ležet v kvartálu seznamu. Datum
události instrumentu i XBRL datum tickerové třídy musí ležet v tomto
intervalu. Rozsah platnosti stále výslovně posuzuje recenzent: jeden dokument
sám nedokazuje nepřerušenou platnost identity od začátku do konce kvartálu.

```bash
python -m market_checker_app.sec13f_identity_bulk export-reviewed --reviews "outputs/sec13f_identity_bulk/reviews.json"
```

Export odmítne chybnou třídu, nesprávný nebo znovu použitý CIK, více symbolů
v témže kontextu, překryvy platnosti, duplicitní import a přiřazení stejného
CUSIP více tickerům. CIK musí souhlasit s jediným emitentem pro daný symbol
ve vybraném katalogu; historické přejmenování nebo chybějící současný symbol
vyžaduje jiný samostatně posouzený postup. Inline fakta s pokračováním,
`xsi:nil`, `ix:exclude`, nepodporovaným namespace nebo konfliktní třídou
stejného tickeru v jiném kontextu se odmítnou. Nástroj neumí automaticky
posoudit ADR, preferenční akcie ani nepodporovanou třídu. Změny přesného
CIK/CUSIP/třídy vyžadují další datovanou revizi popsanou níže.

## Změny identity a corporate actions

Dosavadní loader kontroluje jednotlivé řádky a duplicity; hromadný postup
navíc kontroluje společné časové intervaly **před přípravou i před exportem**.
Překryv různých CIK, CUSIP nebo tříd stejného kanonického instrumentu se
odmítne i tehdy, když je chyba už ve výchozím registru. Alias `BRKB`/`BRK.B`
ji nemůže skrýt. Kompatibilní překrývající se verze se rovněž neimportují,
aby běh nepočítal stejné držení dvakrát.

Obnovení stejného CIK/CUSIP/třídy v dalším nepřekrývajícím se intervalu
potřebuje běžné nové podklady. **Změna identity** navíc potřebuje
`review.identity_transitions`. Posuzují se sousední verze v časovém pořadí,
nezávisle na pořadí vstupních položek. Při doplnění historie mezi dvě různé
identity musí být doloženy oba přechody, nejvýše dva na jednu položku.

Primární SEC oznámení archivuj se skutečným publication time jako
`capture --kind corporate_action_filing`. Podporováno je primární HTML
podání pod přesným předchozím nebo následujícím issuer CIK. Pouhá poznámka
recenzenta, tickerový katalog ani další Schedule 13 tento podklad nenahradí.

Každý přechod má následující tvar; všechny hodnoty níže jsou **zástupné**:

```json
{
  "from_identity_sha256": "SHA256_PRESNE_PREDCHOZI_VERZE",
  "to_identity_sha256": "SHA256_PRESNE_NASLEDUJICI_VERZE",
  "effective_on": "SKUTECNE_DATUM_YYYY-MM-DD",
  "source_snapshot_id": "SHA256_ID_PRIMARNIHO_OZNAMENI",
  "action_excerpt": "PRESNA_DATOVANA_PASAZ_VCETNE_OBOU_IDENTIT",
  "effective_date_text": "DATUM_PRESNE_JAKO_V_PASAZI",
  "from_identity": {
    "excerpt": "PRESNY_TEXT_PREDCHOZI_IDENTITA",
    "ticker_text": "PRESNY_SOURCE_TICKER",
    "issuer_cik_text": "PRESNY_SOURCE_CIK",
    "class_title": "PRESNA_SOURCE_TRIDA"
  },
  "to_identity": {
    "excerpt": "PRESNY_TEXT_NASLEDUJICI_IDENTITA",
    "ticker_text": "PRESNY_SOURCE_TICKER",
    "issuer_cik_text": "PRESNY_SOURCE_CIK",
    "class_title": "PRESNA_SOURCE_TRIDA"
  }
}
```

SHA-256 obou verzí spočítej funkcí `identity_version_sha256` v
`market_checker_app.services.sec13f_identity_bulk`; zahrnuje všechna původní
pole registru včetně intervalů, `known_at` a URL. `review` do něj nevstupuje.
Změna dřívějšího data nebo citace tak zneplatní přechod místo tichého
přepsání historie. Zdroj musí být stažen před posouzením; nová položka nesmí
odkazovat na verzi identity známou až po jejím vlastním `known_at`.

Pasáž má nejvýše 4000 znaků. Obě citace v ní musí obsahovat přesný ticker,
CIK, **CUSIP z odpovídající verze** a odpovídající normalizovanou třídu.
Text se porovná s archivovaným HTML po normalizaci whitespace a HTML
entit; metadata v `head`, skripty, `ix:hidden` a text s inline `display:none`
nepodporují důkaz. Oznámení bez všech přesných vazeb nebo nepodporované HTML
zůstává samostatným nedořešeným případem. Uvedené datum musí být obsažené
v pasáži, ležet po konci předchozího intervalu a nejpozději na začátku
následujícího intervalu. Případná mezera se automaticky nevyplní.

Shoda textu potvrzuje přítomnost dokladu v archivovaném podání. Recenzent
stále ověřuje, že pasáž skutečně popisuje účinnou změnu a správně rozlišuje
starou a novou identitu; samotný textový výskyt ani oznámený plán není
automatickým právním závěrem. Žádný takový živý přechod nebyl v této změně
nově doložen. Testy používají výslovně syntetické pasáže.

Vznikne `reviewed_registry_proposal.json` a `reviewed_registry_proposal.review.json`
s původními zdroji a posouzeními. **Produkční soubor se nepřepisuje.** Tento
ověřitelný návrh lze po revizi integrovat jednou dávkou a otestovat společně,
místo opakování celého workflow po pěti položkách.
Audit obsahuje SHA-256 přesných bajtů návrhu. Žádná hromadná mutace nesmí
zapisovat do celého produkčního adresáře `market_checker_app/data`, ani přes
symlink. Návrh ani audit nesmí přepsat review vstup, vlastní výchozí frontu
nebo archivované podklady; checkpoint nesmí přepsat registr nebo archiv.
Při vlastním `--workspace` u exportu zadej také `--output`, aby návrh skončil
ve stejném pracovním adresáři.
Audit obsahuje také SHA-256 výchozího registru, počet zachovaných verzí a
primárních přechodů. Všechny výchozí řádky včetně `known_at` a intervalů
zůstanou v návrhu beze změny. Změní-li se vstupní registr během kontroly,
žádný nový návrh se nezapíše a posouzení musí navázat na aktuální verze.

Deterministické testy zahrnují přípravu všech 687 tickerů, skutečné ukončení
CLI procesu uprostřed přípravy a následné navázání na uložený index. Používají
malé testovací zdroje. Oba úplné oficiální cache vstupy v tomto pracovním
prostředí nebyly k dispozici; úplný živý průchod SEC, nové identity, výsledek
na uživatelově Windows ani stav specialisty DONE se touto změnou nedokládá.

Oficiální formáty: [kvartální seznam SEC](https://www.sec.gov/divisions/investment/13flists.htm),
[SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[Schedule 13D/G XML specification](https://www.sec.gov/file/schedule-13d-13g-tech-specs-20).

## Ověření skutečného globálního cache — 14. října 2026

Oficiální kvartální TXT 2026q2 a aktuální SEC ticker katalog byly skutečně
načteny a hashově uloženy. TXT má 25 333 řádků a 1 030 opakovaných syntetických
opčních identifikátorů. Tyto opakované CALL/PUT řádky, někdy s rozdílným
addition/deletion příznakem, nejsou konfliktní akciové identity. Parser je
uchovává jako zdrojové řádky a nepřidává je do akciového indexu. Kolize
zahrnující akcii nadále odmítá v obou pořadích; kontrolní číslice akcií a
validace formátu zůstávají vynucené.

Původní parser kvůli těmto legitimním opčním řádkům celý živý vstup odmítl.
Po opravě proběhla skutečná příprava všech 687 slotů: 149 ALREADY_REVIEWED,
515 NEEDS_REVIEW, 21 WAIT_INSTRUMENT_EVIDENCE a 2 WAIT_TICKER_CIK (LEG, PSTG).
Je to úplná review fronta, nikoli 687 ověřených instrumentů.

Aktuální katalog přiřazuje XOM k CIK 0002115436, historický Q2 registr používá
CIK 0000034088. Primární 8-K původního emitenta potvrzuje redomiciliation
merger účinný 1. července 2026 a výměnu akcií jedna za jednu. Nový emitent
má samostatné primární podání. Historický Q2 záznam proto nebyl přepsán ani
nový katalog vydáván za důkaz identity v Q2. Nový interval, instrumentové
vazby a případné historické aliasy stále vyžadují úplný review postup.

Důkaz včetně hashů, příkladů opčních řádků a primárních XOM pasáží je v
`evidence/identity_specialist_audit_20261014.json`. Raw cache a restartovatelná
fronta jsou v `outputs/identity_specialist_20261014`. Specialistovi č. 1
zůstává stav PARTIAL/PENDING: širší dceřiné, značkové a produktové vazby,
LEG/PSTG, nové instrumentové intervaly, koncový Windows a historická
akceptace nejsou tímto krokem dokončené.

Další primární kontrola chybějících katalogových symbolů načetla původní
emitenty. LEG v podání z 26. srpna 2026 uvádí dokončení fúze, přechod pod
Somnigroup a požadavek na pozastavení/stažení obchodování na NYSE. Původní
CIK PSTG nyní uvádí Everpure a obchodovaný symbol P. Samotná současná krycí
stránka neprokazuje datum změny tickeru ani nepřerušenou platnost instrumentu;
statické aliasy, původní universe a historické registry proto nebyly změněny.
