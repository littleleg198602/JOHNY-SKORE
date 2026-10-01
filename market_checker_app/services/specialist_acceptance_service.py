"""Portable runtime diagnostics; exporting facts is not accepting a specialist."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import platform
from pathlib import Path
from typing import Mapping

from market_checker_app.services.research_profile_service import load_research_profiles
from market_checker_app.services.fdic_bank_scout_service import (
    DEFAULT_IDENTITIES, FDIC_REFRESH_DAYS, fdic_coverage, load_verified_banks,
)
from market_checker_app.services.specialist_status_service import load_specialist_status
from market_checker_app.storage.scout_store import ScoutStore
from market_checker_app.utils.ticker_universe import (
    CANONICAL_CSV_SHA256, load_canonical_ticker_records,
)


SOURCE_NAMES = ("sec", "fred", "eia", "usaspending", "recipient_discovery", "fda",
                "finra", "fdic", "sec13f", "nhtsa", "cms", "clinicaltrials", "ofac")


def build_specialist_acceptance_report(store: ScoutStore, *, as_of: datetime | None = None,
                                      environment: Mapping[str, str] | None = None) -> dict:
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Acceptance diagnostic time needs timezone")
    records = load_canonical_ticker_records()
    universe = {row["ticker"] for row in records}
    profiles = load_research_profiles()
    inventory = load_specialist_status()
    runs = store.latest_source_runs()
    source_facts = {name: runs.get(name, {"status": "NEVER_RUN"}) for name in SOURCE_NAMES}
    env = os.environ if environment is None else environment
    access_keys = {
        "sec_contact": ("JOHNY_SKORE_SEC_USER_AGENT",),
        "fred": ("JOHNY_SKORE_FRED_API_KEY",),
        "eia": ("JOHNY_SKORE_EIA_API_KEY",),
        "finra": ("JOHNY_SKORE_FINRA_CLIENT_ID", "JOHNY_SKORE_FINRA_CLIENT_SECRET"),
    }
    coverage = {}
    for source, interval, codes in (("fda", 30, None), ("finra", 15, None),
                                     ("cms", 30, {"HEALTH_SERVICES"}),
                                     ("clinicaltrials", 30, {"PHARMA", "MEDTECH"}),
                                     ("ofac", 1, None)):
        applicable = (universe if codes is None else
                      {t for t, p in profiles.by_ticker.items() if p.code in codes} & universe)
        coverage[source] = {"applicable_profile_subjects": len(applicable),
                            "refresh_days": interval,
                            **store.specialist_coverage(source, as_of=clock,
                                                        refresh_days=interval, subjects=applicable)}
    bank_subjects = {t for t, p in profiles.by_ticker.items() if p.code == "BANK"} & universe
    try:
        bank_identities = load_verified_banks(Path(env.get("JOHNY_SKORE_FDIC_BANKS_FILE") or DEFAULT_IDENTITIES))
        bank_coverage = fdic_coverage(store, bank_identities, as_of=clock, subjects=bank_subjects)
        coverage["fdic"] = {"status": "MEASURED", "applicable_profile_subjects": len(bank_subjects),
                            "refresh_days": FDIC_REFRESH_DAYS, "query_scope": "latest_two_reports_per_CERT",
                            "complete_issuer_groups_verified": False, **bank_coverage,
                            "unmapped_profile_subjects": len(bank_subjects) - bank_coverage["mapped_subjects"]}
    except (OSError, ValueError, KeyError, TypeError):
        coverage["fdic"] = {"status": "INVALID_IDENTITY_MANIFEST",
                            "applicable_profile_subjects": len(bank_subjects)}
    specialists = inventory["specialists"]
    return {
        "schema_version": 1, "generated_at": clock.astimezone(timezone.utc).isoformat(),
        "report_type": "runtime_diagnostic_not_completion_certificate",
        "historical_replay_supported": False,
        "host": {"system": platform.system(), "python": platform.python_version()},
        "universe": {"expected_subjects": len(records), "csv_sha256": CANONICAL_CSV_SHA256},
        "runtime": store.specialist_runtime_diagnostics(
            records=records, source_sha256=CANONICAL_CSV_SHA256, as_of=clock),
        "source_runs": source_facts, "rotating_source_coverage": coverage,
        "configured_access_present": {provider: all(bool(env.get(key, "").strip()) for key in keys)
                                      for provider, keys in access_keys.items()},
        "access_presence_is_successful_authentication": False,
        "inventory_as_of": inventory["as_of"], "specialists": specialists,
        "inventory_done_count": sum(row["code"] == "DONE" for row in specialists),
        "inventory_total": len(specialists), "completion_verified": False,
        "unproven_acceptance": ["dated issuer/product/instrument coverage",
                                "positive and negative live cases for every specialist",
                                "Windows end-to-end run and relevant coverage",
                                "historical out-of-sample evaluation"],
        "scoring_applied": False,
    }
