"""Deadline propagation through the real Feishu and valuation boundaries."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from src.app.account_service import AccountService
from src.domain.holdings import RawHoldingRecord
from src.feishu.errors import FeishuReadTimeout
from src.feishu_client import FeishuClient
from src.feishu_storage import FeishuStorage
from src.local_cache import LocalHoldingsIndexCache, LocalPriceCache
from src.models import Holding
from src.portfolio import PortfolioManager
from src.service import PortfolioService
from src.service.http import create_app
from src.time_utils import remaining_timeout, sleep_with_deadline


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=100.0)

    def sleep(seconds):
        state.now += seconds

    state.sleep = Mock(side_effect=sleep)
    timer = SimpleNamespace(monotonic=lambda: state.now, time=lambda: state.now, sleep=state.sleep)
    for module in ('src.time_utils', 'src.feishu_client', 'src.service.application'):
        monkeypatch.setattr(f'{module}.time', timer)
    return state


def _client():
    client = FeishuClient(user_token='test-user-token')
    client.table_configs = {'holdings': {'app_token': 'test-base', 'table_id': 'test-table'}}
    client.session.trust_env = False
    client._deadline_session.trust_env = False
    return client


def _response(data=None, status=200):
    response = Mock(status_code=status)
    response.json.return_value = {'code': 0, 'data': data or {}}
    return response


def _cash(record_id='cash', account='sy'):
    return {'record_id': record_id, 'fields': {
        'asset_id': 'CNY-CASH', 'asset_name': 'Cash', 'asset_type': 'cash',
        'account': account, 'broker': 'IBKR', 'quantity': 100, 'currency': 'CNY',
    }}


def _storage(client, tmp_path):
    return FeishuStorage(
        client=client,
        local_holdings_index_cache=LocalHoldingsIndexCache(cache_file=tmp_path / 'holdings.json'),
        local_price_cache=LocalPriceCache(cache_file=tmp_path / 'prices.json'),
        local_nav_index_cache=Mock(), local_cash_flow_agg_cache=Mock(),
    )


def test_budget_helpers_reject_expired_and_unaffordable_waits(clock):
    assert remaining_timeout(None, 5) == 5
    assert remaining_timeout(100.0001, 5) == pytest.approx(0.0001)
    with pytest.raises(TimeoutError):
        remaining_timeout(100, 5)
    with pytest.raises(TimeoutError):
        sleep_with_deadline(2, 101)
    clock.sleep.assert_not_called()
    sleep_with_deadline(0.2, 101)
    assert clock.now == 100.2


def test_transport_recalculates_budget_after_token_and_limits_caller_timeout(clock):
    client = _client()

    def headers(*, deadline):
        assert deadline == 110
        clock.now += 3
        return {}

    client._get_headers = Mock(side_effect=headers)
    client._deadline_session.request = Mock(return_value=_response())
    client._request('GET', '/test', deadline=110, timeout=(1, 2))
    timeout = client._deadline_session.request.call_args.kwargs['timeout']
    assert timeout.total == 7
    assert timeout.connect_timeout == 1
    assert timeout.read_timeout == 2
    assert client._deadline_session.request.call_args.kwargs['allow_redirects'] is False
    assert client.session.get_adapter('https://').max_retries.total == 3
    assert client._deadline_session.get_adapter('https://').max_retries.total == 0


@pytest.mark.parametrize('expired', [False, True])
def test_request_timeout_keeps_stage_cause_and_deadline_semantics(clock, expired):
    client = _client()
    cause = requests.ReadTimeout('upstream stalled')

    def fail(*args, **kwargs):
        if expired:
            clock.now = 110
        raise cause

    client._deadline_session.request = Mock(side_effect=fail)
    storage = SimpleNamespace(get_raw_holdings=Mock(
        side_effect=lambda **kwargs: client.list_records('holdings', deadline=kwargs['deadline']),
    ))
    service = PortfolioService(storage=storage)
    result = service.get_valuation_evidence(accounts=['sy'], holdings_scope='non_futu', price_timeout=10)
    assert result['snapshot']['deadline_exceeded'] is expired
    item = result['account_status'][0]
    assert item['status'] == ('deadline_exceeded' if expired else 'unavailable')
    assert item['diagnostics'][0]['stage'] == 'holdings_read'
    assert 'upstream stalled' in item['warnings'][0]
    client._deadline_session.request.assert_called_once()
    with pytest.raises(FeishuReadTimeout) as caught:
        client._request('GET', '/test', deadline=clock.now + 10, _stage='holdings_read')
    assert caught.value.__cause__ is cause


@pytest.mark.parametrize(('lock_name', 'stage'), [
    ('_token_lock', 'token_lock'), ('_rate_lock', 'rate_limit_lock'),
])
def test_contended_locks_stop_at_deadline_without_releasing_other_owner(lock_name, stage):
    client = _client()
    client.user_token = None
    client.app_id, client.app_secret = 'test-id', 'test-secret'
    client._deadline_session.request = Mock()
    lock = getattr(client, lock_name)
    lock.acquire()
    try:
        with pytest.raises(FeishuReadTimeout) as caught:
            client._request('GET', '/test', deadline=time.monotonic() + 0.02)
        assert caught.value.stage == stage
        assert lock.locked()
        client._deadline_session.request.assert_not_called()
    finally:
        lock.release()


def test_token_uses_remaining_budget_and_failure_releases_lock(clock):
    client = _client()
    client.user_token = None
    client.app_id, client.app_secret = 'test-id', 'test-secret'
    cause = requests.ReadTimeout('token stalled')
    client._deadline_session.post = Mock(side_effect=cause)
    with pytest.raises(FeishuReadTimeout) as caught:
        client._get_tenant_token(deadline=104)
    assert caught.value.stage == 'token'
    assert caught.value.__cause__ is cause
    assert client._deadline_session.post.call_args.kwargs['timeout'].total == 4
    assert not client._token_lock.locked()
    assert client._tenant_token is None


def test_late_token_is_not_cached(clock):
    client = _client()
    client.user_token = None
    client.app_id, client.app_secret = 'test-id', 'test-secret'

    def late_token(*args, **kwargs):
        clock.now = 105
        response = Mock()
        response.json.return_value = {'code': 0, 'tenant_access_token': 'late', 'expire': 3600}
        return response

    client._deadline_session.post = Mock(side_effect=late_token)
    with pytest.raises(FeishuReadTimeout, match='token'):
        client._get_tenant_token(deadline=104)
    assert client._tenant_token is None
    assert not client._token_lock.locked()


def test_rate_limit_wait_does_not_oversleep_budget(clock):
    client = _client()
    client._last_request_time = 100
    client._deadline_session.request = Mock()
    with pytest.raises(FeishuReadTimeout) as caught:
        client._request('GET', '/test', deadline=100.01)
    assert caught.value.stage == 'rate_limit_wait'
    assert not client._rate_lock.locked()
    clock.sleep.assert_not_called()
    client._deadline_session.request.assert_not_called()


def test_429_retries_recompute_budget_without_changing_caller_timeout(clock):
    client = _client()
    client._deadline_session.request = Mock(side_effect=[_response(status=429), _response()])
    client._request('GET', '/test', deadline=110, timeout=(1, 2))
    calls = client._deadline_session.request.call_args_list
    assert [call.kwargs['timeout'].total for call in calls] == [10, 9]
    assert [call.kwargs['timeout'].read_timeout for call in calls] == [2, 2]
    assert clock.now == 101


def test_429_refuses_retry_that_cannot_fit_before_deadline(clock):
    client = _client()
    client._deadline_session.request = Mock(return_value=_response(status=429))
    with pytest.raises(FeishuReadTimeout) as caught:
        client._request('GET', '/test', deadline=100.5)
    assert caught.value.stage == 'retry_backoff'
    client._deadline_session.request.assert_called_once()
    clock.sleep.assert_not_called()


def test_retry_stops_when_scheduler_delay_uses_remaining_budget(clock):
    client = _client()
    client._deadline_session.request = Mock(return_value=_response(status=429))
    clock.sleep.side_effect = lambda _: setattr(clock, 'now', 110)
    with pytest.raises(FeishuReadTimeout):
        client._request('GET', '/test', deadline=105)
    client._deadline_session.request.assert_called_once()


def test_record_read_and_cached_token_share_remaining_budget(clock, tmp_path):
    client = _client()
    client.user_token = None
    client._tenant_token, client._token_expire_time = 'cached-token', 1000
    client._deadline_session.post = Mock(side_effect=AssertionError('cached token must be reused'))
    client._deadline_session.request = Mock(return_value=_response({'record': _cash()}))
    storage = _storage(client, tmp_path)
    rows = storage.get_raw_holdings(record_id='cash', deadline=110)
    assert rows[0].record_id == 'cash'
    call = client._deadline_session.request.call_args
    assert call.args[1].endswith('/records/cash')
    assert call.kwargs['timeout'].total == 10
    assert call.kwargs['headers']['Authorization'] == 'Bearer cached-token'
    client._deadline_session.post.assert_not_called()


def test_late_successful_response_is_discarded(clock, tmp_path):
    client = _client()

    def late_response(*args, **kwargs):
        clock.now = 111
        return _response({'items': [_cash()]})

    client._deadline_session.request = Mock(side_effect=late_response)
    storage = _storage(client, tmp_path)
    with pytest.raises(FeishuReadTimeout):
        storage.preload_holdings_index(account='sy', deadline=110)
    assert not storage._holding_fields_cache
    assert not (tmp_path / 'holdings.json').exists()


def test_incomplete_pagination_does_not_publish_or_replace_cached_slice(clock, tmp_path):
    client = _client()
    storage = _storage(client, tmp_path)
    client._deadline_session.request = Mock(return_value=_response({'items': [_cash()]}))
    storage.preload_holdings_index(account='sy', deadline=110)
    before = (tmp_path / 'holdings.json').read_bytes()
    memory_before = dict(storage._holding_fields_cache)

    def fail_second_page(*args, **kwargs):
        if 'page_token' in kwargs['params']:
            raise requests.ReadTimeout('second page stalled')
        return _response({'items': [_cash('different')], 'has_more': True, 'page_token': 'next'})

    client._deadline_session.request = Mock(side_effect=fail_second_page)
    with pytest.raises(FeishuReadTimeout):
        storage.preload_holdings_index(account='sy', deadline=110)
    assert client._deadline_session.request.call_count == 2
    assert (tmp_path / 'holdings.json').read_bytes() == before
    assert storage._holding_fields_cache == memory_before


def test_late_conversion_does_not_publish_cache(clock, tmp_path):
    client = _client()
    client._deadline_session.request = Mock(return_value=_response({'items': [_cash()]}))
    storage = _storage(client, tmp_path)
    original_convert = storage.holdings._convert_raw_holdings

    def convert(records):
        result = original_convert(records)
        clock.now = 111
        return result

    storage.holdings._convert_raw_holdings = convert
    with pytest.raises(TimeoutError):
        storage.get_holdings(account='sy', deadline=110)
    assert not storage._holding_fields_cache
    assert not storage._holdings_index_loaded_accounts
    assert not (tmp_path / 'holdings.json').exists()


def test_cached_holdings_need_no_network_but_expired_requests_do_not_load(clock, tmp_path):
    client = _client()
    client._deadline_session.request = Mock(return_value=_response({'items': [_cash()]}))
    storage = _storage(client, tmp_path)
    assert len(storage.get_holdings(account='sy', deadline=110)) == 1
    client._deadline_session.request.reset_mock()
    assert len(storage.get_holdings(account='sy', deadline=110)) == 1
    with pytest.raises(TimeoutError):
        storage.get_raw_holdings(account='other', deadline=100)
    client._deadline_session.request.assert_not_called()


def test_account_discovery_passes_one_deadline_to_every_source(clock):
    client = Mock(list_records=Mock(return_value=[]))
    storage = SimpleNamespace(get_holdings=Mock(return_value=[]), client=client)
    AccountService(storage=storage).list_accounts(deadline=110)
    storage.get_holdings.assert_called_once_with(account=None, include_empty=True, deadline=110)
    assert [call.args[0] for call in client.list_records.call_args_list] == ['transactions', 'cash_flow', 'nav_history']
    assert all(call.kwargs['deadline'] == 110 for call in client.list_records.call_args_list)


@pytest.mark.parametrize('source', ['holdings', 'transactions'])
def test_account_discovery_timeout_stops_later_sources(clock, source):
    storage = SimpleNamespace(get_holdings=Mock(return_value=[]), client=Mock())
    if source == 'holdings':
        storage.get_holdings.side_effect = TimeoutError('holdings stalled')
    else:
        storage.client.list_records.side_effect = TimeoutError('transactions stalled')
    with pytest.raises(TimeoutError):
        AccountService(storage=storage).list_accounts(deadline=110)
    assert storage.get_holdings.call_count == 1
    assert storage.client.list_records.call_count == (0 if source == 'holdings' else 1)


def test_deadline_discovery_does_not_retry_typeerror_without_budget(clock):
    storage = SimpleNamespace(get_holdings=Mock(side_effect=TypeError('bad conversion')))
    result = AccountService(storage=storage).list_accounts(deadline=110)
    storage.get_holdings.assert_called_once_with(account=None, include_empty=True, deadline=110)
    assert result['warnings'][0]['error'] == 'bad conversion'


@pytest.mark.parametrize(('warnings', 'code'), [([], 'INPUT_ERROR'), ([{'source': 'holdings', 'error': 'failed'}], 'PM_SERVICE_UNAVAILABLE')])
def test_only_complete_discovery_can_reject_unknown_accounts(clock, warnings, code):
    service = PortfolioService(storage=Mock())
    service.list_accounts = Mock(return_value={'accounts': [], 'warnings': warnings})
    result = service.get_valuation_evidence(accounts=['sy'])
    assert result['error_code'] == code


def test_one_account_failure_preserves_other_account_and_its_budget(clock):
    row = _cash(account='first')
    record = RawHoldingRecord(record_id=row['record_id'], raw_fields=row['fields'], fetched_at=datetime.now(timezone.utc))
    storage = SimpleNamespace(
        get_raw_holdings=Mock(side_effect=[[record], FeishuReadTimeout('holdings_read', 'early timeout')]),
        _convert_raw_holdings=lambda records: [Holding(**r.raw_fields, record_id=r.record_id) for r in records],
    )
    service = PortfolioService(storage=storage, portfolio=PortfolioManager(storage=storage, price_fetcher=Mock()))
    result = service.get_valuation_evidence(accounts=['first', 'second'], holdings_scope='non_futu')
    assert [item['status'] for item in result['account_status']] == ['complete', 'unavailable']
    assert result['status'] == 'partial'
    assert result['holdings'][0]['account'] == 'first'
    assert result['holdings'][0]['market_value_cny'] == 100
    assert all(call.kwargs['deadline'] == 130 for call in storage.get_raw_holdings.call_args_list)


def test_quote_budget_exhaustion_does_not_start_fx(clock, monkeypatch):
    row = _cash()
    row['fields'].update(asset_id='USD-CASH', currency='USD')
    record = RawHoldingRecord(record_id=row['record_id'], raw_fields=row['fields'], fetched_at=datetime.now(timezone.utc))
    storage = SimpleNamespace(
        get_raw_holdings=Mock(return_value=[record]),
        _convert_raw_holdings=lambda records: [Holding(**r.raw_fields, record_id=r.record_id) for r in records],
    )
    portfolio = PortfolioManager(storage=storage, price_fetcher=Mock())

    def finish_after_deadline(**kwargs):
        assert kwargs['deadline'] == 130
        clock.now = 131
        return {}, ['quote deadline exceeded']

    portfolio.fetch_price_snapshot = Mock(side_effect=finish_after_deadline)
    fx = Mock()
    monkeypatch.setattr('src.price_fetcher.PriceFetcher', lambda **kwargs: SimpleNamespace(fx_service=fx))
    result = PortfolioService(storage=storage, portfolio=portfolio).get_valuation_evidence(
        accounts=['sy'], holdings_scope='non_futu',
    )
    fx.fetch_exchange_rates_with_evidence.assert_not_called()
    assert result['snapshot']['deadline_exceeded'] is True
    assert result['status'] == 'partial'
    assert result['freshness']['trust_status'] != 'trusted'
    assert any('before FX' in warning for warning in result['warnings'])


@contextmanager
def _http_feishu(route, *, body_delay=0):
    state = SimpleNamespace(paths=[], completed=threading.Event())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state.paths.append(self.path)
            status, payload, delay = route(self.path)
            try:
                time.sleep(delay)
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.flush()
                time.sleep(body_delay)
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                state.completed.set()

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
    thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}'
    try:
        yield state
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


def test_real_adapter_does_not_retry_stalled_holdings_or_continue_after_return():
    with _http_feishu(lambda path: (200, {'code': 0, 'data': {'items': []}}, 0.2)) as server:
        client = _client()
        client.BASE_URL = server.url
        with pytest.raises(FeishuReadTimeout, match='holdings_read'):
            client.list_records('holdings', deadline=time.monotonic() + 0.06)
        assert server.completed.wait(2)
        assert len(server.paths) == 1


def test_real_token_timeout_prevents_holdings_request():
    with _http_feishu(lambda path: (200, {'code': 0, 'tenant_access_token': 'late', 'expire': 3600}, 0.2)) as server:
        client = _client()
        client.BASE_URL = server.url
        client.user_token = None
        client.app_id, client.app_secret = 'test-id', 'test-secret'
        with pytest.raises(FeishuReadTimeout, match='token'):
            client.list_records('holdings', deadline=time.monotonic() + 0.06)
        assert server.completed.wait(2)
        assert server.paths == ['/auth/v3/tenant_access_token/internal']
        assert client._tenant_token is None
        assert not client._token_lock.locked()


@pytest.mark.parametrize('token', [False, True])
def test_real_body_timeout_preserves_stage_and_original_cause(token):
    with _http_feishu(lambda path: (200, {'code': 0, 'data': {}}, 0), body_delay=0.15) as server:
        client = _client()
        client.BASE_URL = server.url
        client.timeout = (0.02, 0.03)
        if token:
            client.user_token = None
            client.app_id, client.app_secret = 'test-id', 'test-secret'
        with pytest.raises(FeishuReadTimeout) as caught:
            client.list_records('holdings', deadline=time.monotonic() + 1)
        assert caught.value.stage == ('token' if token else 'holdings_read')
        assert isinstance(caught.value.__cause__, requests.ConnectionError)
        assert len(server.paths) == 1
        assert client._tenant_token is None
        assert not client._token_lock.locked()


@pytest.mark.parametrize('scope', ['all', 'non_futu'])
def test_http_body_timeout_is_not_hidden_as_discovery_warning(tmp_path, scope):
    with _http_feishu(lambda path: (200, {'code': 0, 'data': {'items': [_cash()]}}, 0), body_delay=0.15) as server:
        client = _client()
        client.BASE_URL = server.url
        client.timeout = (0.02, 0.03)
        service = PortfolioService(storage=_storage(client, tmp_path), default_account='sy')
        api = TestClient(create_app(service=service), client=('127.0.0.1', 50000))
        response = api.post('/api/v1/analysis/valuation-evidence', json={
            'accounts': ['sy'], 'holdings_scope': scope, 'price_timeout': 1,
        })
        payload = response.json()
        if scope == 'all':
            assert response.status_code == 503
            assert payload['error_code'] == 'PM_SERVICE_UNAVAILABLE'
        else:
            assert response.status_code == 200
            assert payload['status'] == 'unavailable'
            assert payload['snapshot']['deadline_exceeded'] is False
            assert payload['account_status'][0]['diagnostics'][0]['stage'] == 'holdings_read'
        assert len(server.paths) == 1


@pytest.mark.parametrize('token', [False, True])
def test_connection_failure_is_not_misclassified_as_timeout(token):
    client = _client()
    failure = requests.ConnectionError('connection refused')
    if token:
        client.user_token = None
        client.app_id, client.app_secret = 'test-id', 'test-secret'
        client._deadline_session.post = Mock(side_effect=failure)
    else:
        client._deadline_session.request = Mock(side_effect=failure)
    with pytest.raises(requests.ConnectionError) as caught:
        client.list_records('holdings', deadline=time.monotonic() + 1)
    assert caught.value is failure


def test_real_429_does_not_start_unaffordable_retry():
    with _http_feishu(lambda path: (429, {'code': 99991400, 'msg': 'rate limited'}, 0)) as server:
        client = _client()
        client.BASE_URL = server.url
        with pytest.raises(FeishuReadTimeout, match='retry_backoff'):
            client.list_records('holdings', deadline=time.monotonic() + 0.3)
        assert len(server.paths) == 1


def test_real_pagination_timeout_leaves_cache_unpublished(tmp_path):
    def route(path):
        if 'page_token' in path:
            return 200, {'code': 0, 'data': {'items': [_cash('second')]}}, 0.2
        return 200, {'code': 0, 'data': {'items': [_cash()], 'has_more': True, 'page_token': 'next'}}, 0

    with _http_feishu(route) as server:
        client = _client()
        client.BASE_URL = server.url
        storage = _storage(client, tmp_path)
        with pytest.raises(FeishuReadTimeout):
            storage.preload_holdings_index(account='sy', deadline=time.monotonic() + 0.12)
        assert not storage._holding_fields_cache
        assert not (tmp_path / 'holdings.json').exists()
        assert server.completed.wait(2)
        assert len(server.paths) == 2


@pytest.mark.parametrize('scope', ['all', 'non_futu'])
def test_http_timeout_is_dependency_failure_not_unknown_account(tmp_path, scope):
    with _http_feishu(lambda path: (200, {'code': 0, 'data': {'items': [_cash()]}}, 0.2)) as server:
        client = _client()
        client.BASE_URL = server.url
        client.timeout = (0.02, 0.03)
        storage = _storage(client, tmp_path)
        service = PortfolioService(storage=storage, default_account='sy')
        api = TestClient(create_app(service=service), client=('127.0.0.1', 50000))
        response = api.post('/api/v1/analysis/valuation-evidence', json={
            'accounts': ['sy'], 'holdings_scope': scope, 'price_timeout': 1,
        })
        payload = response.json()
        if scope == 'all':
            assert response.status_code == 503
            assert payload['error_code'] == 'PM_SERVICE_UNAVAILABLE'
            assert 'account discovery' in payload['message']
        else:
            assert response.status_code == 200
            assert payload['schema_version'] == 'portfolio.valuation_evidence.v1'
            assert payload['status'] == 'unavailable'
            assert payload['snapshot']['deadline_exceeded'] is False
            assert payload['account_status'][0]['diagnostics'][0]['stage'] == 'holdings_read'
            assert payload['holdings'] == []
            assert payload['freshness']['trust_status'] != 'trusted'
        assert server.completed.wait(2)
        assert len(server.paths) == 1
