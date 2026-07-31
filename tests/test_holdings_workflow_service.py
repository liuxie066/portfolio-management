from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.app.holdings_reconciliation_service import HoldingsReconciliationService
from src.app.holdings_workflow_service import HoldingsWorkflowService
from src.app.operation_state_store import OperationStateStore
from src.domain.holdings import RawHoldingRecord


class _Storage:
    def __init__(self, *, currency="", patch_mode="success"):
        self.fields = {
            "asset_id": "AAPL",
            "asset_name": "Apple",
            "asset_type": "us_stock",
            "account": "lx",
            "broker": "IBKR",
            "quantity": 1,
            "currency": currency,
            "asset_class": "美国资产",
        }
        self.patch_mode = patch_mode
        self.patch_calls = []
        self.read_calls = []

    def get_raw_holdings(self, *, account=None, record_id=None):
        self.read_calls.append({"account": account, "record_id": record_id})
        if account is not None and account != self.fields.get("account"):
            return []
        if record_id is not None and record_id != "rec-1":
            return []
        return [RawHoldingRecord("rec-1", dict(self.fields))]

    def patch_holding_record(self, *, record_id, fields):
        self.patch_calls.append((record_id, dict(fields)))
        if self.patch_mode == "timeout_after_apply":
            self.fields.update(fields)
            raise TimeoutError("timeout after server accepted patch")
        if self.patch_mode == "no_apply_error":
            raise TimeoutError("transport outcome unavailable")
        if self.patch_mode == "mismatch":
            self.fields.update(fields)
            target_field = next(iter(fields))
            self.fields[target_field] = "EUR"
            return RawHoldingRecord("rec-1", dict(self.fields))
        self.fields.update(fields)
        return RawHoldingRecord("rec-1", dict(self.fields))


def _workflow(tmp_path, storage, lock_events=None):
    @contextmanager
    def lock(key):
        if lock_events is not None:
            lock_events.append(("enter", key))
        try:
            yield
        finally:
            if lock_events is not None:
                lock_events.append(("exit", key))

    reconciliation = HoldingsReconciliationService(storage=storage)
    return HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(tmp_path / "operations.sqlite3"),
        reconciliation=reconciliation,
        lock_factory=lock,
    )


def _operator():
    return {
        "username": "tester",
        "hostname": "host",
        "command_mode": "test",
        "trusted_identity": False,
    }


def test_notify_materializes_one_case_without_remote_holding_write(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)

    result = service.notify(record_id="rec-1")

    assert storage.patch_calls == []
    assert result["workflow"]["created_case_keys"]
    cases = service.list_cases()["cases"]
    assert [(item["field"], item["state"]) for item in cases] == [
        ("currency", "pending_apply")
    ]
    receipt = service.store.get_operation_receipt(
        f"holdings:case:discovered:{cases[0]['case_key']}"
    )
    assert "--record-id rec-1 --apply --confirm" in receipt["payload"]["action"]["command"]


def test_missing_apply_is_exact_and_locks_account_before_record(tmp_path):
    storage = _Storage()
    locks = []
    service = _workflow(tmp_path, storage, lock_events=locks)

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is True
    assert storage.fields["currency"] == "USD"
    assert storage.patch_calls == [("rec-1", {"currency": "USD"})]
    assert locks[:2] == [
        ("enter", "account-write:lx"),
        ("enter", "holding-record-write:rec-1"),
    ]
    case = next(iter(service.store.list_holding_cases()))
    assert case["state"] == "resolved_accept"
    assert case["remote_attempt_started_at"] is not None

    second = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert second["status"] == "no_eligible_missing_fields"
    assert len(storage.patch_calls) == 1


def test_timeout_with_actual_success_is_resolved_by_fresh_readback(tmp_path):
    storage = _Storage(patch_mode="timeout_after_apply")
    service = _workflow(tmp_path, storage)

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is True
    assert result["patch_error"] == "timeout after server accepted patch"
    assert next(iter(service.store.list_holding_cases()))["state"] == "resolved_accept"


