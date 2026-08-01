from __future__ import annotations

from datetime import datetime, timedelta
import threading
from types import SimpleNamespace

from src.app.holding_event_inbox_service import HoldingEventInboxService
from src.app.holdings_event_service import HOLDINGS_EVENT_TYPE, HoldingsEventTarget
from src.app.holdings_reconciliation_service import HoldingsReconciliationService
from src.app.holdings_workflow_service import HoldingsWorkflowService
from src.app.operation_state_store import OperationStateStore
from src.domain.holdings import RawHoldingRecord


TARGET = HoldingsEventTarget("cli_data", "base_holdings", "tbl_holdings")


def _payload(event_id="evt-1", *, actions=None, table_id="tbl_holdings"):
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
            "revision": "7",
            "action_list": actions
            or [{"action": "record_edited", "record_id": "rec-1"}],
        },
    }


class _Storage:
    def __init__(self, *, exists=True, fail=False, currency=""):
        self.exists = exists
        self.fail = fail
        self.currency = currency
        self.read_calls = []
        self.patch_calls = []

    def get_raw_holdings(self, *, account=None, record_id=None):
        self.read_calls.append({"account": account, "record_id": record_id})
        if self.fail:
            raise TimeoutError("Feishu read unavailable")
        if not self.exists or record_id != "rec-1":
            return []
        return [
            RawHoldingRecord(
                "rec-1",
                {
                    "asset_id": "AAPL",
                    "asset_name": "Apple",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": 1,
                    "currency": self.currency,
                    "asset_class": "美国资产",
                },
            )
        ]

    def patch_holding_record(self, *, record_id, fields):
        self.patch_calls.append((record_id, fields))
        raise AssertionError("event worker must not write holdings")


def _service(tmp_path, storage, *, workflow=None, monotonic=None):
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: datetime(2026, 7, 31, 21, 0),
    )
    if workflow is None:
        workflow = HoldingsWorkflowService(
            storage=storage,
            store=store,
            reconciliation=HoldingsReconciliationService(storage=storage),
        )
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return HoldingEventInboxService(
        storage=storage,
        store=store,
        workflow=workflow,
        target=TARGET,
        **kwargs,
    )


def test_callback_only_durably_accepts_and_filters_other_tables(tmp_path):
    storage = _Storage()
    service = _service(tmp_path, storage)

    accepted = service.accept(_payload())
    duplicate = service.accept(_payload())
    filtered = service.accept(_payload("evt-other", table_id="another"))

    assert accepted["inserted"] is True
    assert duplicate["duplicate"] is True
    assert filtered["filtered"] is True
    assert storage.read_calls == []
    assert storage.patch_calls == []


def test_callback_raises_when_durable_acceptance_exceeds_budget(tmp_path):
    ticks = iter((0.0, 2.1))
    service = _service(tmp_path, _Storage(), monotonic=lambda: next(ticks))

    try:
        service.accept(_payload())
    except TimeoutError as exc:
        assert "receiver budget" in str(exc)
    else:
        raise AssertionError("receiver budget overrun must remain SDK-visible")
    assert service.store.get_holding_event("evt-1")["state"] == "pending"


def test_worker_fresh_reads_exact_record_materializes_case_and_never_writes_holding(tmp_path):
    storage = _Storage()
    service = _service(tmp_path, storage)
    service.accept(_payload())

    result = service.process_due()

    assert result["success"] is True
    assert storage.read_calls == [{"account": None, "record_id": "rec-1"}]
    assert storage.patch_calls == []
    cases = service.store.list_holding_cases()
    assert [(item["field"], item["state"]) for item in cases] == [
        ("currency", "pending_apply")
    ]
    event = service.store.get_holding_event("evt-1")
    assert event["state"] == "processed"
    assert event["outcome"]["record_statuses"] == {"rec-1": "validated"}
    assert event["outcome"]["workflow"]["created_case_keys"] == [cases[0]["case_key"]]
    receipt = service.store.get_operation_receipt(
        f"holdings:case:discovered:{cases[0]['case_key']}"
    )
    assert receipt["payload"]["trigger"] == {
        "mode": "event_validate_notify",
        "event_id": "evt-1",
        "event_type": HOLDINGS_EVENT_TYPE,
        "file_token": "base_holdings",
        "table_id": "tbl_holdings",
        "record_id": "rec-1",
        "actions": ["record_edited"],
        "revision": "7",
    }


