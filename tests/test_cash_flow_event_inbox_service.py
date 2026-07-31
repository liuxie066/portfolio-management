from __future__ import annotations

from datetime import datetime, timedelta
import threading

import pytest

from src.app.cash_flow_event_completion_service import CashFlowEventCompletionService
from src.app.cash_flow_event_inbox_service import CashFlowEventInboxService
from src.app.cash_flow_event_service import CASH_FLOW_EVENT_TYPE, CashFlowEventTarget
from src.app.operation_state_store import OperationStateStore


TARGET = CashFlowEventTarget("cli_data", "base_portfolio", "tbl_cash_flow")


def _payload(event_id="evt-cf-1", *, actions=None, table_id="tbl_cash_flow"):
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
            "revision": "9",
            "action_list": actions
            or [{"action": "record_edited", "record_id": "rec-cf-1"}],
        },
    }


def _service(
    tmp_path,
    *,
    handler=None,
    clock=None,
    monotonic=None,
    terminal_failure_receipt_factory=None,
):
    now = clock or [datetime(2026, 7, 31, 23, 30)]
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: now[0],
    )
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return CashFlowEventInboxService(
        store=store,
        record_handler=handler,
        terminal_failure_receipt_factory=terminal_failure_receipt_factory,
        target=TARGET,
        **kwargs,
    )


def test_callback_only_durably_accepts_deduplicates_and_filters(tmp_path):
    calls = []
    service = _service(tmp_path, handler=lambda **kwargs: calls.append(kwargs))

    accepted = service.accept(_payload())
    duplicate = service.accept(_payload())
    filtered = service.accept(_payload("evt-other", table_id="tbl_holdings"))

    assert accepted["inserted"] is True
    assert duplicate["duplicate"] is True
    assert filtered["filtered"] is True
    assert calls == []


def test_callback_rejects_event_id_collision_and_preserves_first_payload(tmp_path):
    service = _service(tmp_path, handler=lambda **_kwargs: {})
    service.accept(_payload())
    changed = _payload()
    changed["header"]["create_time"] = "1785510000001"

    with pytest.raises(ValueError, match="id collision"):
        service.accept(changed)

    assert service.store.get_cash_flow_event("evt-cf-1")["state"] == "pending"


def test_callback_raises_after_durable_acceptance_when_budget_is_exceeded(tmp_path):
    ticks = iter((0.0, 2.1))
    service = _service(
        tmp_path,
        handler=lambda **_kwargs: {},
        monotonic=lambda: next(ticks),
    )

    with pytest.raises(TimeoutError, match="receiver budget"):
        service.accept(_payload())
    assert service.store.get_cash_flow_event("evt-cf-1")["state"] == "pending"


def test_worker_routes_exact_records_and_audits_deleted_actions(tmp_path):
    calls = []

    def handler(*, record_id, trigger):
        calls.append((record_id, trigger))
        return {"record_id": record_id, "status": "shell_processed"}

    service = _service(tmp_path, handler=handler)
    service.accept(
        _payload(
            actions=[
                {"action": "record_deleted", "record_id": "rec-old"},
                {"action": "record_edited", "record_id": "rec-b"},
                {"action": "record_added", "record_id": "rec-a"},
                {"action": "record_edited", "record_id": "rec-b"},
            ]
        )
    )

    result = service.process_due()

    assert result["success"] is True
    assert [item[0] for item in calls] == ["rec-a", "rec-b"]
    event = service.store.get_cash_flow_event("evt-cf-1")
    assert event["state"] == "processed"
    assert event["outcome"]["record_ids"] == ["rec-a", "rec-b"]
    assert event["outcome"]["ignored_actions"] == [
        {"action": "record_deleted", "record_id": "rec-old"}
    ]


def test_worker_retries_handler_failure_and_recovers_after_due_time(tmp_path):
    clock = [datetime(2026, 7, 31, 23, 30)]
    ready = [False]

    def handler(*, record_id, trigger):
        if not ready[0]:
            raise TimeoutError("temporary Feishu read failure")
        return {"record_id": record_id, "status": "recovered"}

    service = _service(tmp_path, handler=handler, clock=clock)
    service.accept(_payload())

    failed = service.process_due()
    assert failed["success"] is False
    assert service.store.get_cash_flow_event("evt-cf-1")["state"] == "failed_retryable"
    assert service.process_due()["claimed"] == 0

    ready[0] = True
    clock[0] += timedelta(minutes=1)
    recovered = service.process_due()

    assert recovered["success"] is True
    assert service.store.get_cash_flow_event("evt-cf-1")["state"] == "processed"


def test_fourth_handler_failure_atomically_enqueues_attention_and_stops(tmp_path):
    clock = [datetime(2026, 7, 31, 23, 30)]

    def fail(**_kwargs):
        raise TimeoutError("persistent Feishu read failure")

    service = _service(
        tmp_path,
        handler=fail,
        clock=clock,
        terminal_failure_receipt_factory=(
            CashFlowEventCompletionService.terminal_failure_receipts
        ),
    )
    service.accept(_payload())

    for retry_minutes in (1, 5, 15):
        result = service.process_due()
        assert result["failed"] == 1
        assert result["processed"] == 0
        clock[0] += timedelta(minutes=retry_minutes)

    terminal = service.process_due()
    event = service.store.get_cash_flow_event("evt-cf-1")

    assert terminal["success"] is True
    assert terminal["processed"] == 1
    assert terminal["failed"] == 0
    assert event["state"] == "processed"
    assert event["attempt_count"] == 4
    assert event["outcome"]["status"] == "attention_required"
    assert len(event["outcome"]["receipt_keys"]) == 1
    receipt = service.store.get_operation_receipt(
        event["outcome"]["receipt_keys"][0]
    )
    assert receipt["receipt_type"] == "cash_flow_reconcile_attention_required"
    assert receipt["payload"]["reason_code"] == "event_processing_failed"
    clock[0] += timedelta(days=1)
    assert service.process_due()["claimed"] == 0


def test_read_only_status_does_not_create_operation_state(tmp_path):
    db_path = tmp_path / "missing" / "operations.sqlite3"

    result = OperationStateStore.inspect_cash_flow_event_status(db_path)

    assert result["initialized"] is False
    assert result["db_path"] == str(db_path)
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_worker_loop_logs_cycle_failure_and_keeps_running(tmp_path, capsys):
    service = _service(tmp_path, handler=lambda **_kwargs: {})
    stop_event = threading.Event()
    calls = []

    def process_due(*, limit):
        calls.append(limit)
        if len(calls) == 1:
            raise RuntimeError("temporary SQLite failure")
        stop_event.set()
        return {"success": True}

    service.process_due = process_due
    service.run_worker_loop(stop_event=stop_event, poll_seconds=0.01, limit=7)

    assert calls == [7, 7]
    assert "temporary SQLite failure" in capsys.readouterr().err
