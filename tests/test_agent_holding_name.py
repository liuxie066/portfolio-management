from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.app.agent_holding_name_service import AgentHoldingNameService, AgentNameError, authenticate
from src.app.operation_state_store import OperationStateStore
from src.service.http import create_app
from tests.test_holding_event_inbox_service import _hk_service, _payload

# Synthetic access credential, never an actual configured Agent secret.
TOKEN = 'synthetic-test-only'
POLICY = {'enabled': True, 'principals': [{'id': 'test-agent',
    'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
    'grants': [{'record_id': 'rec-1', 'account': 'test-account', 'broker': 'test-broker'}]}]}


def setup(tmp_path, monkeypatch, **updates):
    storage, inbox = _hk_service(tmp_path, updates=updates)
    clock = [1000.0]
    service = AgentHoldingNameService(storage=storage, store=inbox.store,
        reconciliation=inbox.workflow.reconciliation, clock=lambda: clock[0])
    policy = copy.deepcopy(POLICY)
    monkeypatch.setattr('src.service.agent_holdings.config.get', lambda key, *args: policy if key == 'agent_operations' else None)
    app = create_app(service=SimpleNamespace(storage=storage), agent_name_service=service)
    client = TestClient(app, client=('127.0.0.1', 50000), headers={'Authorization': 'Bearer '+TOKEN})
    return storage, inbox, service, client, clock, policy


def url(action):
    return '/api/v1/agent/holdings/rec-1/'+action


def receipt_count(store):
    with store._connect() as conn:
        return conn.execute('SELECT COUNT(*) FROM operation_receipt_outbox').fetchone()[0]


def test_preview_confirm_readback_and_self_event_do_not_notify(tmp_path, monkeypatch):
    storage, inbox, service, client, _, policy = setup(tmp_path, monkeypatch, currency='HDK')
    # Existing invalid currency blocker must survive the name-only operation.
    evaluation = inbox.workflow.reconciliation.evaluate(record_id='rec-1')
    inbox.workflow.materialize_preflight_plan(inbox.workflow.plan_evaluation(evaluation, trigger={}), evaluation,
                                            enqueue_receipts=False)
    original = dict(storage.fields)
    before_cases = inbox.store.list_holding_cases()
    response = client.post(url('name-preview'), json={})
    assert response.status_code == 200
    preview = response.json()
    assert preview['proposed_name'] == '测试证券'
    assert not storage.patch_calls and receipt_count(inbox.store) == 0
    assert inbox.store.list_holding_cases() == before_cases
    body = {'preview_id': preview['preview_id']}
    applied = client.post(url('name-confirm'), json=body).json()
    assert applied['state'] == 'succeeded' and applied['currency'] == 'HDK'
    assert applied['blocks_official_nav']
    assert storage.fields == {**original, 'asset_name': '测试证券'}
    assert receipt_count(inbox.store) == 0
    assert client.post(url('name-confirm'), json=body).json() == applied
    assert client.get(url('name-operations/'+preview['preview_id'])).json() == applied
    assert len(storage.patch_calls) == 1
    inbox.accept(_payload('self-event'))
    assert inbox.process_due()['success']
    assert receipt_count(inbox.store) == 0
    currency = [c for c in inbox.store.list_holding_cases() if c['field'] == 'currency']
    assert any(c['state'] == 'pending_confirmation' for c in currency)
    # Revoked record access protects even previously successful operation results.
    policy['principals'][0]['grants'][0]['record_id'] = 'rec-other'
    assert client.get(url('name-operations/'+preview['preview_id'])).status_code == 403


@pytest.mark.parametrize('change', [{'currency': 'USD'}, {'account': 'other'}, {'asset_name': 'human'}, {'quantity': 11}])
def test_source_changes_reject_without_write(tmp_path, monkeypatch, change):
    storage, inbox, _, client, _, _ = setup(tmp_path, monkeypatch)
    preview = client.post(url('name-preview'), json={}).json()
    storage.fields.update(change)
    result = client.post(url('name-confirm'), json={'preview_id': preview['preview_id']}).json()
    assert result['state'] == 'rejected'
    assert not storage.patch_calls and receipt_count(inbox.store) == 0


