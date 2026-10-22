"""Read-only, reproducible review of dated SEC instrument evidence gaps.

The catalog is an observed current symbol directory, not a historical ticker
bridge. A DEI context and a Schedule 13 anchor establish their own source
claims; this report neither extends those dates nor exports identities.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from urllib.parse import urlsplit

from market_checker_app.services.sec13f_identity_bulk import (
    IDENTITY_FIELDS, Sec13fIdentityBulk, _action_text, _clock,
    _dated_action_text, _literal_in, _ticker, identity_version_sha256, share_class,
)

MAX_TRANSITION_TICKERS = 25
MAX_REPORTED_FACTS = 40


def _source_fact(service: Sec13fIdentityBulk, fact: dict) -> dict:
    snapshot = service.snapshots[fact["snapshot_id"]]
    return {**deepcopy(fact), "source_url": snapshot.url,
            "source_observed_at": snapshot.observed_at.isoformat(),
            "source_published_at": snapshot.published_at.isoformat(),
            "interval_extension_allowed": False}


def _instrument_fact(service: Sec13fIdentityBulk, fact: dict) -> dict:
    row = service.by_cusip.get(fact["cusip"])
    status = ("NOT_PRESENT_IN_SELECTED_LIST" if row is None else
              "DELETED_IN_SELECTED_LIST" if "*D*" in row["flags"] else "ACTIVE_LIST_ROW_ONLY")
    return {**_source_fact(service, fact), "selected_list_row": deepcopy(row),
            "selected_list_status": status, "proves_continuous_quarter_validity": False}


def instrument_transition_review(service: Sec13fIdentityBulk, *, tickers: list[str],
                                 primary_statements: list[dict] | None = None,
                                 corporate_action_bridges: list[dict] | None = None) -> dict:
    """Report exact evidence and blockers; never infer an alias or approval.

    Primary statements are explicit, bounded source-presence reviews. They
    can document a merger, rename or delisting without asserting that the
    excerpt itself binds every ticker/CIK/CUSIP/class needed for export.
    Export remains subject to ``Sec13fIdentityBulk.validate_reviewed``.
    """
    if (not isinstance(tickers, list) or not 0 < len(tickers) <= MAX_TRANSITION_TICKERS
            or len(set(tickers)) != len(tickers)
            or any(ticker not in service.universe_set for ticker in tickers)):
        raise ValueError("Transition review requires a bounded canonical ticker selection")
    statements = primary_statements if primary_statements is not None else []
    if not isinstance(statements, list) or len(statements) > 4 * len(tickers):
        raise ValueError("Unbounded primary transition statements")
    candidates = {ticker: service.candidate(ticker) for ticker in tickers}
    statement_ciks = {
        ticker: ({row["issuer_cik"] for row in candidates[ticker]["catalog_matches"]}
                 | {cover["issuer_cik"] for cover in service.covers
                    if _ticker(cover["ticker"]) == _ticker(ticker)}
                 | {row["issuer_cik"] for row in service.existing
                    if _ticker(row["ticker"]) == _ticker(ticker)}) for ticker in tickers
    }
    reviewed_statements = {ticker: [] for ticker in tickers}
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("ticker") not in reviewed_statements:
            raise ValueError("Primary statement is outside the transition-review scope")
        snapshot = service.snapshots.get(statement.get("source_snapshot_id"))
        if snapshot is None:
            raise ValueError("Primary statement refers to an unarchived source")
        excerpt = statement.get("excerpt")
        if not isinstance(excerpt, str) or not 30 <= len(excerpt) <= 4_000:
            raise ValueError("Primary transition statement needs a bounded exact excerpt")
        excerpt = " ".join(excerpt.split())
        if excerpt not in _action_text(snapshot):
            raise ValueError("Primary transition statement is absent from visible source text")
        archive_cik = urlsplit(snapshot.url).path.split("/")[4].zfill(10)
        if archive_cik not in statement_ciks[statement["ticker"]]:
            raise ValueError("Primary transition statement is filed by an unrelated issuer CIK")
        effective = statement.get("effective_on")
        date_text = statement.get("effective_date_text")
        if effective is not None:
            if (not isinstance(effective, str) or not isinstance(date_text, str)
                    or len(date_text) > 40 or not _dated_action_text(date_text, date.fromisoformat(effective))
                    or not _literal_in(date_text, excerpt)):
                raise ValueError("Primary transition statement has an unproved exact effective date")
        elif date_text is not None:
            raise ValueError("A source date label cannot silently become an effective date")
        reviewed_statements[statement["ticker"]].append({
            "source_snapshot_id": snapshot.snapshot_id, "source_url": snapshot.url,
            "source_observed_at": snapshot.observed_at.isoformat(),
            "source_published_at": snapshot.published_at.isoformat(),
            "excerpt": excerpt, "effective_on": effective,
            "effective_date_text": date_text, "scope": "PRIMARY_STATEMENT_ONLY",
            "identity_transition_approved": False,
        })
    records = []
    for ticker in tickers:
        candidate = candidates[ticker]
        catalog_ciks = {row["issuer_cik"] for row in candidate["catalog_matches"]}
        exact_covers = [cover for cover in service.covers if _ticker(cover["ticker"]) == _ticker(ticker)]
        histories = [entry for entry in service.existing if _ticker(entry["ticker"]) == _ticker(ticker)]
        research_ciks = catalog_ciks | {cover["issuer_cik"] for cover in exact_covers}
        research_ciks |= {entry["issuer_cik"] for entry in histories}
        related_symbols = [row for rows in service.catalog.values() for row in rows
                           if row["issuer_cik"] in research_ciks and _ticker(row["ticker"]) != _ticker(ticker)]
        instruments = [instrument for instrument in service.instruments
                       if instrument["issuer_cik"] in research_ciks]
        covers_in_quarter = [cover for cover in exact_covers
                             if service.period[0] <= date.fromisoformat(cover["anchor_date"]) <= service.period[1]]
        active_candidates = {row["cusip"]: row for row in candidate["candidate_instruments"]}
        matched_instruments = [instrument for instrument in instruments
                               if instrument["issuer_cik"] in catalog_ciks
                               and instrument["cusip"] in active_candidates
                               and service.period[0] <= date.fromisoformat(instrument["event_date"]) <= service.period[1]
                               and share_class(instrument["class_title"]) is not None
                               and share_class(instrument["class_title"]) == share_class(
                                   active_candidates[instrument["cusip"]]["class_description"])]
        blockers = []
        if not catalog_ciks:
            blockers.append("UNRESOLVED_CURRENT_TICKER_CIK")
        elif len(catalog_ciks) != 1:
            blockers.append("AMBIGUOUS_CURRENT_TICKER_CIK")
        if not matched_instruments:
            blockers.append("NO_EXACT_DATED_ACTIVE_CIK_CUSIP_CLASS_PROOF")
        if any(instrument["cusip"] in service.by_cusip
               and "*D*" in service.by_cusip[instrument["cusip"]]["flags"] for instrument in instruments):
            blockers.append("DELETED_INSTRUMENT_CUSIP")
        if not any(cover["issuer_cik"] in catalog_ciks
                   and share_class(cover["class_title"]) is not None
                   and any(share_class(cover["class_title"]) == share_class(row["class_description"])
                           for row in active_candidates.values()) for cover in covers_in_quarter):
            blockers.append("NO_SAME_CONTEXT_TICKER_CIK_CLASS_IN_SELECTED_QUARTER")
        historical_versions = []
        for entry in histories:
            row = service.by_cusip.get(entry["cusip"])
            if row is None:
                status = "NOT_PRESENT_IN_SELECTED_LIST"
            elif "*D*" in row["flags"]:
                status = "DELETED_IN_SELECTED_LIST"
                if "DELETED_BASELINE_CUSIP" not in blockers:
                    blockers.append("DELETED_BASELINE_CUSIP")
            elif share_class(row["class_description"]) != share_class(entry["class_description"]):
                status = "SELECTED_LIST_CLASS_DIFFERS"
            else:
                status = "ACTIVE_LIST_ROW_ONLY"
            if catalog_ciks and entry["issuer_cik"] not in catalog_ciks:
                if "CATALOG_CIK_CHANGED_REQUIRES_PRIMARY_TRANSITION" not in blockers:
                    blockers.append("CATALOG_CIK_CHANGED_REQUIRES_PRIMARY_TRANSITION")
            historical_versions.append({
                "identity": {key: entry[key] for key in IDENTITY_FIELDS},
                "identity_sha256": identity_version_sha256(entry), "selected_list_status": status,
                "selected_list_row": deepcopy(row),
                "covers_selected_quarter": (
                    _clock(entry["known_at"]) <= service.as_of
                    and date.fromisoformat(entry["effective_from"]) <= service.period[0]
                    and date.fromisoformat(entry["effective_to"]) >= service.period[1]
                ), "past_interval_or_knowledge_changed": False,
            })
        other_covers = [cover for cover in service.covers
                        if cover["issuer_cik"] in research_ciks and _ticker(cover["ticker"]) != _ticker(ticker)]
        counts = {"same_ticker_filing_contexts": len(exact_covers),
                  "other_catalog_symbols": len(related_symbols), "other_filing_contexts": len(other_covers),
                  "structured_instrument_claims": len(instruments), "historical_versions": len(historical_versions)}
        truncated = any(count > MAX_REPORTED_FACTS for count in counts.values())
        if truncated:
            blockers.append("REPORT_EVIDENCE_TRUNCATED")
        blockers.append("EXPLICIT_DATED_BINDING_AND_TRANSITION_REVIEW_NOT_EXPORTED")
        records.append({
            "ticker": ticker, "preparation_status": candidate["status"],
            "observed_current_catalog_matches": deepcopy(candidate["catalog_matches"]),
            "catalog_proves_historical_validity": False,
            "candidate_instruments": deepcopy(candidate["candidate_instruments"]),
            "candidates_truncated": candidate.get("candidates_truncated", False),
            "same_ticker_dated_filing_contexts": [_source_fact(service, cover) for cover in exact_covers[:MAX_REPORTED_FACTS]],
            "same_cik_other_catalog_symbols": deepcopy(related_symbols[:MAX_REPORTED_FACTS]),
            "same_cik_other_dated_filing_contexts": [
                _source_fact(service, cover) for cover in other_covers[:MAX_REPORTED_FACTS]
            ], "dated_structured_instrument_claims": [_instrument_fact(service, item) for item in instruments[:MAX_REPORTED_FACTS]],
            "historical_versions": historical_versions[:MAX_REPORTED_FACTS],
            "primary_statements": reviewed_statements[ticker],
            "evidence_counts": counts, "evidence_truncated": truncated,
            "blockers": blockers, "identity_status": "REVIEW_ONLY",
            "new_identity_approved": False, "automatic_symbol_alias_allowed": False,
            "scoring_allowed": False,
        })
    report = {"schema_version": 1, "status": "READ_ONLY_INSTRUMENT_TRANSITION_REVIEW",
              "quarter": service.quarter, "as_of": service.as_of.isoformat(),
              "source_fingerprint": service.fingerprint,
              "sources": {key: source.metadata() for key, source in sorted(service.snapshots.items())},
              "selected_tickers": list(tickers), "canonical_universe_size": len(service.universe),
              "records": records, "new_verified_identities": 0,
              "production_registry_changed": False, "score_changed": False,
              "specialist_done_changed": False}
    if corporate_action_bridges is not None:
        if (not isinstance(corporate_action_bridges, list) or len(corporate_action_bridges) > 2 * len(tickers)
                or any(not isinstance(claim, dict) or claim.get("ticker") not in tickers
                       for claim in corporate_action_bridges)):
            raise ValueError("Corporate-action bridge reviews exceed the bounded selected scope")
        from market_checker_app.services.sec_corporate_action_bridge import review_corporate_action_bridge
        report["corporate_action_bridge_reviews"] = [review_corporate_action_bridge(service, claim)
                                                     for claim in corporate_action_bridges]
    report["review_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report
