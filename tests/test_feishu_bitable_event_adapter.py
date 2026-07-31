from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.app.cash_flow_event_service import CashFlowEventTarget
from src.app.holdings_event_service import HOLDINGS_EVENT_TYPE, HoldingsEventTarget
from src.feishu.bitable_event_adapter import (
    FeishuBitableEventAdapter,
    validate_bitable_targets,
)


class _HandlerBuilder:
    def __init__(self, state):
        self.state = state

    def register_p2_customized_event(self, event_type, callback):
        self.state["registered"] = (event_type, callback)
        return self

    def build(self):
        return "handler"


class _ClientBuilder:
    def __init__(self, client):
        self.client = client

    def app_id(self, value):
        self.client.app_id = value
        return self

    def app_secret(self, value):
        self.client.app_secret = value
        return self

    def build(self):
        return self.client


class _SubscribeRequestBuilder:
    def __init__(self):
        self.values = {}

    def file_token(self, value):
        self.values["file_token"] = value
        return self

    def file_type(self, value):
        self.values["file_type"] = value
        return self

    def event_type(self, value):
        self.values["event_type"] = value
        return self

    def build(self):
        return dict(self.values)


class _SubscribeRequest:
    @staticmethod
    def builder():
        return _SubscribeRequestBuilder()


def _sdk():
    state = {"requests": []}

    class WsClient:
        def __init__(self, app_id, app_secret, *, event_handler, log_level):
            state["ws_init"] = (app_id, app_secret, event_handler, log_level)

        def start(self):
            state["ws_started"] = True

    response = SimpleNamespace(success=lambda: True, data={"ok": True})

    def subscribe(request):
        state["requests"].append(request)
        return response

    api_client = SimpleNamespace(
        drive=SimpleNamespace(
            v1=SimpleNamespace(file=SimpleNamespace(subscribe=subscribe))
        )
    )
    sdk = SimpleNamespace(
        JSON=SimpleNamespace(marshal=lambda value: json.dumps(value)),
        EventDispatcherHandler=SimpleNamespace(
            builder=lambda _key, _verification: _HandlerBuilder(state)
        ),
        ws=SimpleNamespace(Client=WsClient),
        LogLevel=SimpleNamespace(INFO="info"),
        Client=SimpleNamespace(builder=lambda: _ClientBuilder(api_client)),
    )
    return sdk, state


def _targets(*, cash_file="base_portfolio", cash_table="tbl_cash_flow"):
    return (
        HoldingsEventTarget("cli_data", "base_portfolio", "tbl_holdings"),
        CashFlowEventTarget("cli_data", cash_file, cash_table),
    )


def _adapter(sdk, targets):
    return FeishuBitableEventAdapter(
        targets=targets,
        app_secret="secret",
        sdk_module=sdk,
        subscribe_request_class=_SubscribeRequest,
    )


def test_shared_adapter_registers_one_event_callback():
    sdk, state = _sdk()
    received = []

    _adapter(sdk, _targets()).start(received.append)
    event_type, callback = state["registered"]
    callback({"schema": "2.0"})

    assert event_type == HOLDINGS_EVENT_TYPE
    assert received == [{"schema": "2.0"}]
    assert state["ws_init"] == ("cli_data", "secret", "handler", "info")
    assert state["ws_started"] is True


def test_subscribe_deduplicates_same_file_and_reports_distinct_files():
    same_sdk, same_state = _sdk()
    same = _adapter(same_sdk, _targets()).subscribe()

    assert same["success"] is True
    assert [item["file_token"] for item in same["results"]] == ["base_portfolio"]
    assert [item["file_token"] for item in same_state["requests"]] == [
        "base_portfolio"
    ]

    split_sdk, split_state = _sdk()
    split = _adapter(split_sdk, _targets(cash_file="base_cash")).subscribe()

    assert split["success"] is True
    assert [item["file_token"] for item in split["results"]] == [
        "base_cash",
        "base_portfolio",
    ]
    assert [item["file_token"] for item in split_state["requests"]] == [
        "base_cash",
        "base_portfolio",
    ]


def test_target_preflight_rejects_collision_but_allows_same_table_id_in_other_file():
    with pytest.raises(ValueError, match="target collision"):
        validate_bitable_targets(
            _targets(cash_file="base_portfolio", cash_table="tbl_holdings")
        )

    resolved = validate_bitable_targets(
        _targets(cash_file="base_cash", cash_table="tbl_holdings")
    )
    assert len(resolved) == 2