def test_worker_treats_deleted_and_disappearing_records_as_audited_noops(tmp_path):
    deleted = _service(tmp_path / "deleted", _Storage())
    deleted.accept(
        _payload(actions=[{"action": "record_deleted", "record_id": "rec-1"}])
    )
    assert deleted.process_due()["success"] is True
    deleted_event = deleted.store.get_holding_event("evt-1")
    assert deleted_event["outcome"]["ignored_actions"] == [
        {"action": "record_deleted", "record_id": "rec-1"}
    ]

    missing = _service(tmp_path / "missing", _Storage(exists=False))
    missing.accept(_payload())
    assert missing.process_due()["success"] is True
    missing_event = missing.store.get_holding_event("evt-1")
    assert missing_event["outcome"]["record_statuses"] == {
        "rec-1": "stale_record_missing"
    }
    assert missing.store.list_holding_cases() == []


def test_worker_retries_transient_fresh_read_failure(tmp_path):
    service = _service(tmp_path, _Storage(fail=True))
    service.accept(_payload())

    result = service.process_due()

    assert result["success"] is False
    assert result["results"][0]["status"] == "failed_retryable"
    event = service.store.get_holding_event("evt-1")
    assert event["state"] == "failed_retryable"
    assert event["attempt_count"] == 1


def test_worker_retries_futu_provider_failure_without_materializing_cases(tmp_path):
    class FutuStorage(_Storage):
        def get_raw_holdings(self, *, account=None, record_id=None):
            rows = super().get_raw_holdings(account=account, record_id=record_id)
            if not rows:
                return rows
            fields = dict(rows[0].raw_fields)
            fields["broker"] = "富途"
            return [RawHoldingRecord(rows[0].record_id, fields)]

    current_time = [datetime(2026, 7, 31, 21, 0)]
    provider_ready = [False]

    def observe(_account):
        if not provider_ready[0]:
            raise RuntimeError("OpenD unavailable")
        return SimpleNamespace(
            source="futu",
            source_snapshot_id="snapshot-lx",
            observed_at_utc="2026-07-31T13:00:00+00:00",
            profile_fingerprint="profile-lx",
            account_fingerprint="account-lx",
            positions=(
                SimpleNamespace(
                    asset_id="AAPL.US",
                    raw_code="US.AAPL",
                    asset_name="Apple",
                    security_type="STOCK",
                    market="US",
                    currency="USD",
                    currency_explicit=True,
                ),
            ),
        )

    storage = FutuStorage()
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: current_time[0],
    )
    workflow = HoldingsWorkflowService(
        storage=storage,
        store=store,
        reconciliation=HoldingsReconciliationService(
            storage=storage,
            futu_observer=observe,
        ),
    )
    service = HoldingEventInboxService(
        storage=storage,
        store=store,
        workflow=workflow,
        target=TARGET,
    )
    service.accept(_payload())

    failed = service.process_due()

    assert failed["success"] is False
    assert "provider evidence unavailable" in failed["results"][0]["error"]
    assert store.get_holding_event("evt-1")["state"] == "failed_retryable"
    assert store.list_holding_cases() == []

    provider_ready[0] = True
    current_time[0] += timedelta(minutes=2)
    recovered = service.process_due()

    assert recovered["success"] is True
    assert store.get_holding_event("evt-1")["state"] == "processed"
    assert len(store.list_holding_cases()) == 1


def test_new_event_for_repaired_record_closes_once_then_becomes_semantic_noop(tmp_path):
    storage = _Storage()
    service = _service(tmp_path, storage)
    service.accept(_payload("evt-1"))
    service.process_due()
    case = service.store.list_holding_cases()[0]

    storage.currency = "USD"
    service.accept(_payload("evt-2"))
    closed = service.process_due()
    assert closed["success"] is True
    assert service.store.get_holding_case(case["case_key"])["state"] == "resolved_external"
    closed_event = service.store.get_holding_event("evt-2")
    closure_keys = closed_event["outcome"]["workflow"]["enqueued_receipt_keys"]
    assert len(closure_keys) == 1

    service.accept(_payload("evt-3"))
    noop = service.process_due()
    assert noop["success"] is True
    assert service.store.get_holding_event("evt-3")["outcome"]["workflow"] == {
        "created_case_keys": [],
        "refreshed_case_keys": [],
        "reopened_case_keys": [],
        "superseded_case_keys": [],
        "closed_case_keys": [],
        "enqueued_receipt_keys": [],
    }
    assert service.store.get_operation_receipt(closure_keys[0]) is not None


