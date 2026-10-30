from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from src.app.futu_quantity_receipt import (
    RECEIPT_TYPE, quantity_change_message, quantity_change_payload,
)
from src.app.holdings_receipt_service import HoldingsReceiptService
from src.app.operation_receipt_outbox_service import OperationReceiptOutboxService
from src.app.operation_state import OperationStateStore


def written_result():
    return {
        "success": True, "status": "written", "dry_run": False,
        "source": "futu-openapi", "account": "alice",
        "partial_write_possible": False, "receipt_persisted": True,
        "sync_run_id": "sync-new", "source_snapshot_id": "snapshot-new",
        "source_metadata": {"observed_at_utc": "2026-10-30T01:00:00Z"},
        "reconciliation": {"status": "trusted"},
        "positions": [{"asset_id": "EXAMPLE", "security_type": "STOCK",
                       "current_quantity": 50.0, "target_quantity": 51.0,
                       "quantity_changed": True}],
    }


@pytest.mark.parametrize("before,after,security_type", [(50, 51, "STOCK"), (0, 10, "ETF"), (51, 0, "STOCK"), (0.5, 0.75, "STOCK")])
def test_projects_actual_quantity_change_with_frozen_source(before, after, security_type):
    result = written_result()
    result["positions"][0].update(current_quantity=before, target_quantity=after, security_type=security_type)
    payload = quantity_change_payload(result)
    assert payload == {
        "account": "alice", "sync_run_id": "sync-new",
        "source_snapshot_id": "snapshot-new", "observed_at_utc": "2026-10-30T01:00:00Z",
        "changes": [{"asset_id": "EXAMPLE", "before": before, "after": after}],
    }
    result["positions"][0]["target_quantity"] = 999
    assert payload["changes"][0]["after"] == after
    assert quantity_change_message(payload) == (
        "# PM · 回执 · alice\n\n类型｜富途持仓同步\n状态｜✅ 成功\n"
        f"持仓变化｜EXAMPLE：数量 {before}→{after}"
    )


@pytest.mark.parametrize("override", [
    {"success": False}, {"success": 1}, {"status": "dry_run"}, {"dry_run": True},
    {"source": "manual-observation"}, {"partial_write_possible": True},
    {"receipt_persisted": False}, {"reconciliation": {"status": "untrusted"}},
    {"reconciliation": {"status": "unavailable"}}, {"reconciliation": None},
])
def test_failed_unverified_and_preview_results_stay_silent(override):
    result = written_result()
    result.update(override)
    assert quantity_change_payload(result) is None


def test_unchanged_cost_and_mmf_only_refreshes_stay_silent():
    result = written_result()
    result["positions"][0].update(quantity_changed=False, target_quantity=50, cost_changed=True)
    result["cash_mmf"] = {"updated": 1}
    assert quantity_change_payload(result) is None
    result["positions"] = []
    assert quantity_change_payload(result) is None
    result["positions"] = [{"security_type": "OPTION", "quantity_changed": True}]
    assert quantity_change_payload(result) is None


def test_multiple_changes_render_in_one_existing_channel_post():
    payload = quantity_change_payload(written_result())
    payload["changes"].append({"asset_id": "EXAMPLE2", "before": 1, "after": 0})
    sent = Mock(return_value={"message_id": "message-fake"})
    sender = HoldingsReceiptService(app_id="fake-app", app_secret="fake-secret", open_id="fake-destination",
                                    client_factory=lambda **_kwargs: Mock(send_post_message=sent))
    result = sender.send(RECEIPT_TYPE, payload)
    assert result["delivery_state"] == "accepted"
    sent.assert_called_once_with(open_id="fake-destination", markdown=(
        "# PM · 回执 · alice\n\n类型｜富途持仓同步\n状态｜✅ 成功\n"
        "持仓变化｜EXAMPLE：数量 50→51；EXAMPLE2：数量 1→0"
    ))


@pytest.mark.parametrize("failure", ["config", "unknown"])
def test_delivery_failures_remain_visible_without_resync_or_blind_unknown_retry(tmp_path, failure):
    clock = [datetime(2026, 10, 30)]
    store = OperationStateStore(tmp_path / "ops.db", now_factory=lambda: clock[0])
    payload = quantity_change_payload(written_result())
    store.enqueue_operation_receipt(receipt_key="change", receipt_type=RECEIPT_TYPE, payload=payload)
    sent = Mock(side_effect=TimeoutError("ambiguous fake send"))
    sender = HoldingsReceiptService(app_id="fake-app", app_secret="" if failure == "config" else "fake-secret",
                                    open_id="fake-destination", client_factory=lambda **_kwargs: Mock(send_post_message=sent))
    outbox = OperationReceiptOutboxService(store=store, holdings_sender=sender)
    first = outbox.dispatch_pending()
    expected = "failed" if failure == "config" else "unknown"
    assert first[expected] == 1
    persisted = store.get_operation_receipt("change")
    assert persisted["status"] == expected and persisted["last_error"]
    assert persisted["payload"] == payload
    clock[0] += timedelta(minutes=2)
    assert outbox.dispatch_pending()["attempted"] == (1 if failure == "config" else 0)
    assert sent.call_count == (0 if failure == "config" else 1)


def test_new_type_routes_through_default_dispatcher_and_is_not_sent_twice(tmp_path):
    store = OperationStateStore(tmp_path / "ops.db")
    payload = quantity_change_payload(written_result())
    store.enqueue_operation_receipt(receipt_key="change", receipt_type=RECEIPT_TYPE, payload=payload)
    sent = Mock(return_value={"message_id": "fake-message"})
    sender = HoldingsReceiptService(app_id="fake", app_secret="fake", open_id="fake",
                                    client_factory=lambda **_kwargs: Mock(send_post_message=sent))
    assert OperationReceiptOutboxService(store=store, holdings_sender=sender).dispatch_pending()["sent"] == 1
    restarted = OperationStateStore(store.db_path)
    assert OperationReceiptOutboxService(store=restarted, holdings_sender=sender).dispatch_pending()["attempted"] == 0
    sent.assert_called_once()


def test_projection_does_not_reuse_a_previous_result():
    changed = written_result()
    unchanged = deepcopy(changed)
    unchanged["positions"][0].update(current_quantity=51, quantity_changed=False)
    assert quantity_change_payload(changed)
    assert quantity_change_payload(unchanged) is None
