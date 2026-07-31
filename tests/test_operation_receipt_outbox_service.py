from __future__ import annotations

from datetime import datetime, timedelta

from src.app.holdings_receipt_service import HoldingsReceiptService
from src.app.operation_receipt_outbox_service import (
    OperationReceiptDispatchStateUnknown,
    OperationReceiptOutboxService,
)
from src.app.operation_state_store import OperationStateStore


def _enqueue(store, key, receipt_type="holding_case_discovered"):
    store.enqueue_operation_receipt(
        receipt_key=key,
        receipt_type=receipt_type,
        payload={"case_key": key, "record_id": "rec-1", "field": "currency"},
    )


def test_dispatch_routes_by_receipt_type_and_persists_success(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    seen = []
    renderers = {}
    for receipt_type in HoldingsReceiptService.SUPPORTED_TYPES:
        renderers[receipt_type] = (
            lambda payload, resolved_type=receipt_type: (
                seen.append((resolved_type, payload["case_key"]))
                or {
                    "success": True,
                    "delivery_state": "accepted",
                    "message_id": f"msg-{resolved_type}",
                }
            )
        )
        _enqueue(store, f"key-{receipt_type}", receipt_type)

    result = OperationReceiptOutboxService(
        store=store,
        renderers=renderers,
    ).dispatch_pending()

    assert result["success"] is True
    assert result["sent"] == 3
    assert {item[0] for item in seen} == HoldingsReceiptService.SUPPORTED_TYPES
    assert all(
        store.get_operation_receipt(f"key-{receipt_type}")["status"] == "sent"
        for receipt_type in HoldingsReceiptService.SUPPORTED_TYPES
    )


def test_dispatch_unknown_is_not_automatically_retried(tmp_path):
    clock = [datetime(2026, 7, 31, 20, 0)]
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: clock[0],
    )
    _enqueue(store, "receipt-unknown")
    calls = []

    def uncertain(payload):
        calls.append(payload)
        return {
            "success": False,
            "delivery_state": "unknown",
            "error": "timeout after send",
        }

    service = OperationReceiptOutboxService(
        store=store,
        renderers={"holding_case_discovered": uncertain},
    )
    first = service.dispatch_pending()
    clock[0] += timedelta(days=1)
    second = service.dispatch_pending()

    assert first["unknown"] == 1
    assert second["attempted"] == 0
    assert len(calls) == 1
    assert store.get_operation_receipt("receipt-unknown")["status"] == "unknown"


def test_failed_before_acceptance_retries_only_when_due(tmp_path):
    clock = [datetime(2026, 7, 31, 20, 0)]
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: clock[0],
    )
    _enqueue(store, "receipt-failed")
    calls = []

    def renderer(payload):
        calls.append(payload)
        return {
            "success": False,
            "delivery_state": "failed",
            "error": "missing configuration",
        }

    service = OperationReceiptOutboxService(
        store=store,
        renderers={"holding_case_discovered": renderer},
    )
    assert service.dispatch_pending()["failed"] == 1
    assert service.dispatch_pending()["attempted"] == 0
    clock[0] += timedelta(minutes=1)
    assert service.dispatch_pending()["failed"] == 1
    assert len(calls) == 2


def test_two_dispatchers_cannot_claim_same_typed_receipt(tmp_path):
    path = tmp_path / "operations.sqlite3"
    first = OperationStateStore(path)
    second = OperationStateStore(path)
    _enqueue(first, "receipt-atomic")

    claimed = first.claim_due_operation_receipts(claim_id="worker-1")
    competing = second.claim_due_operation_receipts(claim_id="worker-2")

    assert [row["receipt_key"] for row in claimed] == ["receipt-atomic"]
    assert competing == []


def test_holdings_receipt_renderer_includes_frozen_manual_actions():
    discovery = HoldingsReceiptService.build_message(
        "holding_case_discovered",
        {
            "case_key": "case-1",
            "record_id": "rec-1",
            "account": "lx",
            "identity": {"asset_id": "AAPL", "broker": "IBKR"},
            "field": "currency",
            "state": "pending_confirmation",
            "current": "CNY",
            "proposed": "USD",
            "action": {
                "command": "pm holdings resolve --case-key case-1 --decision accept-proposed|keep-current --reason REASON --confirm"
            },
        },
    )
    attention = HoldingsReceiptService.build_message(
        "holding_case_attention_required",
        {
            "case_key": "case-1",
            "record_id": "rec-1",
            "account": "lx",
            "field": "currency",
            "action": {
                "command": "pm holdings recover --case-key case-1 --confirm"
            },
        },
    )

    assert "accept-proposed|keep-current" in discovery
    assert "冲突需要人工" not in discovery
    assert "pm holdings recover --case-key case-1 --confirm" in attention
    assert "禁止自动重试" in attention


def test_send_success_then_local_mark_failure_ages_to_unknown(tmp_path):
    clock = [datetime(2026, 7, 31, 20, 0)]

    class FailMarkStore(OperationStateStore):
        def mark_operation_receipt(self, **kwargs):
            raise RuntimeError("local mark failed")

    path = tmp_path / "operations.sqlite3"
    store = FailMarkStore(path, now_factory=lambda: clock[0])
    _enqueue(store, "receipt-mark-failed")
    service = OperationReceiptOutboxService(
        store=store,
        renderers={
            "holding_case_discovered": lambda _payload: {
                "success": True,
                "delivery_state": "accepted",
                "message_id": "msg-accepted",
            }
        },
    )

    try:
        service.dispatch_pending()
    except OperationReceiptDispatchStateUnknown as exc:
        assert exc.receipt_key == "receipt-mark-failed"
    else:
        raise AssertionError("expected local delivery-state failure")
    assert store.get_operation_receipt("receipt-mark-failed")["status"] == "sending"

    clock[0] += timedelta(minutes=6)
    restarted = OperationStateStore(path, now_factory=lambda: clock[0])
    assert restarted.claim_due_operation_receipts() == []
    assert restarted.get_operation_receipt("receipt-mark-failed")["status"] == "unknown"
