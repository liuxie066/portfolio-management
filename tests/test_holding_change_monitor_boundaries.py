from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from unittest.mock import Mock

import pytest

from src.app.holding_change_monitor_service import HoldingChangeMonitorService, MonitorSettings
from src.app.holding_change_receipt import receipts, message
from src.app.operation_state_store import OperationStateStore
from src.feishu_client import FeishuClient
from src.app import operation_state
from tests.test_holding_change_monitor import raw, Storage


@pytest.mark.parametrize("text, expected", [
    ("1e30", "1000000000000000000000000000000"),
    ("123456789012345678901234567890.12500", "123456789012345678901234567890.125"),
    ("0.123456789012345678901234567890123400", "0.1234567890123456789012345678901234"),
    ("100.00", "100"), ("-0.00", "0"),
])
@pytest.mark.parametrize("precision", [6, 28])
def test_shared_numeric_canonicalization_preserves_value(text, expected, precision):
    from src.app.holdings_validation import canonical_record_payload
    from src.domain.holding_values import canonical_holding_number

    with localcontext() as context:
        context.prec = precision
        assert canonical_holding_number(Decimal(text)) == expected
        payload = canonical_record_payload({"quantity": text, "avg_cost": text})
        assert payload["quantity"] == expected
        assert payload["avg_cost"] == expected


@pytest.mark.parametrize("response", [
    {}, {"items": []}, {"has_more": False}, {"items": [], "has_more": None},
    {"items": [], "has_more": True},
    {"items": [{"record_id": "r", "fields": []}], "has_more": False},
    {"items": [{"record_id": "", "fields": {}}], "has_more": False},
])
def test_strict_full_read_rejects_unproven_empty_or_invalid_rows(response):
    client = FeishuClient.__new__(FeishuClient)
    client._get_table_config = Mock(return_value=("base", "table"))
    client._request = Mock(return_value=response)
    with pytest.raises(RuntimeError):
        client.list_records("holdings", require_complete=True)


def test_strict_full_read_accepts_verified_empty_and_all_pages():
    client = FeishuClient.__new__(FeishuClient)
    client._get_table_config = Mock(return_value=("base", "table"))
    client._request = Mock(return_value={"items": [], "has_more": False})
    assert client.list_records("holdings", require_complete=True) == []
    client._request.side_effect = [
        {"items": [{"record_id": "a", "fields": {}}], "has_more": True, "page_token": "p"},
        {"items": [{"record_id": "b", "fields": {}}], "has_more": False},
    ]
    assert len(client.list_records("holdings", require_complete=True)) == 2


def test_repeated_record_across_pages_is_not_complete():
    client = FeishuClient.__new__(FeishuClient)
    client._get_table_config = Mock(return_value=("base", "table"))
    client._request = Mock(side_effect=[
        {"items": [{"record_id": "a", "fields": {}}], "has_more": True, "page_token": "p"},
        {"items": [{"record_id": "a", "fields": {}}], "has_more": False},
    ])
    with pytest.raises(RuntimeError, match="repeated record_id"):
        client.list_records("holdings", require_complete=True)


def service(tmp_path, now, *, enabled=True):
    store = OperationStateStore(tmp_path / "operations.sqlite3", now_factory=lambda: now[0])
    storage = Storage([raw()])
    result = HoldingChangeMonitorService(storage=storage, store=store, settings=MonitorSettings(("example",), enabled),
                                         target=("base", "table"), dispatcher=Mock())
    result.initialize(apply=True, confirm=True)
    store.configure_holding_monitor(result.scope_key, settings=result.settings.scope_settings(), enabled=enabled)
    return result


def test_event_dirty_is_atomic_deduplicated_and_preserves_running_generation(tmp_path, monkeypatch):
    now = [datetime(2026, 11, 6, 10)]
    monitor = service(tmp_path, now)
    monkeypatch.setattr("src.config.get", lambda key, default=None: {"enabled": True, "coalesce_seconds": 2} if key == "holdings.change_monitor" else default)
    event = dict(event_id="event1", event_type="record-change", file_token="base", table_id="table",
                 revision=None, action_list=[{"action": "record_deleted", "record_id": "r1"}], payload_digest="digest")
    assert monitor.store.accept_holding_event(**event)
    first = monitor._state()
    assert first["requested"] == 1
    assert not monitor.store.accept_holding_event(**event)
    assert monitor._state()["requested"] == 1
    now[0] += timedelta(seconds=2)
    original = monitor.storage.get_raw_holdings

    def receive_during_query(**kwargs):
        monitor.store.accept_holding_event(**{**event, "event_id": "event2"})
        return original(**kwargs)

    monitor.storage.get_raw_holdings = receive_during_query
    result = monitor.scan(apply=True, confirm=True, mode="background", due_only=True)
    assert result["success"], result
    state = monitor._state()
    assert state["requested"] == 2 and state["completed"] == 1
    assert state["next_due"] <= (now[0] + timedelta(seconds=2)).isoformat()


def test_background_backoff_and_manual_success_do_not_hide_failure(tmp_path):
    now = [datetime(2026, 11, 6, 10)]
    monitor = service(tmp_path, now)
    monitor.storage.get_raw_holdings = Mock(side_effect=RuntimeError("network failure"))
    assert not monitor.scan(apply=True, confirm=True, mode="background")["success"]
    failed = monitor._state()
    assert failed["next_due"] == (now[0] + timedelta(seconds=2)).isoformat()
    monitor.storage.get_raw_holdings = Mock(return_value=[raw()])
    assert monitor.scan(apply=True, confirm=True)["success"]
    state = monitor._state()
    assert state["next_due"] == failed["next_due"]
    assert state["background_error"] == "network failure"
    assert state["background_success_at"] is None


def test_duplicate_case_failure_rolls_back_gap_and_generation(tmp_path, monkeypatch):
    now = [datetime(2026, 11, 6, 10)]
    monitor = service(tmp_path, now)
    old = monitor._state()
    monitor.storage.rows = [raw(), raw("r2")]
    monkeypatch.setattr(operation_state.OperationStateStore, "_reconcile_duplicate_cases_tx", Mock(side_effect=RuntimeError("case write failed")))
    assert not monitor.scan(apply=True, confirm=True)["success"]
    state = monitor._state()
    assert state["rows"] == old["rows"]
    assert state["completed"] == old["completed"]
    assert monitor.store.list_holding_cases(record_id="r1", limit=None) == []


def test_receipt_chunks_are_frozen_and_delete_is_not_zero():
    change = {"kind": "record_deleted", "record_id": "r1", "before": raw().raw_fields,
              "after": None, "previous_observed_at": "2026-11-06T10:00:00", "observed_at": "2026-11-06T10:01:00"}
    result = receipts("scope", "epoch", 1, [deepcopy(change) for _ in range(11)])
    assert len(result) == 2
    assert result[0]["payload"]["parts"] == 2
    markdown = message(result[0]["payload"])
    assert "记录已删除" in markdown and "不存在" in markdown
    assert "卖出" not in markdown