def test_expiry_and_unknown_do_not_retry(tmp_path, monkeypatch):
    storage, inbox, _, client, clock, _ = setup(tmp_path, monkeypatch)
    preview = client.post(url('name-preview'), json={}).json()
    clock[0] += 301
    assert client.post(url('name-confirm'), json={'preview_id': preview['preview_id']}).status_code == 409
    preview = client.post(url('name-preview'), json={}).json()
    storage.fail_write = True
    result = client.post(url('name-confirm'), json={'preview_id': preview['preview_id']}).json()
    assert result['state'] == 'unknown'
    assert client.post(url('name-confirm'), json={'preview_id': preview['preview_id']}).json() == result
    second = client.post(url('name-preview'), json={}).json()
    assert client.post(url('name-confirm'), json={'preview_id': second['preview_id']}).json()['state'] == 'unknown'
    assert len(storage.patch_calls) == 1 and receipt_count(inbox.store) == 0


def test_concurrent_confirm_claims_once(tmp_path, monkeypatch):
    storage, _, service, client, _, _ = setup(tmp_path, monkeypatch)
    preview = client.post(url('name-preview'), json={}).json()
    grant = authenticate(POLICY, 'Bearer '+TOKEN, 'rec-1')
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.confirm(grant, preview['preview_id']), range(2)))
    assert len(storage.patch_calls) == 1
    assert all(result['state'] in {'executing', 'succeeded'} for result in results)


def test_auth_extra_parameters_and_scope_fail_closed(tmp_path, monkeypatch):
    storage, _, _, client, _, policy = setup(tmp_path, monkeypatch)
    assert client.post(url('name-preview'), json={}, headers={'Authorization': ''}).status_code == 401
    assert client.post(url('name-preview'), json={'notify': True}).status_code == 422
    assert client.post(url('name-preview')+'?notify=true', json={}).status_code == 422
    assert client.post('/api/v1/agent/holdings/rec-other/name-preview', json={}).status_code == 403
    policy['enabled'] = False
    assert client.post(url('name-preview'), json={}).status_code == 503
    assert not storage.patch_calls


@pytest.mark.parametrize('field,value', [('id','*'), ('token_sha256','bad'), ('grants',[]), ('grants',[{'record_id':'*','account':'a','broker':'b'}])])
def test_malformed_auth_configuration(field,value):
    policy = copy.deepcopy(POLICY)
    policy['principals'][0][field] = value
    with pytest.raises(AgentNameError) as exc:
        authenticate(policy, 'Bearer '+TOKEN, 'rec-1')
    assert exc.value.status == 503


def test_status_without_database_does_not_initialize(tmp_path, monkeypatch):
    path = tmp_path/'nonexistent'/'operations.sqlite3'
    monkeypatch.setattr(OperationStateStore, 'resolve_db_path_read_only', staticmethod(lambda: path))
    service = AgentHoldingNameService(storage=object())
    with pytest.raises(AgentNameError):
        service.status(authenticate(POLICY, 'Bearer '+TOKEN, 'rec-1'), '0'*32)
    assert not path.parent.exists()


def test_known_name_and_missing_evidence_no_operation(tmp_path, monkeypatch):
    storage, inbox, _, client, _, _ = setup(tmp_path, monkeypatch, asset_name='existing')
    assert client.post(url('name-preview'), json={}).json()['state'] == 'noop'
    storage.fields['asset_name'] = ''
    inbox.workflow.reconciliation.hk_stock_name_lookup = lambda _: None
    result = client.post(url('name-preview'), json={}).json()
    assert result['state'] == 'not_executable' and 'preview_id' not in result
    assert not storage.patch_calls


