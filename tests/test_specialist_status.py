import json

import pytest

from market_checker_app.services.specialist_status_service import load_specialist_status


def test_specialist_inventory_has_unique_scope_and_no_unverified_done():
    inventory = load_specialist_status()
    rows = inventory["specialists"]
    assert len(rows) >= 20
    assert {row["id"] for row in rows} >= {
        "identity", "sec", "suppliers", "commodities", "contracts",
        "fda", "bank_fdic", "institutions", "regulatory", "evaluation",
    }
    assert all(row["code"] != "DONE" for row in rows)


def test_inventory_rejects_claim_of_done_without_live_evidence(tmp_path):
    payload = load_specialist_status()
    payload["specialists"][0]["code"] = "DONE"
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be DONE"):
        load_specialist_status(path)
