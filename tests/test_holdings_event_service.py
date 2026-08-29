from __future__ import annotations

import pytest

from src.app.holdings_event_service import (
    HOLDINGS_EVENT_TYPE,
    MAX_EVENT_ACTIONS,
    HoldingEventTargetMismatch,
    HoldingsEventTarget,
    NormalizedHoldingEvent,
    normalize_holding_event,
)


TARGET = HoldingsEventTarget(
    app_id="cli_data",
    file_token="base_holdings",
    table_id="tbl_holdings",
)


def _payload(*, event_id="evt-1", actions=None, table_id="tbl_holdings"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": HOLDINGS_EVENT_TYPE,
            "app_id": "cli_data",
            "create_time": "1785510000000",
        },
        "event": {
            "file_token": "base_holdings",
            "file_type": "bitable",
            "table_id": table_id,
            "revision": 7,
            "action_list": actions
            or [
                {"action": "record_edited", "record_id": "rec-2", "field_name": "x"},
                {"action": "record_added", "record_id": "rec-1"},
                {"action": "record_edited", "record_id": "rec-2"},
            ],
        },
    }


def test_normalize_holding_event_requires_exact_target_and_freezes_trigger_metadata():
    normalized = normalize_holding_event(_payload(), target=TARGET)

    assert isinstance(normalized, NormalizedHoldingEvent)
    assert normalized.event_id == "evt-1"
    assert normalized.revision == "7"
    assert normalized.action_list == (
        {"action": "record_added", "record_id": "rec-1"},
        {"action": "record_edited", "record_id": "rec-2"},
    )
    assert len(normalized.payload_digest) == 64

    with pytest.raises(
        HoldingEventTargetMismatch,
        match=r"^holding event target does not match configured app/base/table$",
    ):
        normalize_holding_event(_payload(table_id="another_table"), target=TARGET)


def test_normalize_holding_event_rejects_malformed_or_unbounded_transport():
    malformed = _payload()
    malformed["schema"] = "1.0"
    with pytest.raises(
        ValueError,
        match=r"^holding event must use Feishu event schema 2\.0$",
    ):
        normalize_holding_event(malformed, target=TARGET)

    too_many = _payload(
        actions=[
            {"action": "record_edited", "record_id": f"rec-{index}"}
            for index in range(MAX_EVENT_ACTIONS + 1)
        ]
    )
    with pytest.raises(
        ValueError,
        match=r"^holding event action_list exceeds the receiver limit$",
    ):
        normalize_holding_event(too_many, target=TARGET)

    malformed_foreign = _payload(table_id="another_table")
    malformed_foreign["schema"] = "1.0"
    with pytest.raises(ValueError, match="schema 2.0"):
        normalize_holding_event(malformed_foreign, target=TARGET)

    foreign_without_actions = _payload(table_id="another_table")
    foreign_without_actions["event"]["action_list"] = None
    with pytest.raises(HoldingEventTargetMismatch):
        normalize_holding_event(foreign_without_actions, target=TARGET)


def test_payload_digest_covers_full_delivery_not_only_frozen_actions():
    first = normalize_holding_event(_payload(), target=TARGET)
    changed = _payload()
    changed["header"]["create_time"] = "1785510000001"
    second = normalize_holding_event(changed, target=TARGET)

    assert first.action_list == second.action_list
    assert first.payload_digest != second.payload_digest
