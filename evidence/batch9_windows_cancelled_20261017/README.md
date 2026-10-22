# Původní neúplný Windows běh b0e866de

Run39072646508 skončil `cancelled`; Windows117513252470 je `cancelled`,
release gate117521376998 `failure`. ZIP12117165429 má přesně58491bytes
a SHA2567f0a915a75c3fa3a128e030a6923f535d7cea4e9a6817caba1de63a1a563edf9,
shodný se zveřejněným digestem. Obsahuje pouze původní `windows-tests.log`;
žádný výsledkový `windows-tests.json` neexistuje a nebyl domyšlen.

Původní log má394171bytes,1945 zahájených metod a1944 dokončených původních
`ok` bloků. Poslední metoda `test_sec_scout_service...687_tickers...` zůstala
rozpracovaná. Všech194 nových batch9 metod má původní `ok`, ale celá2231 sada
nebyla dokončena. Konečné failure/error/skip počty nejsou známé, nuly se netvrdí.
Chybí závěrečný plainOK footer. Není to úspěšná Windows akceptace.

Traceback `KeyboardInterrupt` a `The operation was canceled.` jsou pouze v
hosted console, jejíž původní decodedUTF8 bytes jsou zachované zvlášť. První
verifier chybně očekával traceback i uvnitř testlog memberu; jeho celé bytes,
offline reprodukce vadného očekávání a přesná kvalifikace jsou zachované v
`verification_diagnostics/`. Ověření bylo dokončeno nad již staženými původními
bytes bez dalšího HTTP; zip ani log se neměnily.

`other_jobs/` obsahuje původní logy tohoto stejného b0e běhu: Linux2231plainOK
435.234s, UI8plainOK15.472s a687scale5plainOK22.108s. Tyto úspěchy ani starší
b81 baseline nenahrazují neúplnou Windows sadu. `verify_originals.py` ověřuje
obsah offline; nemění důkazy, DB, zdroje, registry ani skóre.

Workflow měl Windows limit40minut. Čas ukončení odpovídá této hranici;
jde o úsudek z limitu a časů, nikoli o domyšlenou GitHub timeout anotaci.
Navazující oprava nastaví bounded CI prostor60minut při zachování všech testů,
jejich vlastních limitů včetně687/60s, archivace i přísné release gate.
Nový HEAD vyžaduje vlastní úplné obsahové hosted ověření.
