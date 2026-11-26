"""Read-throttle recovery through the real request/pagination boundary."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from src.feishu.errors import FeishuReadTimeout
from src.feishu_client import FeishuClient


@pytest.fixture
def transport(monkeypatch, caplog):
    clock = SimpleNamespace(now=100.0)

    def advance(seconds):
        clock.now += seconds

    clock.sleep = Mock(side_effect=advance)
    timer = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now, sleep=clock.sleep)
    monkeypatch.setattr('src.feishu_client.time', timer)
    monkeypatch.setattr('src.time_utils.time', timer)
    client = FeishuClient(app_id='private-app', user_token='private-token')
    client._min_interval = 0
    client.table_configs = {'holdings': {'app_token': 'private-base', 'table_id': 'private-table'}}
    request = Mock()
    monkeypatch.setattr(client.session, 'request', request)
    monkeypatch.setattr(client._deadline_session, 'request', request)
    caplog.set_level(logging.INFO, logger='src.feishu_client')
    return client, request, clock


def response(code=0, *, status=200, headers=None, data=None):
    result = requests.Response()
    result.status_code = status
    result.url = 'https://example.test/private-url'
    result.request = requests.Request('GET', result.url).prepare()
    result.headers.update(headers or {})
    result._content = json.dumps({
        'code': code, 'msg': 'TooManyRequest' if code == 1254290 else 'private-message',
        'data': {} if data is None else data,
        'error': {'log_id': 'trace-body'},
    }).encode()
    return result


def events(caplog):
    return [json.loads(row.message.split(' ', 1)[1]) for row in caplog.records
            if row.name == 'src.feishu_client' and row.message.startswith('feishu_request ')]


@pytest.mark.parametrize('status,code', [(200, 1254290), (400, 1254607)])
def test_list_records_retries_same_page_without_replaying_prior_pages(transport, caplog, status, code):
    client, request, clock = transport
    request.side_effect = [
        response(data={'items': [{'record_id': 'r1', 'fields': {}}], 'has_more': True, 'page_token': 'page2'}),
        response(code, status=status, headers={'Retry-After': '3', 'X-Tt-Logid': 'trace-header'}),
        response(data={'items': [{'record_id': 'r2', 'fields': {}}], 'has_more': False}),
    ]
    rows = client.list_records('holdings', filter_str='private-account', field_names=['private-field'])
    assert [row['record_id'] for row in rows] == ['r1', 'r2']
    calls = request.call_args_list
    assert 'page_token' not in calls[0].kwargs['params']
    assert calls[1].kwargs == calls[2].kwargs
    assert calls[1].kwargs['params']['page_token'] == 'page2'
    clock.sleep.assert_called_once_with(3)
    logs = events(caplog)
    assert [row['attempt'] for row in logs] == [1, 1, 2]
    assert [row['outcome'] for row in logs] == ['succeeded', 'retry', 'succeeded']
    assert logs[1]['log_id'] == 'trace-header'
    assert logs[1]['server_wait_seconds'] == 3
    assert logs[1]['retry_delay_seconds'] == 3
    assert logs[1]['endpoint'] == 'bitable.tables.records'
    assert len(logs[1]['app_hash']) == 16
    assert len({row['base_hash'] for row in logs}) == 1
    assert all(row['pid'] > 0 and row['elapsed_ms'] >= 0 for row in logs)
    for private in ('private-app', 'private-token', 'private-base', 'private-table',
                    'private-account', 'private-field', 'private-message', 'private-url'):
        assert private not in caplog.text


@pytest.mark.parametrize('status,code', [(200, 1254290), (400, 1254607)])
def test_business_throttle_exhaustion_preserves_last_trace(transport, caplog, status, code):
    client, request, clock = transport
    request.side_effect = [response(code, status=status, headers={'X-Tt-Logid': f'trace-{i}'}) for i in range(4)]
    with pytest.raises(Exception, match=rf'HTTP {status}, code={code}, log_id=trace-3'):
        client.list_records('holdings')
    assert request.call_count == 4
    assert [call.args[0] for call in clock.sleep.call_args_list] == [1, 2, 4]
    assert [row['outcome'] for row in events(caplog)] == ['retry'] * 3 + ['failed']


@pytest.mark.parametrize('method,status,code', [
    ('POST', 200, 1254290), ('PUT', 200, 1254290), ('DELETE', 200, 1254290),
    ('GET', 400, 1254290), ('GET', 500, 1254290), ('GET', 200, 1254045),
    ('GET', 200, '1254290'),
    ('POST', 400, 1254607), ('PUT', 400, 1254607), ('DELETE', 400, 1254607),
    ('GET', 200, 1254607), ('GET', 500, 1254607), ('GET', 400, '1254607'),
    ('GET', 400, 1254606),
])
def test_does_not_expand_business_retry_to_writes_or_other_errors(transport, method, status, code):
    client, request, clock = transport
    request.return_value = response(code, status=status)
    with pytest.raises(Exception):
        client._request(method, '/im/v1/messages' if method == 'POST' else '/test')
    request.assert_called_once()
    clock.sleep.assert_not_called()


@pytest.mark.parametrize('status,code', [(200, 1254290), (400, 1254607)])
@pytest.mark.parametrize('headers,delay', [
    ({}, 1), ({'Retry-After': '2'}, 2), ({'x-ogw-ratelimit-reset': '3'}, 3),
    ({'Retry-After': '2', 'x-ogw-ratelimit-reset': '4'}, 4),
    ({'Retry-After': 'Thu, 01 Jan 1970 00:01:45 GMT'}, 5),
    ({'Retry-After': 'invalid'}, 1), ({'Retry-After': '-10'}, 1),
    ({'Retry-After': 'nan'}, 1), ({'Retry-After': 'inf'}, 1),
])
def test_wait_hints(transport, headers, delay, status, code):
    client, request, clock = transport
    request.side_effect = [response(code, status=status, headers=headers), response()]
    assert client._request('GET', '/test') == {}
    clock.sleep.assert_called_once_with(delay)


@pytest.mark.parametrize('status,code', [(200, 1254290), (429, 0), (400, 1254607)])
def test_long_server_wait_is_not_shortened_or_slept(transport, caplog, status, code):
    client, request, clock = transport
    request.return_value = response(code, status=status, headers={'Retry-After': '120'})
    with pytest.raises(Exception):
        client._request('GET', '/test')
    request.assert_called_once()
    clock.sleep.assert_not_called()
    assert events(caplog)[0]['server_wait_seconds'] == 120
    assert events(caplog)[0]['retry_delay_seconds'] is None


@pytest.mark.parametrize('status,code', [(200, 1254290), (400, 1254607)])
def test_retry_recomputes_deadline_budget(transport, status, code):
    client, request, clock = transport
    request.side_effect = [response(code, status=status), response()]
    client._request('GET', '/test', deadline=110, timeout=(1, 2))
    assert [call.kwargs['timeout'].total for call in request.call_args_list] == [10, 9]
    assert all(call.kwargs['timeout'].read_timeout == 2 for call in request.call_args_list)
    assert clock.now == 101


@pytest.mark.parametrize('status,code', [(200, 1254290), (400, 1254607)])
def test_business_backoff_rejects_insufficient_budget(transport, caplog, status, code):
    client, request, clock = transport
    request.return_value = response(code, status=status, headers={'Retry-After': '3'})
    with pytest.raises(FeishuReadTimeout, match='retry_backoff'):
        client._request('GET', '/test', deadline=102)
    request.assert_called_once()
    clock.sleep.assert_not_called()
    assert events(caplog)[0]['outcome'] == 'failed'


@pytest.mark.parametrize('status,code', [(200, 1254290), (400, 1254607)])
def test_business_retry_rechecks_budget_after_oversleep(transport, status, code):
    client, request, clock = transport
    request.return_value = response(code, status=status)
    clock.sleep.side_effect = lambda _: setattr(clock, 'now', 120)
    with pytest.raises(FeishuReadTimeout):
        client._request('GET', '/test', deadline=110)
    request.assert_called_once()


def test_http_429_non_json_still_retries(transport):
    client, request, clock = transport
    throttle = response(status=429)
    throttle._content = b'too many requests'
    request.side_effect = [throttle, response()]
    assert client._request('GET', '/test') == {}
    clock.sleep.assert_called_once_with(1)


def test_not_ready_readback_does_not_replay_successful_write(transport, caplog):
    client, request, clock = transport
    request.side_effect = [
        response(data={'record': {'record_id': 'mmf-record', 'fields': {'quantity': 200}}}),
        response(1254607, status=400),
        response(data={'items': [{'record_id': 'mmf-record', 'fields': {'quantity': 200}}]}),
    ]
    client.update_record('holdings', 'mmf-record', {'quantity': 200})
    rows = client.list_records('holdings')
    assert rows[0]['fields']['quantity'] == 200
    assert [call.args[0] for call in request.call_args_list] == ['PUT', 'GET', 'GET']
    assert request.call_args_list[1] == request.call_args_list[2]
    clock.sleep.assert_called_once_with(1)
    assert [event['outcome'] for event in events(caplog)] == ['succeeded', 'retry', 'succeeded']


def test_transport_failure_logs_no_exception_message_and_does_not_retry(transport, caplog):
    client, request, clock = transport
    request.side_effect = requests.ConnectionError('private-url private-token')
    with pytest.raises(requests.ConnectionError):
        client._request('GET', '/test/private-id')
    request.assert_called_once()
    clock.sleep.assert_not_called()
    event = events(caplog)[0]
    assert event['http_status'] is None
    assert event['error_type'] == 'ConnectionError'
    assert 'private-' not in caplog.text
