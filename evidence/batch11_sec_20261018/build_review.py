"""Immutable offline temporal replay of retained proxy and original Form4 bytes."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from evidence.batch10_sec_20261018.build_review import (
    BODY_PATHS, PINS, SELECTION, load_inputs as _retained_inputs,
)
from market_checker_app.services.sec_person_role_temporal_review import review_sec_person_role_temporal

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SERVICE = "market_checker_app/services/sec_person_role_temporal_review.py"
ROLE_SERVICE = "market_checker_app/collectors/sec_person_role_claim_review.py"
TEMPORAL_REQUEST = {"role_title_literal":"Chairman and Chief Executive Officer",
    "transaction_kind":"nonDerivativeTransaction","transaction_ordinal":1,
    "requested_transaction_day":"2026-08-31"}


def pin(path):
    raw = path.read_bytes()
    return {"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def load_inputs():
    captures,selection,receipts = _retained_inputs()
    return captures,selection,dict(TEMPORAL_REQUEST),receipts


def build_review_artifact(*, replay_clocks=None):
    captures,selection,request,receipts = load_inputs()
    def review():
        return review_sec_person_role_temporal(primary_capture=captures["primary"],index_capture=captures["index"],
            account_packet_capture=captures["account_packet"],selection=selection,temporal_request=request)
    if replay_clocks is None:
        result = review()
    else:
        with patch("market_checker_app.services.sec_person_role_temporal_review._physical_clock",
                   side_effect=[datetime.fromisoformat(value) for value in replay_clocks["temporal"]]),\
             patch("market_checker_app.collectors.sec_person_role_claim_review._physical_clock",
                   side_effect=[datetime.fromisoformat(value) for value in replay_clocks["source"]]):
            result = review()
    return {"schema_version":1,"result":"CURRENT_TRANSACTION_AFTER_PROXY_ANCHOR_ROLE_AT_DAY_UNVERIFIED",
        "actual_new_http_requests":0,"new_source_or_api_collection":False,"DB_quota_lease_mutations":0,
        "retained_whole_originals":3,"named_person_disclosures":1,"comparison_account_disclosures":1,
        "original_capture_receipts":receipts,"source_service_pin":pin(REPO/SERVICE),
        "source_role_dependency_pin":pin(REPO/ROLE_SERVICE),"producer_pin":pin(Path(__file__)),
        "review":result,"new_verified_adoptions":0,"full_specialist_acceptance":False,
        "windows_or_historical_OOS_claim":False,
        "limits":["Literal YEAR bounds are calendar precision envelopes, not effective appointment or cessation dates",
            "Proxy present is anchored only to 2025-10-21 and cannot extend to the 2026-08-31 transaction",
            "The exact original XML transaction date is a declaration, not trade execution or natural-person identity",
            "The Form4 reporting-owner role is as-filed scope, not continuous legal role at the transaction day",
            "Named person/account-CIK/approved alias and independent exact role interval evidence are absent",
            "The 110-byte publisher index/served-proxy conflict and UNKNOWN publication timezones remain",
            "A newly ingested interpretation is available at physical completion, never at an older source/event day"]}


def build_review_plan():
    captures,selection,request,_ = load_inputs()
    return {"schema_version":"captured_identity_review_plan_v1","kind":"SEC_PERSON_ROLE_TEMPORAL",
        "request":{"selection":selection,"temporal_request":request},
        "captures":{key:{"metadata":capture.summary(),"body_available":True,
            "body_ref":{"path":BODY_PATHS[key],"sha256":capture.sha256,"bytes":capture.size_bytes}}
            for key,capture in captures.items()}}


if __name__=="__main__":
    for name,report in (("person_role_temporal_review_v1.json",build_review_artifact()),
                        ("actual_review_plan.json",build_review_plan())):
        with (ROOT/name).open("x",encoding="utf-8",newline="\n") as stream:
            stream.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
        print(name)
