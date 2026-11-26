from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.app.cash_flow_read import read_cash_flows
from src.domain.cash_flow_contracts import RawCashFlowRecord
from src.service.http import create_app


FIELDS = dict(account="lx", broker="富途", currency="HKD", amount=-10000, flow_date="2026-11-26")


class Storage:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_raw_cash_flows(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def test_manual_rows_need_no_generated_fields_and_do_not_write():
    storage = Storage([RawCashFlowRecord("pm1", FIELDS)])
    result = read_cash_flows(storage, "lx")
    assert storage.calls == [{"require_complete": True}]
    assert result["records"][0]["flow_type"] == "WITHDRAW"
    assert result["row_errors"] == []
    modified = read_cash_flows(Storage([RawCashFlowRecord("pm1", {**FIELDS, "remark": "note"})]), "lx")
    assert modified["records"][0]["financial_version"] == result["records"][0]["financial_version"]


def test_invalid_rows_are_reported_and_incomplete_read_is_not_empty():
    invalid = read_cash_flows(Storage([RawCashFlowRecord("bad", {**FIELDS, "broker": ""})]), "lx")
    assert invalid["row_errors"][0]["record_id"] == "bad"
    assert invalid["freshness"]["trust_status"] == "partial"
    def fail(**kwargs):
        raise RuntimeError("incomplete page")
    with pytest.raises(RuntimeError, match="incomplete"):
        read_cash_flows(SimpleNamespace(get_raw_cash_flows=fail), "lx")


def test_http_read_requires_account_and_preserves_owner_freshness():
    response = read_cash_flows(Storage([]), "lx")
    service = SimpleNamespace(get_cash_flows=lambda **kwargs: response)
    client = TestClient(create_app(service=service), client=("127.0.0.1", 50000))
    assert client.get("/api/v1/cash-flows").status_code == 422
    result = client.get("/api/v1/cash-flows", params={"account": "lx"})
    assert result.status_code == 200
    assert result.headers["X-PM-API-Version"] == "portfolio.api.v1"
    assert result.json()["freshness"] == response["freshness"]


class FeishuReadFake:
    from src.feishu_client import FeishuClient
    list_records = FeishuClient.list_records

    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []
        self.forbidden_calls = []

    def _get_table_config(self, table):
        assert table == "cash_flow"
        return "test-app", "test-table"

    def _request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        if method != "GET":
            self.forbidden_calls.append((method, endpoint))
            raise AssertionError("write forbidden")
        return next(self.pages)

    def _forbid(self, *args, **kwargs):
        self.forbidden_calls.append((args, kwargs))
        raise AssertionError("write forbidden")

    create_record = update_record = delete_record = _forbid
    batch_create_records = batch_update_records = batch_delete_records = _forbid


def adapter_http(pages):
    from src.feishu_storage import FeishuStorage
    from src.service.application import PortfolioService

    fake = FeishuReadFake(pages)
    storage = FeishuStorage(client=fake, local_price_cache=SimpleNamespace())
    client = TestClient(create_app(service=PortfolioService(storage=storage)),
                        client=("127.0.0.1", 50000), raise_server_exceptions=False)
    return fake, client


def test_real_repository_pagination_isolation_and_unknown_owner():
    fake, client = adapter_http([
        {"items": [{"record_id": "lx1", "fields": FIELDS},
                   {"record_id": "sy1", "fields": {**FIELDS, "account": "sy"}}],
         "has_more": True, "page_token": "page2"},
        {"items": [{"record_id": "unknown", "fields": {**FIELDS, "account": ""}}], "has_more": False},
    ])
    response = client.get("/api/v1/cash-flows", params={"account": "lx"})
    assert response.status_code == 200
    payload = response.json()
    assert [row["record_id"] for row in payload["records"]] == ["lx1"]
    assert payload["freshness"]["trust_status"] == "partial"
    assert payload["row_errors"] == [{"issues": [{"code": "cash_flow_account_missing"}]}]
    assert len(fake.calls) == 2
    assert all(not call[2].get("params", {}).get("filter") for call in fake.calls)
    assert not fake.forbidden_calls


def test_real_repository_incomplete_page_is_public_503():
    fake, client = adapter_http([{"items": [], "has_more": True}])
    response = client.get("/api/v1/cash-flows", params={"account": "lx"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "PM_SERVICE_UNAVAILABLE"
    assert not fake.forbidden_calls


@pytest.mark.parametrize("owner", [[{"text": "lx"}], {"text": "lx"}, True, 123])
def test_malformed_account_cannot_become_trusted_empty(owner):
    fake, client = adapter_http([{"items": [{"record_id": "bad", "fields": {**FIELDS, "account": owner}}],
                                 "has_more": False}])
    result = client.get("/api/v1/cash-flows", params={"account": "lx"})
    assert result.status_code == 200
    payload = result.json()
    assert payload["freshness"]["trust_status"] == "partial"
    assert payload["row_errors"] == [{"issues": [{"code": "cash_flow_account_missing"}]}]
    assert not fake.forbidden_calls


@pytest.mark.parametrize("currency", [[{"text": "HKD"}], {"text": "HKD"}])
def test_nested_invalid_value_returns_json_row_error(currency):
    fake, client = adapter_http([{"items": [{"record_id": "bad", "fields": {**FIELDS, "currency": currency}}],
                                 "has_more": False}])
    result = client.get("/api/v1/cash-flows", params={"account": "lx"})
    assert result.status_code == 200
    assert result.json()["row_errors"][0]["record_id"] == "bad"
    assert result.json()["freshness"]["trust_status"] == "partial"
    assert not fake.forbidden_calls