def test_attempt_with_exact_before_value_becomes_unknown_and_never_retries(tmp_path):
    storage = _Storage(patch_mode="no_apply_error")
    service = _workflow(tmp_path, storage)

    first = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    case_key = next(iter(first["case_states"]))
    assert first["case_states"][case_key] == "apply_outcome_unknown"
    assert len(storage.patch_calls) == 1

    with pytest.raises(ValueError, match="not applicable"):
        service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert len(storage.patch_calls) == 1

    storage.fields["currency"] = "USD"
    recovered = service.recover(case_key=case_key, confirmed_operator=_operator())
    assert recovered["status"] == "resolved_accept"
    assert service.store.get_holding_case(case_key)["state"] == "resolved_accept"


def test_mismatching_readback_supersedes_case(tmp_path):
    storage = _Storage(patch_mode="mismatch")
    service = _workflow(tmp_path, storage)

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is False
    assert next(iter(result["case_states"].values())) == "superseded"


def test_conflict_keep_current_requires_reason_and_exact_scope(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    notified = service.notify(record_id="rec-1")
    case_key = notified["workflow"]["created_case_keys"][0]

    with pytest.raises(ValueError, match="nonblank reason"):
        service.resolve(
            case_key=case_key,
            decision="keep-current",
            reason=" ",
            confirmed_operator=_operator(),
        )
    storage.fields["currency"] = "EUR"
    with pytest.raises(ValueError, match="scope changed"):
        service.resolve(
            case_key=case_key,
            decision="keep-current",
            reason="manual exception",
            confirmed_operator=_operator(),
        )
    assert storage.patch_calls == []


def test_conflict_keep_current_closes_without_feishu_write(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]

    result = service.resolve(
        case_key=case_key,
        decision="keep-current",
        reason="broker statement confirmed CNY",
        confirmed_operator=_operator(),
    )

    assert result["status"] == "resolved_keep"
    assert result["wrote_holdings"] is False
    assert storage.patch_calls == []
    assert service.store.get_holding_case(case_key)["state"] == "resolved_keep"

    storage.fields["currency"] = "EUR"
    changed = service.notify(record_id="rec-1")
    assert changed["workflow"]["superseded_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "superseded"
    new_cases = [
        item
        for item in service.store.list_holding_cases()
        if item["case_key"] != case_key
    ]
    assert len(new_cases) == 1
    assert new_cases[0]["state"] == "pending_confirmation"


def test_repeat_keep_current_rechecks_fresh_scope_before_deduplication(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    service.resolve(
        case_key=case_key,
        decision="keep-current",
        reason="statement evidence",
        confirmed_operator=_operator(),
    )
    storage.fields["currency"] = "EUR"

    with pytest.raises(ValueError, match="scope changed"):
        service.resolve(
            case_key=case_key,
            decision="keep-current",
            reason="repeat",
            confirmed_operator=_operator(),
        )


def test_manual_repair_is_proved_by_fresh_notify_and_resolved_external(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    storage.fields["currency"] = "USD"

    result = service.notify(record_id="rec-1")

    assert result["workflow"]["closed_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "resolved_external"
    assert storage.patch_calls == []

    storage.fields["currency"] = ""
    reopened = service.notify(record_id="rec-1")
    assert reopened["workflow"]["reopened_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "pending_apply"
    with service.store._connect() as conn:
        discovery_count = conn.execute(
            """
            SELECT COUNT(*) FROM operation_receipt_outbox
            WHERE receipt_key = ?
            """,
            (f"holdings:case:discovered:{case_key}",),
        ).fetchone()[0]
    assert discovery_count == 1

    storage.fields["currency"] = "USD"
    closed_again = service.notify(
        record_id="rec-1",
        trigger={"mode": "event_validate_notify", "event_id": "event-2"},
    )
    assert closed_again["workflow"]["closed_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "resolved_external"


def test_manual_repair_with_identity_change_is_superseded_not_resolved_external(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    storage.fields["currency"] = "USD"
    storage.fields["asset_id"] = "MSFT"

    result = service.notify(record_id="rec-1")

    assert result["workflow"]["closed_case_keys"] == []
    assert result["workflow"]["superseded_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "superseded"


def test_apply_fresh_scan_closes_other_manually_repaired_case(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    currency_case = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    storage.fields["currency"] = "USD"
    storage.fields["asset_class"] = ""

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is True
    assert result["workflow"]["closed_case_keys"] == [currency_case]
    assert service.store.get_holding_case(currency_case)["state"] == "resolved_external"
    assert storage.patch_calls[-1] == ("rec-1", {"asset_class": "美国资产"})


def test_recovery_that_still_reads_before_remains_unknown_without_resend(tmp_path):
    storage = _Storage(patch_mode="no_apply_error")
    service = _workflow(tmp_path, storage)
    first = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    case_key = next(iter(first["case_states"]))

    recovered = service.recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "apply_outcome_unknown"
    with service.store._connect() as conn:
        attention = conn.execute(
            "SELECT * FROM operation_receipt_outbox WHERE receipt_type = 'holding_case_attention_required'"
        ).fetchall()
    assert len(attention) == 1


def test_recover_rejects_case_that_never_started_apply(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]

    with pytest.raises(ValueError, match="no recoverable apply attempt"):
        service.recover(case_key=case_key, confirmed_operator=_operator())

    assert service.store.get_holding_case(case_key)["state"] == "pending_apply"


@pytest.mark.parametrize(
    ("field", "value"),
    (("account", "sy"), ("asset_id", "MSFT"), ("broker", "OTHER")),
)
def test_recovery_supersedes_when_frozen_identity_drifted(tmp_path, field, value):
    storage = _Storage(patch_mode="no_apply_error")
    service = _workflow(tmp_path, storage)
    first = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    case_key = next(iter(first["case_states"]))
    storage.fields["currency"] = "USD"
    storage.fields[field] = value

    recovered = service.recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "superseded"
    assert service.store.get_holding_case(case_key)["state"] == "superseded"


def test_remote_success_then_local_finalize_failure_recovers_without_second_patch(tmp_path):
    class FailFinalizeStore(OperationStateStore):
        def finalize_holding_cases(self, **kwargs):
            raise RuntimeError("local finalize unavailable")

    storage = _Storage()
    path = tmp_path / "operations.sqlite3"
    failing_store = FailFinalizeStore(path)
    service = HoldingsWorkflowService(
        storage=storage,
        store=failing_store,
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    )
    with pytest.raises(RuntimeError, match="local finalize"):
        service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert storage.fields["currency"] == "USD"
    assert len(storage.patch_calls) == 1
    case_key = OperationStateStore(path).list_holding_cases()[0]["case_key"]

    recovered = HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(path),
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    ).recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "resolved_accept"
    assert len(storage.patch_calls) == 1


def test_local_failure_before_remote_attempt_recovers_as_retryable(tmp_path):
    class FailRemoteMarkerStore(OperationStateStore):
        def mark_holding_remote_attempt(self, **kwargs):
            raise RuntimeError("local marker unavailable")

    storage = _Storage()
    path = tmp_path / "operations.sqlite3"
    service = HoldingsWorkflowService(
        storage=storage,
        store=FailRemoteMarkerStore(path),
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    )
    with pytest.raises(RuntimeError, match="local marker"):
        service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert storage.patch_calls == []
    case_key = OperationStateStore(path).list_holding_cases()[0]["case_key"]

    recovered = HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(path),
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    ).recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "failed_retryable"
    assert storage.patch_calls == []


@contextmanager
def _null_lock():
    yield
