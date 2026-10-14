"""A single prospective run in the pre-existing canonical task ledger.

No documentation request, no fresh store, no synthetic production registry.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from market_checker_app.services.nhtsa_negative_review_service import (
    NhtsaNegativeReviewService, NhtsaRawRecallClient, documented_model_scope,
)
from market_checker_app.storage.scout_store import ScoutStore

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
STORE_PATH = Path('/workspace/scratch/334b3a149083/JOHNY-batch4-integration-20261013/outputs/market_checker_history.db')


def documented_scopes():
    scopes = []
    for make, models in (
        ('CADILLAC', ['CT5', 'Celestiq', 'Optiq', 'Vistiq', 'XT5']),
        ('CHEVROLET', ['Corvette', 'Trax']),
        ('FORD', ['Bronco']),
    ):
        catalog = ROOT / f'evidence/batch4_regulatory_20261013/raw/nhtsa_models_{make.lower()}_2026.json'
        metadata = json.loads(catalog.with_name(catalog.stem + '.metadata.json').read_text())
        for model in models:
            scopes.append(documented_model_scope(make=make, model=model, model_year=2026,
                catalog_body=catalog.read_bytes(), catalog_metadata=metadata))
    return scopes


if __name__ == '__main__':
    if not STORE_PATH.is_file():
        raise RuntimeError('Authorized durable task ledger is unavailable; no fresh database is created')
    store = ScoutStore(STORE_PATH)
    initial_clock = datetime.now(timezone.utc)
    before = store.public_daily_usage('nhtsa', as_of=initial_clock)
    service = NhtsaNegativeReviewService(store, client=NhtsaRawRecallClient(),
        scopes=documented_scopes(), archive_root=FOLDER / 'captures', max_requests=8, max_run_seconds=180)
    result = service.run()
    final_clock = datetime.now(timezone.utc)
    report = service.report(as_of=final_clock, limit=8)
    output = {'schema_version': 1, 'kind': 'PROSPECTIVE_SHARED_LEDGER_NHTSA_MODEL_YEAR_RUN',
        'run_started_at': initial_clock.isoformat(), 'recorded_at': final_clock.isoformat(),
        'shared_ledger_reused': True, 'fresh_or_reset_database': False,
        'shared_registration_prospective_utc_day': '2026-10-14',
        'prior_research_quota_history_recovered': False, 'daily_before': before,
        'daily_after': store.public_daily_usage('nhtsa', as_of=final_clock),
        'scopes': [scope.record() for scope in service.scopes], 'result': result, 'persisted_report': report,
        'production_registry_modified': False, 'issuer_wide_absence_verified': False,
        'scoring_applied': False, 'prediction_input': False}
    (FOLDER / 'live_run.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'attempted_requests': result['attempted_requests'],
        'complete_negative_model_years': result['complete_negative_model_years'],
        'live_complete_negative_model_years': result['live_complete_negative_model_years'],
        'cases': [{'make': row['scope']['make'], 'model': row['scope']['model'],
                   'http_status': row.get('http_status'), 'status': row['status'],
                   'publisher_count': row.get('publisher_count')} for row in result['reviews']]}), flush=True)
