from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.app.cash_flow_event_completion_service import (
    CASH_FLOW_ATTENTION_RECEIPT_TYPE,
    CashFlowEventCompletionService,
)
from src.app.operation_state_store import OperationStateStore


class _CashFlowStorage:
    def __init__(
        self,
        *,
        currency="CNY",
        complete=False,
        missing=False,
        error=None,
        apply_error=None,
        nonconverging=False,
    ):
        self.currency = currency
        self.complete = complete
        self.missing = missing
        self.error = error
        self.apply_error = apply_error
        self.nonconverging = nonconverging
        self.calls = []
        self.row_overrides = {}

    def _row(self):
        rate = "1" if self.currency == "CNY" else "7.2"
        row = {
            "record_id": "rec-cf-1",
            "flow_date": "2026-07-31",
            "account": "lx",
            "broker": "manual",
            "amount": "100",
            "currency": self.currency,
            "exchange_rate": rate,
            "cny_amount": "100" if self.currency == "CNY" else "720",
            "generated_fingerprint": "fingerprint-1" if self.complete else None,
            "source": "manual",
            "updated_at": "2026-07-31T23:30:00",
            "status": "error" if self.error else "ok",
        }
        if self.error:
            row["error"] = self.error
        row.update(self.row_overrides)
        return row

    def reconcile_cash_flows(self, *, record_id, dry_run):
        self.calls.append({"record_id": record_id, "dry_run": dry_run})
        if self.missing:
            return {
                "success": True,
                "scanned": 0,
                "change_count": 0,
                "error_count": 0,
                "rows": [],
            }
        row = self._row()
        if dry_run:
            return {
                "success": True,
                "scanned": 1,
                "change_count": 0 if self.complete else 1,
                "error_count": 1 if self.error else 0,
                "rows": [row],
            }
        if self.apply_error:
            return {"success": False, "error": self.apply_error}
        if not self.nonconverging:
            self.complete = True
        return {"success": True, "updated_count": 1, "rows": [row]}


def _service(tmp_path, storage, *, locks=None):
    observed_locks = locks if locks is not None else []

    @contextmanager
    def lock_factory(key):
        observed_locks.append(key)
        yield

    return CashFlowEventCompletionService(
        storage=storage,
        operation_store=OperationStateStore(tmp_path / "operations.sqlite3"),
        lock_factory=lock_factory,
    )


def _trigger(event_id="evt-cf-1"):
    return {"event_id": event_id, "revision": "9"}


def _record_valid_fx(store):
    store.record_fx_confirmation(
        confirmation_id="fx-1",
        record_id="rec-cf-1",
        source_hash="fingerprint-1",
        exchange_rate="7.20",
        exchange_rate_date="2026-07-31",
        exchange_rate_source="provider:example",
        exchange_rate_evidence_type="provider",
        cny_amount="720.00",
        confirmation={"operator": "tester"},
    )


def test_cny_event_reconciles_exact_record_under_lock_and_reads_back(tmp_path):
    storage = _CashFlowStorage()
    locks = []
    service = _service(tmp_path, storage, locks=locks)

    result = service.process_record(record_id="rec-cf-1", trigger=_trigger())

    assert result == {
        "record_id": "rec-cf-1",
        "status": "completed",
        "currency": "CNY",
        "receipts": [],
        "updated_count": 1,
    }
    assert storage.calls == [
        {"record_id": "rec-cf-1", "dry_run": True},
        {"record_id": "rec-cf-1", "dry_run": True},
        {"record_id": "rec-cf-1", "dry_run": False},
        {"record_id": "rec-cf-1", "dry_run": True},
    ]
    assert locks == ["cash-flow-record-write:rec-cf-1"]


def test_self_write_event_is_silent_noop(tmp_path):
    storage = _CashFlowStorage(complete=True)

    result = _service(tmp_path, storage).process_record(
        record_id="rec-cf-1",
        trigger=_trigger("evt-self-write"),
    )

    assert result["status"] == "already_complete"
    assert result["receipts"] == []
    assert storage.calls == [{"record_id": "rec-cf-1", "dry_run": True}]


def test_deleted_record_is_audited_noop(tmp_path):
    storage = _CashFlowStorage(missing=True)

    result = _service(tmp_path, storage).process_record(
        record_id="rec-cf-1",
        trigger=_trigger(),
    )

    assert result == {
        "record_id": "rec-cf-1",
        "status": "stale_record_missing",
        "receipts": [],
    }


