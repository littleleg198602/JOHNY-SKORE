"""Offline synthetic issuer/logo versus competitor/symbol regression case."""
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from tests.test_batch8_symbol_action_scope import NOW, CLASS, fixture, html, replace_body
from market_checker_app.services.sec_symbol_action_scope_review import symbol_action_passages

HERE = Path(__file__).resolve().parent
ORIGINAL_PRODUCER = "8a24a55fdc647c9692d8ded0d255797f502584365831e20b134c980717d45dd7"


def case():
    request, captures = fixture()
    text = ("On 2026-04-01, Everpure, Inc. changed its logo. "
            "Different Company changed its ticker symbol from PSTG to P.")
    continuity = text+f" CUSIP 74624M102 remained unchanged for its {CLASS}."
    replace_body(captures,"first",html(f"<p>{text}</p><p>{continuity}</p>"))
    replace_body(captures,"second",html(f"<p>{text}</p>"))
    for index, claim in enumerate(request["action_claims"]):
        claim["selection"] = symbol_action_passages(captures[claim["source_key"]][1])[1 if index==2 else 0]
    return request,captures


def qualify(service, producer_sha256):
    request, captures = case()
    inputs = {"request":request,"captures":{key:{"metadata":metadata,"sha256":hashlib.sha256(body).hexdigest(),"bytes":len(body)}
                                           for key,(metadata,body) in captures.items()}}
    with patch.object(service,"_physical_clock",return_value=NOW):
        report=service.review_symbol_action_scope(request,captures)
    return {"schema_version":"synthetic_compound_actor_regression_v1","synthetic_not_live_acceptance":True,
            "http_requests":0,"producer_sha256":producer_sha256,
            "case_sha256":hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),
            "case":inputs,"report":report}


def main():
    producer=HERE/"producer_versions/pre_clause_guard_service.py"
    if hashlib.sha256(producer.read_bytes()).hexdigest()!=ORIGINAL_PRODUCER:
        raise ValueError("Frozen pre-fix producer changed")
    spec=importlib.util.spec_from_file_location("pre_clause_symbol_scope",producer)
    service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    result=qualify(service,ORIGINAL_PRODUCER)
    with (HERE/"peer_before_fix.json").open("x",encoding="utf-8") as stream:
        stream.write(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"case_sha256":result["case_sha256"],"status":result["report"]["status"],
                      "effective_on":result["report"]["effective_on"],"blockers":result["report"]["blockers"]}))


if __name__=="__main__":main()
