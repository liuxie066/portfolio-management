from __future__ import annotations

from datetime import datetime, timedelta
import threading

import pytest
from types import SimpleNamespace

from src.app.holding_event_inbox_service import HoldingEventInboxService
from src.app.holdings_event_service import HOLDINGS_EVENT_TYPE, HoldingsEventTarget
from src.app.holdings_reconciliation_service import HoldingsReconciliationService
from src.app.holdings_workflow_service import HoldingsWorkflowService
from src.app.operation_state_store import OperationStateStore
from src.domain.holding_mutations import HoldingRepairPatch
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

    def patch_holding_record(self, patch):
        assert isinstance(patch, HoldingRepairPatch)
        self.patch_calls.append((patch.record_id, dict(patch.values)))
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
    planned = service.workflow.process_event_notification(
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
    def process_event_notification(self, *, record_id, trigger):
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


class _FundStorage:
    def __init__(self):
        self.fields = {
            "asset_id": "011609", "asset_name": "", "asset_type": "cn_fund",
            "account": "hb", "broker": "支付宝", "quantity": 10, "currency": "CNY",
        }
        self.patch_calls = []
        self.exists = True
        self.before_patch = None
        self.fail_write = False

    def get_raw_holdings(self, *, account=None, record_id=None):
        if not self.exists:
            return []
        return [RawHoldingRecord(
            record_id="rec-1", raw_fields=dict(self.fields), fetched_at=datetime.now(),
        )]

    def patch_holding_record(self, patch, *, refresh_account_cache=True):
        from src.domain.holding_mutations import raw_holding_state_digest, HoldingMutationConflictError

        if self.before_patch:
            self.before_patch()
        if patch.base_digest != raw_holding_state_digest("rec-1", self.fields):
            raise HoldingMutationConflictError("raw row changed before patch")
        self.patch_calls.append(patch)
        if self.fail_write:
            raise TimeoutError("remote result unknown")
        self.fields.update(patch.values)
        return self.get_raw_holdings(record_id="rec-1")[0]


def _fund_evidence(code="011609", **overrides):
    return {
        "code": code, "name": "测试基金", "source": "tencent_jj",
        "source_as_of": "2026-10-14T08:00:00", **overrides,
    }


def _fund_service(tmp_path, storage, lookup=None):
    service = _service(tmp_path, storage)
    service.workflow.reconciliation.fund_name_lookup = lookup or (lambda code: _fund_evidence(code))
    return service


def test_fund_event_completes_only_empty_name_and_self_event_is_noop(tmp_path):
    storage = _FundStorage()
    original = dict(storage.fields)
    service = _fund_service(tmp_path, storage)
    service.accept(_payload())
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1
    assert dict(storage.patch_calls[0].values) == {"asset_name": "测试基金"}
    assert storage.fields == {**original, "asset_name": "测试基金"}
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "resolved_accept"
    assert case["resolution"]["operator_context"]["method"] == "automatic_policy"
    assert case["resolution"]["operator_context"]["event_id"] == "evt-1"
    assert service.store.get_operation_receipt(f"holdings:case:discovered:{case['case_key']}") is None
    service.accept(_payload("evt-self"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def test_fund_name_lookup_failure_leaves_manual_case(tmp_path):
    storage = _FundStorage()
    def fail(code):
        raise TimeoutError("provider unavailable")
    service = _fund_service(tmp_path, storage, fail)
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not storage.patch_calls
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "pending_manual_edit"
    assert service.store.get_operation_receipt(f"holdings:case:discovered:{case['case_key']}")["payload"]["reason_code"] == "ASSET_NAME_MISSING"


def test_fund_unknown_attempt_survives_new_event_and_provider_name_change(tmp_path):
    storage = _FundStorage()
    storage.fail_write = True
    service = _fund_service(tmp_path, storage)
    service.accept(_payload())
    assert service.process_due()["success"]
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "apply_outcome_unknown"
    service.workflow.reconciliation.fund_name_lookup = lambda code: _fund_evidence(code, name="另一名称")
    service.accept(_payload("evt-again"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1
    assert service.store.get_holding_case(case["case_key"])["state"] == "apply_outcome_unknown"
    assert len([c for c in service.store.list_holding_cases() if c["field"] == "asset_name"]) == 1


def test_fund_completion_preserves_user_edit_during_lookup(tmp_path):
    storage = _FundStorage()
    def lookup(code):
        storage.fields["asset_name"] = "人工名称"
        return _fund_evidence(code)
    service = _fund_service(tmp_path, storage, lookup)
    service.accept(_payload())
    assert service.process_due()["success"]
    assert storage.fields["asset_name"] == "人工名称"
    assert not storage.patch_calls


def test_fund_completion_durable_when_inbox_commit_fails(tmp_path, monkeypatch):
    storage = _FundStorage()
    service = _fund_service(tmp_path, storage)
    service.accept(_payload())
    original = service.store.complete_holding_event
    def fail(**kwargs):
        raise RuntimeError("inbox transaction failed")
    monkeypatch.setattr(service.store, "complete_holding_event", fail)
    assert not service.process_due()["success"]
    assert len(storage.patch_calls) == 1
    assert next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")["state"] == "resolved_accept"
    monkeypatch.setattr(service.store, "complete_holding_event", original)
    service.accept(_payload("evt-after-crash"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def test_ordinary_reconcile_never_queries_or_completes_fund_name(tmp_path):
    storage = _FundStorage()
    def unexpected(code):
        raise AssertionError("ordinary reconcile must not query fund names")
    service = _fund_service(tmp_path, storage, unexpected)
    result = service.workflow.reconciliation.reconcile(record_id="rec-1")
    assert result["blocking_record_count"] > 0
    assert not storage.patch_calls


@pytest.mark.parametrize("updates", [
    {"code": "999999"}, {"source": "eastmoney"}, {"source_as_of": None},
    {"name": ""}, {"name": "011609"}, {"name": None}, {"name": ["name"]},
])
def test_untrusted_fund_name_evidence_never_writes(tmp_path, updates):
    storage = _FundStorage()
    service = _fund_service(tmp_path, storage, lambda code: _fund_evidence(**updates))
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not storage.patch_calls
    assert next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")["state"] == "pending_manual_edit"


@pytest.mark.parametrize("updates", [
    {"asset_name": "已有名称"}, {"asset_type": "us_stock"},
    {"account": ""}, {"broker": "unknown"}, {"asset_id": "bad-code"},
    {"asset_name": 123}, {"asset_type": ["cn_fund"]},
])
def test_non_candidates_never_query_fund_name(tmp_path, updates):
    storage = _FundStorage()
    storage.fields.update(updates)
    calls = []
    service = _fund_service(tmp_path, storage, lambda code: calls.append(code))
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not calls
    assert not storage.patch_calls


@pytest.mark.parametrize("updates", [
    {"account": "sy"}, {"asset_id": "011610"}, {"asset_type": "us_stock"},
])
def test_fund_completion_rechecks_raw_row_after_evidence_fetch(tmp_path, updates):
    storage = _FundStorage()
    def lookup(code):
        storage.fields.update(updates)
        return _fund_evidence(code)
    service = _fund_service(tmp_path, storage, lookup)
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not storage.patch_calls
    assert all(storage.fields[key] == value for key, value in updates.items())


def test_fund_record_deleted_before_locked_read_is_noop(tmp_path):
    from contextlib import contextmanager
    storage = _FundStorage()
    service = _fund_service(tmp_path, storage)
    @contextmanager
    def lock(key):
        storage.exists = False
        yield
    service.workflow.lock_factory = lock
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not storage.patch_calls
    assert service.store.get_holding_event("evt-1")["outcome"]["record_statuses"] == {"rec-1": "stale_record_missing"}


def test_name_completion_does_not_silence_other_missing_fields(tmp_path):
    storage = _FundStorage()
    storage.fields["currency"] = ""
    service = _fund_service(tmp_path, storage)
    service.accept(_payload())
    assert service.process_due()["success"]
    assert dict(storage.patch_calls[0].values) == {"asset_name": "测试基金"}
    currency = next(c for c in service.store.list_holding_cases() if c["field"] == "currency")
    assert currency["state"] == "pending_apply"
    assert service.store.get_operation_receipt(f"holdings:case:discovered:{currency['case_key']}") is not None
    assert storage.fields["currency"] == ""


@pytest.mark.parametrize("consumer", ["notify", "nav"])
def test_normal_validation_cannot_release_unknown_automatic_name_attempt(tmp_path, consumer):
    storage = _FundStorage()
    storage.fail_write = True
    service = _fund_service(tmp_path, storage)
    service.accept(_payload())
    assert service.process_due()["success"]
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "apply_outcome_unknown"

    if consumer == "notify":
        service.workflow.notify(record_id="rec-1")
    else:
        evaluation = service.workflow.reconciliation.evaluate(account="hb")
        plan = service.workflow.plan_evaluation(evaluation, trigger={"mode": "nav_preflight"})
        service.workflow.materialize_preflight_plan(plan, evaluation)
    assert service.store.get_holding_case(case["case_key"])["state"] == "apply_outcome_unknown"
    service.accept(_payload("evt-after-validation"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def test_prepared_automatic_attempt_survives_finalize_failure_and_validation(tmp_path, monkeypatch):
    storage = _FundStorage()
    storage.fail_write = True
    service = _fund_service(tmp_path, storage)
    original = service.store.finalize_holding_cases
    def fail(**kwargs):
        raise RuntimeError("finalization interrupted")
    monkeypatch.setattr(service.store, "finalize_holding_cases", fail)
    service.accept(_payload())
    assert not service.process_due()["success"]
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "applying"
    assert case["remote_attempt_started_at"]
    monkeypatch.setattr(service.store, "finalize_holding_cases", original)
    service.workflow.notify(record_id="rec-1")
    assert service.store.get_holding_case(case["case_key"])["state"] == "applying"
    service.accept(_payload("evt-retry"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def test_unknown_automatic_origin_survives_manual_recovery(tmp_path):
    storage = _FundStorage()
    storage.fail_write = True
    service = _fund_service(tmp_path, storage)
    service.accept(_payload())
    assert service.process_due()["success"]
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    for _ in range(2):
        result = service.workflow.recover(
            case_key=case["case_key"], confirmed_operator={"method": "manual_cli"},
        )
        assert result["status"] == "apply_outcome_unknown"
    service.workflow.notify(record_id="rec-1")
    assert service.store.get_holding_case(case["case_key"])["state"] == "apply_outcome_unknown"
    service.accept(_payload("evt-after-recover"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def _hk_service(tmp_path, *, updates=None, lookup=None):
    storage = _FundStorage()
    storage.fields.update({"asset_id": "00001", "asset_type": "hk_stock", "account": "test-account",
                           "broker": "test-broker", "currency": "HKD", **(updates or {})})
    service = _service(tmp_path, storage)
    service.workflow.reconciliation.hk_stock_name_lookup = lookup or _hk_evidence
    return storage, service


def _hk_evidence(asset_code, **updates):
    return {"code": asset_code, "name": "测试证券", "source": "tencent_hk", "market": "HK",
            "source_as_of": "2026-01-01T00:00:00+00:00", **updates}


def test_hk_name_completion_only_name_with_evidence_and_reentry(tmp_path):
    storage, service = _hk_service(tmp_path, updates={"asset_id": "1.HK"})
    original = dict(storage.fields)
    service.accept(_payload())
    assert service.process_due()["success"]
    assert storage.fields == {**original, "asset_name": "测试证券"}
    assert dict(storage.patch_calls[0].values) == {"asset_name": "测试证券"}
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "resolved_accept"
    assert case["evidence"]["source"] == "tencent_hk"
    assert case["evidence"]["source_as_of"]
    assert case["resolution"]["operator_context"]["policy"] == "empty_hk_stock_name.v1"
    service.accept(_payload())
    service.accept(_payload("evt-self"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


@pytest.mark.parametrize("updates", [{"code": "00002"}, {"market": "US"}, {"source": "other"},
                                    {"name": "1.HK"}, {"name": ""}, {"source_as_of": None},
                                    {"source_as_of": "not-a-time"}, {"source_as_of": "2026-01-01"},
                                    {"is_stale": True}, {"is_from_cache": True}])
def test_hk_unproven_name_remains_manual(tmp_path, updates):
    storage, service = _hk_service(tmp_path, lookup=lambda code: _hk_evidence(code, **updates))
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not storage.patch_calls
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "pending_manual_edit"


def test_hk_provider_failure_and_ordinary_reads_do_not_write(tmp_path):
    calls = []
    def fail(code):
        calls.append(code)
        raise TimeoutError("synthetic")
    storage, service = _hk_service(tmp_path, lookup=fail)
    service.workflow.reconciliation.reconcile(record_id="rec-1")
    assert calls == []
    service.accept(_payload())
    assert service.process_due()["success"]
    assert calls == ["00001"] and not storage.patch_calls


@pytest.mark.parametrize("updates", [{"asset_name": "已有名称"}, {"broker": "unknown"},
                                    {"account": ""}, {"asset_id": "1.US"}])
def test_hk_non_candidates_never_lookup(tmp_path, updates):
    calls = []
    storage, service = _hk_service(tmp_path, updates=updates, lookup=lambda code: calls.append(code))
    service.accept(_payload())
    assert service.process_due()["success"]
    assert calls == [] and not storage.patch_calls


@pytest.mark.parametrize("updates", [{"asset_name": "人工名称"}, {"account": "other-account"},
                                    {"broker": "other-broker"}, {"asset_type": "us_stock"}])
def test_hk_completion_preserves_concurrent_edit(tmp_path, updates):
    storage, service = _hk_service(tmp_path)
    def lookup(code):
        storage.fields.update(updates)
        return _hk_evidence(code)
    service.workflow.reconciliation.hk_stock_name_lookup = lookup
    service.accept(_payload())
    assert service.process_due()["success"]
    assert not storage.patch_calls
    assert all(storage.fields[k] == v for k, v in updates.items())


def test_hk_unknown_write_does_not_retry_on_new_event(tmp_path):
    storage, service = _hk_service(tmp_path)
    storage.fail_write = True
    service.accept(_payload())
    assert service.process_due()["success"]
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "apply_outcome_unknown"
    service.accept(_payload("evt-again"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def test_hk_name_completion_keeps_invalid_currency_nav_blocker(tmp_path):
    storage, service = _hk_service(tmp_path, updates={"currency": "HDK"})
    service.accept(_payload())
    assert service.process_due()["success"]
    assert storage.fields["asset_name"] == "测试证券"
    assert storage.fields["currency"] == "HDK"
    report = service.workflow.reconciliation.reconcile(record_id="rec-1")
    assert report["blocking_record_count"] == 1
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "currency")
    assert case["kind"] == "conflict" and case["blocks_official_nav"]


def test_hk_completion_preserves_edit_at_final_patch_recheck(tmp_path):
    storage, service = _hk_service(tmp_path)
    storage.before_patch = lambda: storage.fields.update(asset_name="人工最终名称")
    service.accept(_payload())
    assert service.process_due()["success"]
    assert storage.fields["asset_name"] == "人工最终名称"
    assert not storage.patch_calls
    case = next(c for c in service.store.list_holding_cases() if c["field"] == "asset_name")
    assert case["state"] == "superseded"


def test_hk_futu_record_does_not_borrow_public_name_evidence(tmp_path):
    calls = []
    storage, service = _hk_service(tmp_path, updates={"broker": "富途"},
                                  lookup=lambda code: calls.append(code))
    # Synthetic absent account evidence; do not construct a trading provider.
    service.workflow.reconciliation.futu_observer = lambda account: (_ for _ in ()).throw(
        RuntimeError("synthetic unavailable"))
    service.accept(_payload())
    assert not service.process_due()["success"]
    assert calls == [] and not storage.patch_calls



def _hk_currency_case(service):
    return next(c for c in service.store.list_holding_cases() if c["field"] == "currency")


def test_currency_correction_requires_explicit_exact_case_confirmation(tmp_path):
    storage, service = _hk_service(tmp_path, updates={"asset_name": "已有名称", "currency": "HDK"})
    service.accept(_payload())
    assert service.process_due()["success"]
    case = _hk_currency_case(service)
    assert case["state"] == "pending_confirmation" and case["proposed"] == "HKD"
    receipt = service.store.get_operation_receipt(f"holdings:case:discovered:{case['case_key']}")
    assert receipt["payload"]["action"]["allowed_decisions"] == ["accept-proposed"]
    assert not storage.patch_calls
    result = service.workflow.apply_missing(record_id="rec-1", confirmed_operator={"test": True})
    assert result["status"] == "no_eligible_missing_fields" and not storage.patch_calls
    with pytest.raises(ValueError, match="invalid currency cannot be kept"):
        service.workflow.resolve(case_key=case["case_key"], decision="keep-current",
                                 reason="test", confirmed_operator={"test": True})
    result = service.workflow.resolve(case_key=case["case_key"], decision="accept-proposed",
                                      reason="confirm reviewed typo", confirmed_operator={"test": True})
    assert result["success"]
    assert dict(storage.patch_calls[0].values) == {"currency": "HKD"}
    assert storage.fields["currency"] == "HKD"
    assert service.workflow.reconciliation.reconcile(record_id="rec-1")["blocking_record_count"] == 0
    with pytest.raises(ValueError, match="scope changed"):
        service.workflow.resolve(case_key=case["case_key"], decision="accept-proposed",
                                 reason="repeat", confirmed_operator={"test": True})
    assert len(storage.patch_calls) == 1


@pytest.mark.parametrize("updates", [{"currency": "EUR"}, {"account": "other"}, {"broker": "other"},
                                    {"asset_type": "us_stock"}])
def test_currency_correction_rejects_stale_confirmation(tmp_path, updates):
    storage, service = _hk_service(tmp_path, updates={"asset_name": "已有名称", "currency": "HDK"})
    service.accept(_payload())
    assert service.process_due()["success"]
    case = _hk_currency_case(service)
    storage.fields.update(updates)
    with pytest.raises(ValueError, match="scope changed"):
        service.workflow.resolve(case_key=case["case_key"], decision="accept-proposed",
                                 reason="test", confirmed_operator={"test": True})
    assert not storage.patch_calls


def test_currency_correction_unknown_result_never_replays_automatically(tmp_path):
    storage, service = _hk_service(tmp_path, updates={"asset_name": "已有名称", "currency": "HDK"})
    service.accept(_payload())
    assert service.process_due()["success"]
    case = _hk_currency_case(service)
    storage.fail_write = True
    service.workflow.resolve(case_key=case["case_key"], decision="accept-proposed",
                             reason="test", confirmed_operator={"test": True})
    assert service.store.get_holding_case(case["case_key"])["state"] == "apply_outcome_unknown"
    service.accept(_payload("evt-retry"))
    assert service.process_due()["success"]
    assert len(storage.patch_calls) == 1


def test_invalid_currency_cannot_reuse_stored_keep_waiver(tmp_path):
    from src.app.holding_case_contract import confirmation_scope
    storage, service = _hk_service(tmp_path, updates={"asset_name": "已有名称", "currency": "HDK"})
    service.accept(_payload())
    assert service.process_due()["success"]
    case = _hk_currency_case(service)
    service.store.finalize_holding_cases(outcomes=[{
        "case_key": case["case_key"], "state": "resolved_keep", "event_type": "test_prior_keep",
        "resolution": {"confirmation_scope": confirmation_scope(case)},
    }], receipts=[])
    evaluation = service.workflow.reconciliation.evaluate(record_id="rec-1")
    plan = service.workflow.plan_evaluation(evaluation, trigger={"test": True})
    assert case["case_key"] in plan["blocking_case_keys"]
    with pytest.raises(ValueError, match="invalid currency cannot be kept"):
        service.workflow.resolve(case_key=case["case_key"], decision="keep-current",
                                 reason="repeat prior keep", confirmed_operator={"test": True})
    assert not storage.patch_calls
