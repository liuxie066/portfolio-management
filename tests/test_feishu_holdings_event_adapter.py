from __future__ import annotations

import json
from types import SimpleNamespace

from src.app.holdings_event_service import HOLDINGS_EVENT_TYPE, HoldingsEventTarget
from src.feishu.holdings_event_adapter import FeishuHoldingsEventAdapter


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
    state = {}

    class WsClient:
        def __init__(self, app_id, app_secret, *, event_handler, log_level):
            state["ws_init"] = (app_id, app_secret, event_handler, log_level)

        def start(self):
            state["ws_started"] = True

    response = SimpleNamespace(
        success=lambda: True,
        data={"ok": True},
    )
    api_client = SimpleNamespace(
        drive=SimpleNamespace(
            v1=SimpleNamespace(
                file=SimpleNamespace(
                    subscribe=lambda request: state.setdefault("request", request) or response
                )
            )
        )
    )
    api_client.drive.v1.file.subscribe = lambda request: (
        state.__setitem__("request", request) or response
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


def _adapter(sdk):
    return FeishuHoldingsEventAdapter(
        target=HoldingsEventTarget("cli_data", "base_holdings", "tbl_holdings"),
        app_secret="secret",
        sdk_module=sdk,
        subscribe_request_class=_SubscribeRequest,
    )


def test_compatibility_adapter_default_secret_uses_bitable_role(monkeypatch):
    requested = []

    def fake_get(key, default=None):
        requested.append(key)
        return "data_secret" if key == "feishu.bitable.app_secret" else default

    monkeypatch.setattr("src.feishu.holdings_event_adapter.config.get", fake_get)

    adapter = FeishuHoldingsEventAdapter(
        target=HoldingsEventTarget("cli_data", "base_holdings", "tbl_holdings")
    )

    assert adapter.app_secret == "data_secret"
    assert requested == ["feishu.bitable.app_secret"]


def test_adapter_registers_only_exact_event_and_forwards_marshaled_payload():
    sdk, state = _sdk()
    received = []

    _adapter(sdk).start(received.append)
    event_type, callback = state["registered"]
    callback({"schema": "2.0"})

    assert event_type == HOLDINGS_EVENT_TYPE
    assert received == [{"schema": "2.0"}]
    assert state["ws_init"] == ("cli_data", "secret", "handler", "info")
    assert state["ws_started"] is True


def test_adapter_subscribes_only_configured_base_document_event():
    sdk, state = _sdk()

    result = _adapter(sdk).subscribe()

    assert result["success"] is True
    assert state["request"] == {
        "file_token": "base_holdings",
        "file_type": "bitable",
    }
    assert result["response"] == {"ok": True}