def test_event_closes_repaired_field_while_silently_migrating_legacy_case(tmp_path):
    class TimestampStorage(_Storage):
        def get_raw_holdings(self, *, account=None, record_id=None):
            rows = super().get_raw_holdings(account=account, record_id=record_id)
            if not rows:
                return rows
            fields = dict(rows[0].raw_fields)
            fields["created_at"] = "2026-03-30T00:00:00Z"
            return [RawHoldingRecord(rows[0].record_id, fields)]

    storage = TimestampStorage()
    service = _service(tmp_path, storage)
    service.accept(_payload("evt-1"))
    assert service.process_due()["success"] is True
    cases = {case["field"]: case for case in service.store.list_holding_cases()}
    planned = service.workflow.plan_event_notification(
        record_id="rec-1",
        trigger={"mode": "test_legacy_fixture"},
    )
    timestamp_candidate = next(
        case for case in planned["cases"] if case["field"] == "created_at"
    )
    with service.store._connect() as conn:
        conn.execute(
            "UPDATE holding_reconciliation_cases "
            "SET case_precondition_digest = ? WHERE case_key = ?",
            (
                timestamp_candidate["legacy_case_precondition_digest"],
                timestamp_candidate["case_key"],
            ),
        )
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM operation_receipt_outbox"
        ).fetchone()[0]
    storage.currency = "USD"
    service.accept(_payload("evt-2"))

    processed = service.process_due()

    assert processed["success"] is True
    assert storage.patch_calls == []
    event = service.store.get_holding_event("evt-2")
    workflow = event["outcome"]["workflow"]
    assert workflow["closed_case_keys"] == [cases["currency"]["case_key"]]
    assert len(workflow["enqueued_receipt_keys"]) == 1
    assert service.store.get_operation_receipt(
        workflow["enqueued_receipt_keys"][0]
    )["receipt_type"] == "holding_case_closed"
    durable_timestamp = service.store.get_holding_case(
        cases["created_at"]["case_key"]
    )
    assert durable_timestamp["state"] == "pending_manual_edit"
    assert durable_timestamp["case_precondition_digest"].startswith(
        "holdings-precondition.v2:"
    )
    assert [
        item["event_type"]
        for item in service.store.list_holding_case_events(
            cases["created_at"]["case_key"]
        )
    ].count("precondition_contract_migrated") == 1
    with service.store._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operation_receipt_outbox"
        ).fetchone()[0] == receipt_count + 1


def test_read_only_status_does_not_create_operation_state(tmp_path):
    db_path = tmp_path / "missing" / "operations.sqlite3"

    result = OperationStateStore.inspect_holding_event_status(db_path)

    assert result["initialized"] is False
    assert result["db_path"] == str(db_path)
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_worker_loop_logs_cycle_failure_and_keeps_running(tmp_path, capsys):
    service = _service(tmp_path, _Storage())
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


class _InvalidWorkflow:
    def plan_event_notification(self, *, record_id, trigger):
        case = {
            "case_key": "case-atomic",
            "record_id": record_id,
            "account": "lx",
            "field": "currency",
            "kind": "missing_completable",
            "blocks_official_nav": True,
            "policy_version": "holdings-validation.v1+holdings-currency.v1",
            "authority_id": "asset_type:us_stock",
            "current": None,
            "proposed": "USD",
            "record_digest": "record",
            "case_precondition_digest": "precondition",
            "latest_evidence_instance_id": None,
            "evidence": {},
            "state": "pending_apply",
        }
        return {
            "record_id": record_id,
            "cases": [case],
            "discovery_receipts": [],
            "active_case_keys": [case["case_key"]],
            "record_digest": "record",
            "current_identity": {"account": "lx"},
            "prove_external": False,
            "trigger": trigger,
            "validation": {"success": False},
            "record_status": "validated",
        }


def test_worker_rolls_back_cases_and_receipts_before_retrying_event(tmp_path):
    storage = _Storage()
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    service = HoldingEventInboxService(
        storage=storage,
        store=store,
        workflow=_InvalidWorkflow(),
        target=TARGET,
    )
    service.accept(_payload())

    result = service.process_due()

    assert result["success"] is False
    assert store.get_holding_case("case-atomic") is None
    assert store.get_operation_receipt(
        "holdings:case:discovered:case-atomic"
    ) is None
    assert store.get_holding_event("evt-1")["state"] == "failed_retryable"
