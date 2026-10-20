"""Add the concrete qualified source frontier, preserving older version sections."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    proof_path = ROOT / "evidence/sourcefront_integration_20261020.json"
    proof = json.loads(proof_path.read_bytes())
    run = proof["local_full_suite"]
    assert run["successful"] and run["tests_run"] == run["discovered_tests"] == 2767
    marker = "## Zdrojové pokračování: konečné listiny, oznámení a skutečný CMS rozsah — 20. 10. 2026"
    text = f'''{marker}

Čtyři nové celé primární HTTP200 originály (3 SEC + 1 DOJ, **3,449,563 bytes**)
jsou v `evidence/sourcefront_20261020/`. Každý měl samostatnou rezervaci před
jediným požadavkem; žádný retry, redirect, app collector nebo nový quotaDB.
Šest skutečných izolovaných agentů dokončilo zachovanou práci po přerušení
limitem využití. Jejich commity, přesné piny a původní diagnostiky jsou v
`evidence/sourcefront_coordinator_20261020/worker_commits.json` a lane handoffech.

- LEG konečná listina povoluje 10,000 common shares s par $0.001 pro přeživší
  soukromou společnost; podpis 2026-08-26 není doložená účinnost ani návaznost
  starého CUSIP. Direct/indirect control conflict zůstává otevřený.
- XOM successor listina povoluje 9 miliard common shares s par $0.001,
  účinnost i podpis 2026-07-01. Neprokazuje nový CUSIP, outstanding shares ani
  vysvětlení rozporu proti July7 cover s no-par textem.
- Nový původní Form144 potvrzuje účet/issuer CIK a společný den nabytí.
  Jeho 86,525/Common se liší od Form4 178,622/70,465.844/Common Stock;
  žádná shoda transakce, fyzická osoba, alias, kontinuální role či execution.
- DOJ původní First Amended Complaint má Doc51, filed 2024-06-11 a plný
  case stamp 2:24-cv-04055-JXN-LDW. Čtyři vybrané stránky včetně paragraph19
  jsou podpořené jako ALLEGATIONS_ONLY; separate Order/current docket/CIK,
  kontinuita a merits zůstávají neověřené.
- Runtime CMS již nevydává starou pětifieldovou projekci OBSERVED_SNAPSHOT za
  ověřenou issuer atribuci bez whole owner/provider originals a CCN/PAC vazby,
  ani ve stejný den. Query leads zůstávají. Nezměněné zdrojové řádky se znovu
  nevytvářejí kvůli změně dne v diagnostice. Předchozí skutečné chyby, peer
  assertions a tři opravené starší test contracts jsou obsahově zachované.
- Samostatný retained SWKS Note11 source-only packet dokládá refundable
  foundry deposits/prepayments 26.4m USD k 2025-10-03 versus 167.8m k
  2024-09-27. Není to named foundry, physical volume, purchases ani závazek;
  Note14 channel partition nepřiděluje Apple částku.

Čtyři plány jsou dostupné v existujícím CLI/UI. Osm skutečných akcí má
immutable fyzické hodiny v `evidence/sourcefront_coordinator_20261020/runtime/`,
0 HTTP/SQL/adopcí/pipeline runs. Source research není native userWindows,
genuine forecast, aktuální kompletní negativní dotaz ani historická/OOS akceptace.

Místní společná sada: **2767 discovered=executed, plain OK, {run['plain_OK_seconds']:.3f}s**;
všechny failure/error/skip/expected/unexpected počty0. Testovaný code snapshot
`{proof['tested_code_snapshot']}`; všech79 nových exact module/class/method
má původní ok blok v přesných hranicích testu. Celé JSON/console/separate runner
a immutable pre-hosted-CI proof jsou `evidence/sourcefront_linux*_20261020.*`
a `evidence/sourcefront_integration_20261020.json`. Nový public HEAD potřebuje
vlastní obsahově ověřené Linux/UI/687/Windows original ZIP+JSON/log/digest a
release gate; pozdější výsledek patří do PR162 a skutečného artefaktu.

Všech2224 starších public evidencefiles/101,339,193bytes,208 z209 starších
testfiles a223 z230 productionfiles zůstává byte-identical; přesně7 production
a1 testfile má schválenou změnu. Sedm protected inputs, statusy všech21,
workflow/687/60s/default modelconfig/fitter/skóre jsou stejné. Původní DB/journal
se neotevírá, nehashuje, nekopíruje ani nepřehrává; aktuální counts jsou
null/UNOBSERVED. Čtyři celé withheld access-bearing bodies zůstávají absent.
Canonical687/Q2instrument150/currentQ3=0/healthcare10/NHTSA3/legal5/adopt0,
**fullacceptance0/21**. Žádný merge, automatické obchodování ani completion notice.

### Konkrétní další fronta

- identity_bulk: nezávislá dated ClassA/CUSIP/symbol/action vazba; LEG control
  chain a XOM July7/newCUSIP. Authorized charter class není veřejný instrument.
- healthcare: legitimní CMS whole originals/CCN/PAC/name a durable history;
  přesná Ireland legal/applicant/product dated endpoints a širší clinical scope.
- regulatory: separate original Order, současný úplný docket/disposition a
  nezávislá dated financial identity; whole FRS/program body. NHTSA approval
  block zůstává, žádný retry/alternate transport/bypass/new quotaDB.
- contracts: whole prime request/pages a původní durable history/earlier UEI;
  explicitní denominator basis a physical supplier quantities.
- sec: natural-person/account/legal alias, datovaný role interval/instrument;
  current publisher IR authority. Form144 proposed sale není execution.
- evaluation: genuine66-session market originals/revisions a skutečný timely
  saved forecast/native userWindows/later original labels/broader history/OOS.
  Původní failed market plan zůstává consumed; žádný retry/backdate/slide/activation.

<!-- Starší oddíly níže zůstávají kvalifikací jejich vlastních verzí. -->

'''
    updates = []
    for name in ("SCOUT_PROGRESS.md", "SPECIALIST_COMPLETION.md",
                 "SPECIALIST_IMPLEMENTATION_CHECKPOINT_20261001.md", "PARALLEL_SPECIALIST_WORK.md"):
        path = ROOT / name
        before = path.read_bytes()
        assert marker.encode() not in before
        first, rest = before.split(b"\n", 1)
        after = first + b"\n\n" + text.encode() + rest
        path.write_bytes(after)
        updates.append({"path": name, "before_sha256": hashlib.sha256(before).hexdigest(),
                        "after_sha256": hashlib.sha256(after).hexdigest(), "inserted_bytes": len(after) - len(before),
                        "original_header_and_all_remaining_bytes_preserved": True})
    target = ROOT / "evidence/sourcefront_coordinator_20261020/documentation_updates.json"
    target.write_text(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(),
                                 "updates": updates}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"updated_documents": len(updates)}))
