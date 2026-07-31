from __future__ import annotations

import pytest

from src.app.bitable_event_contract import BITABLE_RECORD_CHANGED_EVENT_TYPE
from src.app.cash_flow_event_service import (
    CASH_FLOW_EVENT_TYPE,
    CashFlowEventTarget,
    CashFlowEventTargetMismatch,
    normalize_cash_flow_event,
)
from src.app.holdings_event_service import HOLDINGS_EVENT_TYPE, MAX_EVENT_ACTIONS


TARGET = CashFlowEventTarget(
    app_id="cli_data",
    file_token="base_portfolio",
    table_id="tbl_cash_flow",
)


def test_table_specific_event_types_share_the_neutral_protocol_contract():
    assert CASH_FLOW_EVENT_TYPE == HOLDINGS_EVENT_TYPE
    assert CASH_FLOW_EVENT_TYPE == BITABLE_RECORD_CHANGED_EVENT_TYPE


def _payload(*, event_id="evt-cf-1", actions=None, table_id="tbl_cash_flow"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": CASH_FLOW_EVENT_TYPE,
            "app_id": "cli_data",
            "create_time": "1785510000000",
        },
        "event": {
            "file_token": "base_portfolio",
            "file_type": "bitable",
            "table_id": table_id,
            "revision": 9,
            "action_list": actions
            or [
                {"action": "record_edited", "record_id": "rec-2", "field_name": "x"},
                {"action": "record_added", "record_id": "rec-1"},
                {"action": "record_edited", "record_id": "rec-2"},
            ],
        },
    }


def test_normalize_cash_flow_event_requires_exact_target_and_freezes_actions():
    normalized = normalize_cash_flow_event(_payload(), target=TARGET)

    assert normalized.event_id == "evt-cf-1"
    assert normalized.revision == "9"
    assert normalized.action_list == (
        {"action": "record_added", "record_id": "rec-1"},
        {"action": "record_edited", "record_id": "rec-2"},
    )
    assert len(normalized.payload_digest) == 64

    with pytest.raises(CashFlowEventTargetMismatch):
        normalize_cash_flow_event(_payload(table_id="tbl_holdings"), target=TARGET)


def test_normalize_cash_flow_event_rejects_malformed_or_unbounded_transport():
    malformed = _payload()
    malformed["schema"] = "1.0"
    with pytest.raises(ValueError, match="schema 2.0"):
        normalize_cash_flow_event(malformed, target=TARGET)

    too_many = _payload(
        actions=[
            {"action": "record_edited", "record_id": f"rec-{index}"}
            for index in range(MAX_EVENT_ACTIONS + 1)
        ]
    )
    with pytest.raises(ValueError, match="action_list exceeds"):
        normalize_cash_flow_event(too_many, target=TARGET)


def test_cash_flow_payload_digest_covers_full_delivery():
    first = normalize_cash_flow_event(_payload(), target=TARGET)
    changed = _payload()
    changed["header"]["create_time"] = "1785510000001"
    second = normalize_cash_flow_event(changed, target=TARGET)

    assert first.action_list == second.action_list
    assert first.payload_digest != second.payload_digest
