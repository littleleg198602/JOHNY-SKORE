"""Replay genuine current XML/index with honest missing-packet provenance."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.services.sec_owner_instrument_scope_review import (
    OwnerInstrumentCapture, review_sec_owner_instrument_scope,
)

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
OLD=ROOT.parent/"batch6_sec_20261014"
SOURCE_PINS={
    "primary/nadella_current_submissions.json":(17733,"90b5bc75b82e4d027368b07e2adcdf829eb232d0d88034476e0910e164a1133d"),
    "primary/nadella_2026_form4.xml":(8214,"c71fd4237b551e70fd8054cfd54b9d101e63d689d0a26b10bb8bd123a0a4c810"),
    "primary/nadella_2026_form4_index.html":(8111,"828b5f5463be1a9d06e25ad0a19a5fa288e2ac756173e96835302bee16246a31"),
}
OLD_PIN=(5836,"9123343610f3b95c84a0be6d172fea451ccc45139ad1d9d88f5ad91a343a5053")
SELECTION={"issuer_cik":"0000789019","issuer_name":"MICROSOFT CORP","owner_cik":"0001513142","owner_name":"Nadella Satya",
    "accession":"0000789019-26-000157","form":"4","period_day":"2026-08-31","instrument_title":"Common Stock",
    "transaction_or_period_day":"2026-08-31","role":{"isDirector":"1","isOfficer":"1","isTenPercentOwner":"0","isOther":"0","officerTitle":"Chief Executive Officer"},
    "signature":{"name":"Julia Stark, Attorney-in-Fact for Satya Nadella","date":"2026-09-01"}}


def _capture(row,root,record_id,pin):
    if (row["size_bytes"],row["sha256"])!=pin: raise ValueError("Owner scope original metadata pin changed")
    body=(root/row["path"]).read_bytes()
    if len(body)!=pin[0] or hashlib.sha256(body).hexdigest()!=pin[1]: raise ValueError("Owner scope whole original byte hash changed")
    return OwnerInstrumentCapture(record_id,row["url"],body,pin[1],pin[0],datetime.fromisoformat(row["observed_at"]))


def load_inputs():
    plan_raw=(ROOT/"research_plan.json").read_bytes(); plan=json.loads(plan_raw)
    reserved=sorted(ROOT.glob("attempt-*-reserved.json"))
    if len(reserved)!=4 or plan["maximum_transports"]!=6: raise ValueError("Original bounded owner research attempt count changed")
    receipts=[]
    for sequence,path in enumerate(reserved,1):
        reservation=json.loads(path.read_bytes()); result=json.loads((ROOT/f"attempt-{sequence:02d}-result.json").read_bytes())
        if (any(result.get(key)!=value for key,value in reservation.items())
                or reservation["sequence"]!=sequence or reservation["research_plan_sha256"]!=hashlib.sha256(plan_raw).hexdigest()
                or reservation["maximum_original_body_bytes"]!=4000000 or reservation["absolute_timeout_seconds"]!=25
                or reservation["redirects_or_retries_allowed"] or reservation["application_collector_or_quota_db_used"]
                or not datetime.fromisoformat(plan["written_at"])<=datetime.fromisoformat(reservation["reserved_at"])<datetime.fromisoformat(result["observed_at"])):
            raise ValueError("Original reservation, plan or physical capture clocks changed")
        if result.get("complete_original_body_received"):
            if result["http_status"]!=200 or result["retention"]!="ORIGINAL_BYTES": raise ValueError("Original source is unavailable")
            _capture(result,ROOT,"pin-only-"+str(sequence),SOURCE_PINS[result["path"]])
        elif sequence!=2 or result["retention"]!="NOT_CAPTURED_COMPLETE_BODY" or result["received_prefix_bytes"]!=0 or result["failure_type"]!="URLError":
            raise ValueError("The genuine failed incomplete SGML transport changed")
        receipts.append(result)
    old=json.loads((OLD/"captures.json").read_bytes())["captures"][0]
    prior=_capture(old,OLD,"actual-prior-nadella-form3-2014",OLD_PIN)
    packet_receipt=receipts[1]
    packet=OwnerInstrumentCapture("actual-current-packet-not-captured",packet_receipt["url"],None,None,None,
        datetime.fromisoformat(packet_receipt["observed_at"]),retention="NOT_CAPTURED_COMPLETE_BODY")
    xml=_capture(receipts[2],ROOT,"actual-current-nadella-xml-2026",SOURCE_PINS[receipts[2]["path"]])
    index=_capture(receipts[3],ROOT,"actual-current-nadella-index-2026",SOURCE_PINS[receipts[3]["path"]])
    return prior,packet,xml,index,json.loads(json.dumps(SELECTION)),receipts


def build_review_artifact(*,replay_clocks=None):
    prior,packet,xml,index,selection,receipts=load_inputs()
    def review():
        return review_sec_owner_instrument_scope(prior_account_capture=prior,packet_capture=packet,
            xml_capture=xml,index_capture=index,selection=selection)
    if replay_clocks is None: result=review()
    else:
        with patch("market_checker_app.services.sec_owner_instrument_scope_review._physical_clock",
                   side_effect=[datetime.fromisoformat(value) for value in replay_clocks]): result=review()
    return {"schema_version":1,"result":"CURRENT_AS_FILED_ACCOUNT_ROLE_INSTRUMENT_FACTS_HEADER_AND_LEGAL_IDENTITY_WITHHELD",
        "actual_new_primary_research_transports":4,"successful_complete_http200_originals":3,
        "failed_original_transports":1,"remaining_unused_transport_slots":2,"maximum_transports":6,
        "original_receipts":receipts,"review":result,"original_failed_packet_hash_is_not_invented":True,
        "current_disclosure_is_separate_from_2014_and_reviewed_161":True,"underlying_current_form4_disclosures":1,
        "current_profile_is_latest_or_natural_person_identity_proof":False,
        "application_collector_live_or_quota_history_claim":False,"registry_or_score_change":False,
        "full_specialist_acceptance":False,"historical_oos_or_windows_claim":False}


def build_review_plan():
    prior,packet,xml,index,selection,_=load_inputs()
    captures={}
    for key,capture,path in (("prior_account",prior,"evidence/batch6_sec_20261014/primary/nadella_2014_form3.txt"),
        ("packet",packet,None),("xml",xml,"evidence/batch8_sec_20261015/primary/nadella_2026_form4.xml"),
        ("index",index,"evidence/batch8_sec_20261015/primary/nadella_2026_form4_index.html")):
        captures[key]={"metadata":capture.summary(),"body_available":path is not None,
            "body_ref":{"path":path,"sha256":capture.sha256,"bytes":capture.size_bytes} if path else None}
    return {"schema_version":"captured_identity_review_plan_v1","kind":"SEC_OWNER_INSTRUMENT_SCOPE",
            "request":{"selection":selection},"captures":captures}


if __name__=="__main__":
    for name,result in (("owner_instrument_scope_review.json",build_review_artifact()),("actual_review_plan.json",build_review_plan())):
        with (ROOT/name).open("x",encoding="utf-8",newline="\n") as stream: stream.write(json.dumps(result,indent=2,sort_keys=True)+"\n")
        print(name)
