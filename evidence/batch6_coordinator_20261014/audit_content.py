"""Read-only exact original content audit for the sixth integration."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from market_checker_app.services.ir_primary_scout_service import _has_sensitive_body

ROOT = Path(__file__).resolve().parents[2]
BASE = "cbc6c4a3f023f4f32778456ae4f73d6d2c91fcc6"


def pin(path):
    body = path.read_bytes()
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def build():
    original = []
    identity = ROOT / "evidence/batch6_identity_20261014"
    for path in sorted((identity / "captures").glob("*.metadata.json")):
        meta = json.loads(path.read_bytes())
        original.append((path.with_name(meta["body_path"]), meta["raw_sha256"], meta["bytes"], meta["requested_url"], meta["observed_at"]))
        assert meta["http_status"] == 200 and meta["status"] == "PUBLIC_ORIGINAL_BYTES_CAPTURED"
    healthcare = ROOT / "evidence/batch6_healthcare_20261014"
    meta = json.loads((healthcare / "sec_hca_submissions.metadata.json").read_bytes())
    original.append((healthcare / meta["body_path"], meta["body_sha256"], meta["body_bytes"], meta["response_url"], meta["observed_at"]))
    assert meta["http_status"] == 200 and meta["raw_publication_permitted"]
    for path in sorted((ROOT / "evidence/batch6_regulatory_20261014/raw").glob("*.metadata.json")):
        meta = json.loads(path.read_bytes())
        original.append((ROOT / meta["archive_path"], meta["raw_response_sha256"], meta["raw_response_bytes"], meta["requested_url"], meta["observed_at"]))
        assert meta["http_status"] == 200 and meta["public_raw_available"]
    sec = ROOT / "evidence/batch6_sec_20261014"
    for meta in json.loads((sec / "captures.json").read_bytes())["captures"]:
        original.append((sec / meta["path"], meta["sha256"], meta["size_bytes"], meta["url"], meta["observed_at"]))
        assert meta["http_status"] == 200 and meta["retention"] == "ORIGINAL_BYTES"
    assert len(original) == 12 and len({row[0] for row in original}) == 12
    bodies = []
    for path, digest, size, url, observed in original:
        current = pin(path)
        assert current["sha256"] == digest and current["bytes"] == size
        assert not _has_sensitive_body(path.read_bytes())
        assert subprocess.check_output(["git", "check-attr", "text", "--", current["path"]], cwd=ROOT).decode().rstrip().endswith(": unset")
        bodies.append(dict(current, url=url, observed_at=observed, original_http_status=200,
                           whole_body_gate_positive=False, text_conversion_disabled=True))
    prior = []
    for name in subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE, "evidence"], cwd=ROOT).decode().splitlines():
        body = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
        assert (ROOT / name).read_bytes() == body, name
        prior.append({"path": name, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
    registries = []
    for path in sorted((ROOT / "market_checker_app/data").glob("verified_*.json")):
        name = path.relative_to(ROOT).as_posix()
        assert path.read_bytes() == subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
        registries.append(pin(path))
    canonical = ROOT / "market_checker_app/data/market_checker_687_tickers.csv"
    assert canonical.read_bytes() == subprocess.check_output(["git", "show", BASE + ":" + canonical.relative_to(ROOT).as_posix()], cwd=ROOT)
    from market_checker_app.utils.ticker_universe import load_canonical_tickers
    assert len(load_canonical_tickers()) == 687
    absent = ["evidence/batch4_regulatory_20261013/raw/nhtsa_api_docs.html",
              "evidence/batch4_ir_20261013/primary/ir_authority.html",
              "evidence/batch5_ir_20261014/primary/ir_authority.html"]
    assert all(not (ROOT / name).exists() for name in absent)
    withheld_hashes = {
        "84b6ad89bee343819008e22babdf9a87dbfd3034a99022ea68320f0f17edf8b3",
        "deb5642d450c0fd60cd12066a42da8c1905f4fd47562afcef06cf8e428bcbeb1",
        "5130e6c90a0770d32e40f5eafcc87cd980d963934ae6c125963d8b1f2c20eac1"}
    public_names = set(subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT).decode().splitlines())
    assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() not in withheld_hashes
               for name in public_names if (ROOT / name).is_file())
    sources = [pin(path) for folder in ("market_checker_app", "tests") for path in sorted((ROOT / folder).rglob("*.py"))
               if ("batch6" in path.name or path.name in {"sec_corporate_action_bridge.py", "cms_provider_ownership_review.py",
                   "epa_frs_ownership_review.py", "sec_form4_person_bridge.py", "usaspending_prime_archive_recovery.py",
                   "prospective_specialist_history.py", "sec_operational_metrics_review.py", "captured_identity_review_service.py",
                   "specialist_evidence_runner.py", "specialist_evidence_ui.py", "app.py", "test_streamlit_ui_smoke.py",
                   "test_batch5_identity_symbol.py"})]
    return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(), "public_base": BASE,
        "new_original_primary_http_bodies": bodies, "new_original_http_requests": 12,
        "source_apis_http_requests": 0, "raw_copied_or_replaced": False, "quotas_reset_or_reconstructed": False,
        "all_prior_evidence_unchanged": True, "prior_evidence_files": len(prior),
        "prior_evidence_bytes": sum(row["bytes"] for row in prior),
        "prior_evidence_hash_manifest_sha256": hashlib.sha256(json.dumps(prior,sort_keys=True).encode()).hexdigest(),
        "production_registries": registries, "all_production_registries_unchanged": True,
        "canonical": pin(canonical), "canonical_tickers": 687, "whole_withheld_paths_absent": absent,
        "whole_withheld_original_hashes_absent_from_all_current_files": sorted(withheld_hashes),
        "current_source_test_versions": sources, "verified_adoptions": 0,
        "source_hashes_are_historical_availability_proof": False, "full_specialist_acceptance": False}


if __name__ == "__main__":
    target = Path(__file__).with_name("content_audit.json")
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(build(), sort_keys=True, indent=2) + "\n")
    print(target.name)