def test_race_to_futu_never_observes_other_identity(tmp_path, monkeypatch):
    storage, inbox, _, client, _, _ = setup(tmp_path, monkeypatch)
    preview = client.post(url('name-preview'), json={}).json()
    original_read = storage.get_raw_holdings
    reads = [0]
    def changing_read(**kwargs):
        reads[0] += 1
        if reads[0] == 2:
            storage.fields.update(account='other-account', broker='富途')
        return original_read(**kwargs)
    storage.get_raw_holdings = changing_read
    calls = []
    inbox.workflow.reconciliation.futu_observer = lambda account: calls.append(account)
    result = client.post(url('name-confirm'), json={'preview_id': preview['preview_id']}).json()
    assert result['state'] == 'rejected'
    assert calls == [] and not storage.patch_calls


def test_readback_identity_race_never_observes_other_identity(tmp_path, monkeypatch):
    storage, inbox, _, client, _, _ = setup(tmp_path, monkeypatch)
    preview = client.post(url('name-preview'), json={}).json()
    original_patch = storage.patch_holding_record
    def moved_after_patch(patch, **kwargs):
        result = original_patch(patch, **kwargs)
        storage.fields.update(account='other-account', broker='富途')
        return result
    storage.patch_holding_record = moved_after_patch
    calls = []
    inbox.workflow.reconciliation.futu_observer = lambda account: calls.append(account)
    result = client.post(url('name-confirm'), json={'preview_id': preview['preview_id']}).json()
    assert result['state'] == 'unknown'
    assert calls == [] and 'asset_name' not in result
    assert receipt_count(inbox.store) == 0


def test_status_on_old_schema_is_read_only(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE old_marker(value TEXT)')
    before = path.read_bytes()
    monkeypatch.setattr(OperationStateStore, 'resolve_db_path_read_only', staticmethod(lambda: path))
    service = AgentHoldingNameService(storage=object())
    with pytest.raises(AgentNameError) as exc:
        service.status(authenticate(POLICY, 'Bearer '+TOKEN, 'rec-1'), '0'*32)
    assert exc.value.status == 404
    assert path.read_bytes() == before


def test_real_repository_agent_path_never_fetches_account_slice(tmp_path):
    from tests.test_holdings_preload_minimal import StubHoldingsClient
    from src.feishu_storage import FeishuStorage
    from src.app.holdings_reconciliation_service import HoldingsReconciliationService
    from tests.test_holding_event_inbox_service import _hk_evidence

    fields = {'asset_id': '00001', 'asset_type': 'hk_stock', 'asset_name': '',
              'account': 'test-account', 'broker': 'test-broker', 'currency': 'HKD',
              'quantity': 7, 'avg_cost': 12}
    class ExactRecordClient(StubHoldingsClient):
        def list_records(self, *args, **kwargs):
            self.list_records_calls.append({'args': args, 'kwargs': kwargs})
            raise AssertionError('Agent must not list any account holdings')
    client = ExactRecordClient(initial_records=[
        {'record_id': 'rec-1', 'fields': dict(fields)},
        {'record_id': 'rec-unrelated', 'fields': {**fields, 'asset_name': 'Unrelated'}},
    ])
    storage = FeishuStorage(client=client)
    service = AgentHoldingNameService(storage=storage,
        store=OperationStateStore(tmp_path/'real-adapter.sqlite3'),
        reconciliation=HoldingsReconciliationService(storage=storage, hk_stock_name_lookup=_hk_evidence))
    grant = authenticate(POLICY, 'Bearer '+TOKEN, 'rec-1')
    preview = service.preview(grant)
    result = service.confirm(grant, preview['preview_id'])
    assert result['state'] == 'succeeded'
    assert len(client.update_record_calls) == 1
    patch = client.update_record_calls[0]
    assert patch['record_id'] == 'rec-1'
    assert set(patch['fields']) == {'asset_name', 'updated_at'}
    assert patch['fields']['asset_name'] == '测试证券'
    assert client._records[0]['fields']['currency'] == 'HKD'
    assert client._records[0]['fields']['quantity'] == 7
    assert client._records[0]['fields']['avg_cost'] == 12
    assert client._records[1]['fields'] == {**fields, 'asset_name': 'Unrelated'}
    assert client.list_records_calls == []
    assert receipt_count(service.store) == 0
