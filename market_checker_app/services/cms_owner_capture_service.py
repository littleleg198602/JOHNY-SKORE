"""Bounded, resumable primary CMS name capture for identity review.

This stores candidate pages, not issuer attribution or specialist acceptance.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from market_checker_app.services.healthcare_scout_service import CmsHospitalOwnerClient, CMS_URL
from market_checker_app.services.public_source_budget import PublicSourceBudget, PublicBudgetExhausted
from market_checker_app.utils.ticker_universe import load_canonical_tickers

FIELDS = ("ENROLLMENT ID", "ASSOCIATE ID - OWNER", "TYPE - OWNER",
          "ORGANIZATION NAME - OWNER", "ORGANIZATION NAME", "ASSOCIATION DATE - OWNER",
          "ROLE CODE - OWNER", "ROLE TEXT - OWNER", "PERCENTAGE OWNERSHIP")
MAX_BYTES = 8_000_000


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _save(path, state):
    payload = dict(state, content_sha256=_hash(state))
    body = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    if len(body.encode("utf-8")) > MAX_BYTES:
        raise ValueError("CMS checkpoint exceeds byte bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".cms-capture-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def capture_cms_owners(store, *, subject_id, query_name, checkpoint_path,
                       page_size=100, max_pages=10, as_of=None, client=None):
    """Resume the same exact-name query under the application's shared quota.

    Checkpoint scope is pinned; callers must explicitly use another file for
    another query. Completion is pagination completion, never issuer coverage.
    """
    if (subject_id not in load_canonical_tickers() or not isinstance(query_name, str)
            or not query_name.strip() or len(query_name) > 200
            or type(page_size) is not int or not 1 <= page_size <= 100
            or type(max_pages) is not int or not 1 <= max_pages <= 20):
        raise ValueError("Invalid CMS capture scope or bounds")
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Capture needs timezone")
    path = Path(checkpoint_path)
    from market_checker_app.services.healthcare_identity_service import DEFAULT_HEALTHCARE_IDENTITIES
    protected = [store.db_path, DEFAULT_HEALTHCARE_IDENTITIES]
    configured = os.getenv("JOHNY_SKORE_HEALTHCARE_IDENTITIES_FILE")
    if configured:
        protected.append(Path(configured))
    if (any(path.resolve() == item.resolve() or
            (path.exists() and item.exists() and path.samefile(item)) for item in protected)
            or path.suffix != ".json"
            or DEFAULT_HEALTHCARE_IDENTITIES.parent.resolve() in path.resolve().parents):
        raise ValueError("Capture cannot overwrite the database or production data")
    scope = dict(source_url=CMS_URL, subject_id=subject_id, query_name=query_name.strip(), page_size=page_size)
    state = dict(schema_version=1, scope=scope, pages=[], next_cursor=None,
                 complete=False, issuer_attribution_allowed=False,
                 pagination_snapshot_guaranteed=False)
    if path.exists():
        with path.open("rb") as stream:
            body = stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("CMS checkpoint exceeds byte bound")
        previous = json.loads(body)
        checksum = previous.pop("content_sha256", None)
        if (_hash(previous) != checksum or previous.get("schema_version") != 1
                or previous.get("scope") != scope or not isinstance(previous.get("pages"), list)
                or len(previous["pages"]) > 20):
            raise ValueError("Invalid or different CMS checkpoint scope")
        expected = None
        for position, page in enumerate(previous["pages"]):
            rows = page.get("rows")
            if (page.get("cursor") != expected or not isinstance(rows, list)
                    or len(rows) > page_size or page.get("next_cursor") !=
                    (str(int(expected or 0) + page_size) if len(rows) == page_size else None)):
                raise ValueError("Invalid CMS checkpoint cursor chain")
            expected = page["next_cursor"]
            if expected is None and position != len(previous["pages"]) - 1:
                raise ValueError("CMS checkpoint continues after terminal page")
        if (previous.get("next_cursor") != expected
                or previous.get("complete") != bool(previous["pages"] and expected is None)):
            raise ValueError("Invalid CMS checkpoint completion")
        state = previous
    def summary(status):
        return dict(status=status, pages=len(state["pages"]),
                    captured_rows=sum(len(p["rows"]) for p in state["pages"]),
                    next_cursor=state["next_cursor"], complete=state["complete"],
                    issuer_attribution_allowed=False, pagination_snapshot_guaranteed=False)
    if state["complete"]:
        return summary("CAPTURE_COMPLETE")
    budget = PublicSourceBudget(store, "cms", as_of=clock, max_run_seconds=120)
    if not budget.acquire():
        return budget.blocked_summary()
    client = client or CmsHospitalOwnerClient()
    status = "PARTIAL"
    try:
        # Another process may finish a page between our read and lease claim.
        # Never overwrite its newer cursor using our stale in-memory state.
        if path.exists():
            with path.open("rb") as stream:
                current_body = stream.read(MAX_BYTES + 1)
            if len(current_body) > MAX_BYTES:
                raise ValueError("CMS checkpoint exceeds byte bound")
            current = json.loads(current_body)
            current.pop("content_sha256", None)
            if _hash(current) != _hash(state):
                raise ValueError("CMS checkpoint changed during lease acquisition")
        for _ in range(max_pages):
            if len(state["pages"]) >= 20 or int(state["next_cursor"] or 0) > 1000:
                break
            if (budget.now() - budget.clock).total_seconds() >= 120:
                break
            cursor = state["next_cursor"]
            rows, next_cursor = budget.call(subject_id, "cms-name:" + _hash(scope),
                lambda: client.page(scope["query_name"], size=page_size, cursor=cursor))
            if (not isinstance(rows, list) or len(rows) > page_size
                    or next_cursor != (str(int(cursor or 0) + page_size) if len(rows) == page_size else None)):
                raise ValueError("Invalid CMS page or cursor")
            projected = []
            for row in rows:
                if (not isinstance(row, dict) or
                        row.get("ORGANIZATION NAME - OWNER", "").strip().casefold() != query_name.strip().casefold()
                        or any(not isinstance(row.get(k, ""), str) or len(row.get(k, "")) > 1000 for k in FIELDS)):
                    raise ValueError("CMS page has mismatched name or invalid fields")
                projected.append({key: row.get(key, "") for key in FIELDS})
            page = dict(cursor=cursor, next_cursor=next_cursor, rows=projected,
                        observed_at=budget.now().isoformat())
            candidate = dict(state, pages=state["pages"] + [page], next_cursor=next_cursor,
                             complete=next_cursor is None)
            _save(path, candidate)
            state = candidate
            if state["complete"]:
                status = "CAPTURE_COMPLETE"
                break
    except PublicBudgetExhausted:
        status = "WAIT_QUOTA"
    finally:
        budget.release()
    return budget.annotate(summary(status))
