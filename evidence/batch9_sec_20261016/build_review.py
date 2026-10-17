"""Reproduce the new whole-packet diagnostic without fetching or rewriting evidence."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from market_checker_app.services.sec_complete_packet_review import (
    CompletePacketCapture, review_sec_complete_packet,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PRIOR = ROOT.parent / "batch8_sec_20261015"
SERVICE = "market_checker_app/services/sec_complete_packet_review.py"
PACKET_PIN = (9665, "d2facdeb8d8935143f3b5fbc53fbe0140a28e69e5bd7915913d4ef573a769ec8")
XML_PIN = (8214, "c71fd4237b551e70fd8054cfd54b9d101e63d689d0a26b10bb8bd123a0a4c810")
INDEX_PIN = (8111, "828b5f5463be1a9d06e25ad0a19a5fa288e2ac756173e96835302bee16246a31")
OLD_FAILURE_SHA256 = "a5e3dce538938e342451f3e4288db9bec754cac334688c52c86301fb5ec6090b"
SELECTION = {
    "accession": "0000789019-26-000157", "form": "4",
    "issuer_cik": "0000789019", "issuer_name": "MICROSOFT CORP",
    "owner_cik": "0001513142", "owner_name": "Nadella Satya",
    "primary_filename": "form4.xml", "period_day": "2026-08-31",
}


def pin(path):
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _capture(row, root, record_id, expected):
    if (row["size_bytes"], row["sha256"]) != expected:
        raise ValueError("Original complete-packet capture metadata changed")
    path = root / row["path"]
    if path.stat().st_size != expected[0]:
        raise ValueError("Original complete-packet capture size changed")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected[1]:
        raise ValueError("Original complete-packet whole-body hash changed")
    return CompletePacketCapture(record_id, row["url"], raw, expected[1], expected[0],
                                 datetime.fromisoformat(row["observed_at"]))


def load_inputs():
    plan_raw = (ROOT / "research_plan.json").read_bytes()
    plan = json.loads(plan_raw)
    reservations = sorted(ROOT.glob("attempt-*-reserved.json"))
    if len(reservations) != 1 or plan["maximum_current_packet_diagnostic_attempts"] != 1 or plan["maximum_transports"] != 4:
        raise ValueError("New diagnostic is bounded to exactly one independently reserved packet GET")
    reserved = json.loads(reservations[0].read_bytes())
    receipt = json.loads((ROOT / "attempt-01-result.json").read_bytes())
    if (any(receipt.get(key) != value for key, value in reserved.items())
            or reserved["sequence"] != 1
            or reserved["url"] != plan["allowed_exact_url"]
            or reserved["research_plan_sha256"] != hashlib.sha256(plan_raw).hexdigest()
            or reserved["maximum_original_body_bytes"] != 4_000_000
            or reserved["absolute_timeout_seconds"] != 25
            or reserved["redirects_or_retries_allowed"]
            or reserved["application_collector_or_quota_db_used"]
            or not datetime.fromisoformat(plan["written_at"]) <= datetime.fromisoformat(reserved["reserved_at"]) < datetime.fromisoformat(receipt["observed_at"])
            or receipt["http_status"] != 200 or receipt["retention"] != "ORIGINAL_BYTES"
            or not receipt["complete_original_body_received"]
            or receipt["elapsed_seconds"] > 25):
        raise ValueError("New reserved original diagnostic receipt/clocks changed")
    old_path = PRIOR / "attempt-02-result.json"
    old_raw = old_path.read_bytes()
    old = json.loads(old_raw)
    if (hashlib.sha256(old_raw).hexdigest() != OLD_FAILURE_SHA256
            or plan["old_failed_transport_sha256"] != OLD_FAILURE_SHA256
            or old["url"] != receipt["url"] or old["failure_type"] != "URLError"
            or old["retention"] != "NOT_CAPTURED_COMPLETE_BODY" or old["received_prefix_bytes"] != 0
            or "sha256" in old or "size_bytes" in old
            or not datetime.fromisoformat(old["observed_at"]) < datetime.fromisoformat(reserved["reserved_at"])):
        raise ValueError("Earlier incomplete packet transport must remain unchanged and distinct")
    xml_row = json.loads((PRIOR / "attempt-03-result.json").read_bytes())
    index_row = json.loads((PRIOR / "attempt-04-result.json").read_bytes())
    packet = _capture(receipt, ROOT, "actual-whole-current-nadella-packet-20261016", PACKET_PIN)
    xml = _capture(xml_row, PRIOR, "retained-current-nadella-xml-20261015", XML_PIN)
    index = _capture(index_row, PRIOR, "retained-current-nadella-index-20261015", INDEX_PIN)
    return packet, xml, index, dict(SELECTION), receipt, old


def build_review_artifact(*, replay_clocks=None):
    packet, xml, index, selection, receipt, old = load_inputs()
    def review():
        return review_sec_complete_packet(packet_capture=packet, xml_capture=xml,
                                          index_capture=index, selection=selection)
    if replay_clocks is None:
        result = review()
    else:
        with patch("market_checker_app.services.sec_complete_packet_review._physical_clock",
                   side_effect=[datetime.fromisoformat(value) for value in replay_clocks]):
            result = review()
    return {
        "schema_version": 1,
        "result": "COMPLETE_ORIGINAL_PACKET_HEADER_XML_COMPONENT_CORROBORATED_LEGAL_IDENTITY_UNVERIFIED",
        "actual_new_primary_research_transports": 1, "successful_complete_http200_originals": 1,
        "explicit_new_packet_diagnostics": 1, "maximum_current_packet_diagnostic_attempts": 1,
        "new_request_receipt": receipt, "prior_failed_receipt_preserved": old,
        "reused_prior_xml_index_new_http_count": 0,
        "underlying_form4_disclosures": 1,
        "application_collector_live_or_shared_quota_history_claim": False,
        "prior_failed_capture_is_rewritten": False,
        "full_specialist_acceptance": False, "historical_oos_or_windows_claim": False,
        "source_service_pin": pin(REPO / SERVICE), "review": result,
    }


def build_review_plan():
    packet, xml, index, selection, _, _ = load_inputs()
    captures = {}
    for key, capture, path in (
        ("packet", packet, "evidence/batch9_sec_20261016/primary/nadella_2026_form4_complete_packet.txt"),
        ("xml", xml, "evidence/batch8_sec_20261015/primary/nadella_2026_form4.xml"),
        ("index", index, "evidence/batch8_sec_20261015/primary/nadella_2026_form4_index.html"),
    ):
        captures[key] = {"metadata": capture.summary(), "body_available": True,
                         "body_ref": {"path": path, "sha256": capture.sha256, "bytes": capture.size_bytes}}
    return {"schema_version": "captured_identity_review_plan_v1", "kind": "SEC_COMPLETE_PACKET",
            "request": {"selection": selection}, "captures": captures}


if __name__ == "__main__":
    for name, report in (("complete_packet_review_v1.json", build_review_artifact()),
                         ("actual_review_plan.json", build_review_plan())):
        with (ROOT / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(name)
