from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.domain.snapshot_contracts import NormalizedValuationSnapshot
from src.service.http import create_app


@pytest.mark.parametrize('official', [True, False])
def test_daily_bundle_transports_facts_without_internal_write_capability(official):
    builder = (NormalizedValuationSnapshot._from_valuation_service
               if official else NormalizedValuationSnapshot.build)
    normalized = builder(account='synthetic', rows=(), shares='100.12345678')
    valuation = normalized.to_portfolio_valuation()
    bundle = {
        'success': True,
        'snapshot': {'normalized_valuation': normalized, 'valuation': valuation},
        'report': {'total_value': 0, 'warnings': ['synthetic empty portfolio']},
    }
    service = SimpleNamespace(daily_report_bundle=lambda **kwargs: bundle)
    client = TestClient(create_app(service), client=('127.0.0.1', 50000))
    response = client.post('/report/daily-bundle', json={'account': 'synthetic'})
    assert response.status_code == 200
    payload = response.json()
    assert 'normalized_valuation' not in payload['snapshot']
    assert '_official_issuer' not in response.text
    assert payload['report'] == bundle['report']
    assert payload['snapshot']['valuation']['total_value_cny'] == 0
    assert normalized.official_eligible is official
    assert bundle['snapshot']['normalized_valuation'] is normalized
    normalized.assert_compatible(valuation)


def test_daily_bundle_keeps_application_failure_payload():
    failure = {'success': False, 'error': 'synthetic source unavailable', 'dry_run': True}
    service = SimpleNamespace(daily_report_bundle=lambda **kwargs: failure)
    client = TestClient(create_app(service), client=('127.0.0.1', 50000))
    assert client.post('/report/daily-bundle', json={}).json() == failure