def test_invalid_manual_row_requires_attention_without_write(tmp_path):
    storage = _CashFlowStorage(error="amount is required")

    result = _service(tmp_path, storage).process_record(
        record_id="rec-cf-1",
        trigger=_trigger(),
    )

    assert result["status"] == "attention_required"
    receipt = result["receipts"][0]
    assert receipt["receipt_type"] == CASH_FLOW_ATTENTION_RECEIPT_TYPE
    assert receipt["payload"]["reason_code"] == "cash_flow_reconcile_error"
    assert "evt-cf-1" not in receipt["receipt_key"]
    assert storage.calls == [{"record_id": "rec-cf-1", "dry_run": True}]


def test_foreign_row_without_confirmation_requires_attention(tmp_path):
    storage = _CashFlowStorage(currency="USD")

    result = _service(tmp_path, storage).process_record(
        record_id="rec-cf-1",
        trigger=_trigger(),
    )

    assert result["status"] == "attention_required"
    assert result["reason_code"] == "fx_confirmation_missing"
    assert result["receipts"][0]["payload"]["fx_confirmation"] == {
        "state": "no_confirmation"
    }
    assert all(call["dry_run"] for call in storage.calls)


def test_matching_foreign_confirmation_allows_completed_foreign_row(tmp_path):
    storage = _CashFlowStorage(currency="USD", complete=True)
    service = _service(tmp_path, storage)
    _record_valid_fx(service.operation_store)

    result = service.process_record(record_id="rec-cf-1", trigger=_trigger())

    assert result["status"] == "already_complete"
    assert result["currency"] == "USD"
    assert all(call["dry_run"] is True for call in storage.calls)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generated_fingerprint", "fingerprint-changed"),
        ("amount", "101"),
    ],
)
def test_stale_foreign_confirmation_blocks_apply_after_manual_edit(
    tmp_path,
    field,
    value,
):
    storage = _CashFlowStorage(currency="USD")
    service = _service(tmp_path, storage)
    _record_valid_fx(service.operation_store)
    storage.row_overrides[field] = value
    if field == "amount":
        storage.row_overrides["cny_amount"] = "727.2"

    result = service.process_record(record_id="rec-cf-1", trigger=_trigger())

    assert result["reason_code"] == "fx_confirmation_stale"
    assert result["receipts"][0]["payload"]["error"] == (
        "foreign cash-flow FX confirmation is stale"
    )
    assert all(call["dry_run"] for call in storage.calls)


def test_remote_apply_failure_and_nonconvergence_remain_retryable(tmp_path):
    apply_failure = _CashFlowStorage(apply_error="timeout")
    with pytest.raises(RuntimeError, match="exact-record apply failed"):
        _service(tmp_path / "apply", apply_failure).process_record(
            record_id="rec-cf-1",
            trigger=_trigger(),
        )

    nonconverging = _CashFlowStorage(nonconverging=True)
    with pytest.raises(RuntimeError, match="did not converge"):
        _service(tmp_path / "readback", nonconverging).process_record(
            record_id="rec-cf-1",
            trigger=_trigger(),
        )


def test_attention_receipt_identity_is_semantic_not_event_or_generated_state():
    base = {
        "record_id": "rec-cf-1",
        "flow_date": "2026-07-31",
        "account": "lx",
        "broker": "manual",
        "amount": "100.00",
        "currency": "USD",
        "exchange_rate": "7.2",
        "updated_at": "first",
    }
    first = CashFlowEventCompletionService._attention_receipt(
        record_id="rec-cf-1",
        reason_code="fx_confirmation_missing",
        error="missing",
        row=base,
        confirmation=None,
    )
    generated_changed = CashFlowEventCompletionService._attention_receipt(
        record_id="rec-cf-1",
        reason_code="fx_confirmation_missing",
        error="wording changed",
        row={**base, "exchange_rate": "7.3", "updated_at": "second"},
        confirmation=None,
    )
    manual_changed = CashFlowEventCompletionService._attention_receipt(
        record_id="rec-cf-1",
        reason_code="fx_confirmation_missing",
        error="missing",
        row={**base, "amount": "101"},
        confirmation=None,
    )

    assert first["receipt_key"] == generated_changed["receipt_key"]
    assert first["payload"] == generated_changed["payload"]
    assert first["receipt_key"] != manual_changed["receipt_key"]
