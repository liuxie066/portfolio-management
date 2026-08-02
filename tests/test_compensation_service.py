import json
import multiprocessing
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.app.compensation_service import CompensationService
from src.app.snapshot_service import SnapshotService
from src.domain.holding_mutations import (
    HoldingMutationConflictError,
    HoldingTarget,
)
from src.feishu.repositories.nav_history_repository import NavHistoryRepository
from src.models import AssetType, Holding, NAVHistory
from src.snapshot_models import HoldingSnapshot
from src.domain.snapshot_contracts import (
    SnapshotExactSetPlan,
    SnapshotWriteAuthority,
)


def _record_worker(queue_file, operation):
    CompensationService(storage=None, queue_file=queue_file).record(
        operation_type=operation,
        account="a",
        payload={"legacy": True},
        error="failed",
    )


def _holding(quantity):
    return Holding(
        record_id="holding-1",
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        broker="manual",
        quantity=quantity,
        currency="CNY",
    )


def _target(service, before, target):
    mutation = HoldingTarget.from_holdings(
        base=before,
        target=target,
        owned_fields={"quantity"},
    )
    return {
        "type": "HOLDING_TARGET_SET",
        "identity": {"asset_id": "000001", "account": "a", "broker": "manual"},
        "before": service.serialize_holding(before),
        "target": service.serialize_holding(target),
        "mutation": mutation.to_payload(),
    }


def _storage(current):
    storage = Mock()
    storage.mirror_compensation_task.return_value = {
        "status": "skipped_unconfigured",
    }
    state = {"holding": current}
    storage.get_holding_fresh.side_effect = lambda *_args: state["holding"]

    def replace(target):
        assert isinstance(target, HoldingTarget)
        previous = state["holding"]
        holding = target.to_holding(
            record_id=previous.record_id if previous is not None else "holding-1",
            created_at=previous.created_at if previous is not None else None,
        )
        state["holding"] = holding
        return holding

    storage.replace_holding.side_effect = replace
    return storage, state


def _snapshot_recovery_details(plan, authority, *, task_id="repair-snapshot"):
    binding = {
        "target_digest": plan.target_digest,
        "plan_digest": plan.plan_digest,
        "row_digest": plan.row_digest,
        "authority_digest": authority.authority_digest,
        "bound_authority_digest": authority.digest,
        "run_id": authority.run_id,
        "issuer": authority.issuer,
        "overwrite_existing": authority.overwrite_existing,
        "task_id": task_id,
    }
    base = {
        "source": "daily-job",
        "run_id": authority.run_id,
        "finality": {
            "run_id": authority.run_id,
            "writer": authority.issuer,
        },
        "cash_flow_basis": {"financial_fingerprint": "cash-flow-v1"},
    }
    prepared = {
        **base,
        "snapshot_persisted": False,
        "snapshot_status": "prepared",
        "snapshot_plan_digest": plan.plan_digest,
        "snapshot_task_id": task_id,
        "snapshot_retry_command": (
            f"pm compensation retry --task-id {task_id} --confirm"
        ),
        "snapshot_evidence": {**binding, "status": "prepared"},
    }
    failed = {
        **prepared,
        "snapshot_status": "failed",
        "snapshot_error": "snapshot boom",
        "snapshot_evidence": {**binding, "status": "failed"},
    }
    complete = {
        **base,
        "snapshot_persisted": True,
        "snapshot_status": "complete",
        "snapshot_digest": plan.row_digest,
        "snapshot_plan_digest": plan.plan_digest,
        "snapshot_task_id": task_id,
        "snapshot_evidence": {**binding, "status": "complete"},
    }
    return prepared, failed, complete


def test_nav_recovery_reads_details_from_the_nav_index():
    assert "details" in NavHistoryRepository.NAV_INDEX_PROJECTION_FIELDS


def test_compensation_service_persists_local_before_best_effort_mirror(tmp_path):
    queue_file = tmp_path / "compensation.jsonl"
    storage = Mock()
    service = CompensationService(storage=storage, queue_file=queue_file)
    before = _holding(10)
    desired = _holding(5)

    def fail_after_local_append(task, *, mirror_record_id=None):
        assert mirror_record_id is None
        assert service.get_task(task["task_id"])["status"] == "PENDING"
        raise RuntimeError("mirror unavailable")

    storage.mirror_compensation_task.side_effect = fail_after_local_append

    task = service.record(
        operation_type="BUY_TARGETS_INCOMPLETE",
        account="test",
        payload={"targets": [_target(service, before, desired)]},
        error="failed",
        related_record_id="rec1",
    )

    rows = [json.loads(line) for line in queue_file.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["task_id"] == task.task_id
    assert rows[0]["status"] == "PENDING"
    assert rows[1]["event"] == "MIRROR"
    assert rows[1]["mirror_receipt"]["status"] == "failed"
    assert service.get_task(task.task_id)["status"] == "PENDING"
    storage.mirror_compensation_task.assert_called_once()


def test_prepared_snapshot_target_is_fsynced_locally_without_mirror(tmp_path):
    queue_file = tmp_path / "compensation.jsonl"
    storage = Mock()
    service = CompensationService(storage=storage, queue_file=queue_file)
    before = _holding(10)
    desired = _holding(5)

    task = service.prepare(
        operation_type="NAV_HOLDINGS_SNAPSHOT_TARGET_SET",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        task_id="repair-prepared",
    )

    assert task.status == "PREPARED"
    event = json.loads(queue_file.read_text(encoding="utf-8").splitlines()[0])
    assert event["status"] == "PREPARED"
    assert event["task_id"] == "repair-prepared"
    service.update_status(
        task.task_id,
        "RESOLVED",
        resolution="original_write_completed",
    )
    assert service.get_task(task.task_id)["status"] == "RESOLVED"
    storage.mirror_compensation_task.assert_not_called()


def test_mirror_tracks_actionable_lifecycle_with_one_remote_identity(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, state = _storage(before)
    working_replace = storage.replace_holding.side_effect
    storage.replace_holding.side_effect = RuntimeError("holding unavailable")
    mirrored_statuses = []
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )

    def mirror_current(task, *, mirror_record_id=None):
        local = service.get_task(task["task_id"])
        assert local["status"] == task["status"]
        mirrored_statuses.append(task["status"])
        if mirror_record_id is None:
            return {"status": "created", "record_id": "mirror-1"}
        assert mirror_record_id == "mirror-1"
        return {"status": "updated", "record_id": mirror_record_id}

    storage.mirror_compensation_task.side_effect = mirror_current
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="initial failure",
    )

    failed = service.retry(task.task_id, confirm=True)

    assert failed["success"] is False
    assert failed["status"] == "FAILED"
    assert failed["mirror_record_id"] == "mirror-1"
    assert failed["mirror_receipt"]["status"] == "updated"
    assert state["holding"].quantity == 10

    storage.replace_holding.side_effect = working_replace
    resolved = service.retry(task.task_id, confirm=True)

    assert resolved["success"] is True
    assert resolved["status"] == "RESOLVED"
    assert resolved["mirror_record_id"] == "mirror-1"
    assert resolved["mirror_receipt"]["status"] == "updated"
    assert mirrored_statuses == [
        "PENDING",
        "RUNNING",
        "FAILED",
        "RUNNING",
        "RUNNING",
        "RESOLVED",
    ]


def test_mirror_skip_duplicate_and_error_are_receipts_not_task_authority(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, _state = _storage(before)
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    outcomes = [
        {"status": "skipped_unconfigured", "error": "table not configured"},
        {
            "status": "duplicate",
            "matched_count": 2,
            "error": "task_id is ambiguous",
        },
        RuntimeError("mirror transport failed"),
    ]

    def mirror_outcome(*_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    storage.mirror_compensation_task.side_effect = mirror_outcome
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="initial failure",
    )
    pending = service.get_task(task.task_id)
    assert pending["status"] == "PENDING"
    assert pending["mirror_receipt"]["status"] == "skipped_unconfigured"

    service.update_status(task.task_id, "RUNNING")
    running = service.get_task(task.task_id)
    assert running["status"] == "RUNNING"
    assert running["mirror_receipt"]["status"] == "duplicate"

    service.update_status(task.task_id, "FAILED", error="target failed")
    failed = service.get_task(task.task_id)
    assert failed["status"] == "FAILED"
    assert failed["error"] == "target failed"
    assert failed["mirror_receipt"]["status"] == "failed"
    assert "transport failed" in failed["mirror_receipt"]["error"]


def test_actionable_task_cannot_regress_to_prepared_before_local_append(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, _state = _storage(before)
    storage.mirror_compensation_task.return_value = {
        "status": "created",
        "record_id": "mirror-1",
    }
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="initial failure",
    )
    before_events = service._read_events()
    before_calls = storage.mirror_compensation_task.call_count

    with pytest.raises(
        ValueError,
        match="invalid compensation transition: PENDING -> PREPARED",
    ):
        service.update_status(task.task_id, "PREPARED")

    assert service._read_events() == before_events
    assert service.get_task(task.task_id)["status"] == "PENDING"
    assert storage.mirror_compensation_task.call_count == before_calls


def test_concurrent_recorders_retain_both_task_ids(tmp_path):
    queue_file = tmp_path / "compensation.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_record_worker, args=(queue_file, f"OP{index}")) for index in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    tasks = CompensationService(queue_file=queue_file).list_tasks()
    assert len(tasks) == 2
    assert {task["operation_type"] for task in tasks} == {"OP0", "OP1"}


def test_retry_applies_before_to_target_and_resolves(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, state = _storage(before)
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="boom",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is True
    assert result["status"] == "RESOLVED"
    assert state["holding"].quantity == 5
    storage.replace_holding.assert_called_once()
    events = [json.loads(line) for line in service.queue_file.read_text(encoding="utf-8").splitlines()]
    assert any(
        event.get("status") == "RUNNING"
        and event.get("target_outcomes") == [
            {"index": 0, "type": "HOLDING_TARGET_SET", "status": "applied"}
        ]
        for event in events
    )


def test_retry_after_target_side_effect_is_idempotent(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, _state = _storage(desired)
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="crash before resolved",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is True
    assert result["target_outcomes"] == [{"index": 0, "type": "HOLDING_TARGET_SET", "status": "already_applied"}]
    storage.replace_holding.assert_not_called()


def test_retry_treats_owned_fields_as_complete_and_preserves_manual_metadata(
    tmp_path,
):
    before = _holding(10).model_copy(update={"tag": ["old"]})
    desired = _holding(5).model_copy(update={"tag": ["old"]})
    current = _holding(5).model_copy(update={"tag": ["manual-new"]})
    storage, state = _storage(current)
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="crash before resolved",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is True
    assert result["target_outcomes"] == [{
        "index": 0,
        "type": "HOLDING_TARGET_SET",
        "status": "already_applied",
    }]
    assert state["holding"].tag == ["manual-new"]
    storage.replace_holding.assert_not_called()


def test_retry_can_resume_after_target_write_failed_before_mutation(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, state = _storage(before)
    working_replace = storage.replace_holding.side_effect
    storage.replace_holding.side_effect = RuntimeError("holding storage unavailable")
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="boom",
    )

    failed = service.retry(task.task_id, confirm=True)

    assert failed["success"] is False
    assert failed["status"] == "FAILED"
    assert failed["error_type"] == "target_apply_failed"
    assert state["holding"].quantity == 10

    storage.replace_holding.side_effect = working_replace
    resolved = service.retry(task.task_id, confirm=True)

    assert resolved["success"] is True
    assert resolved["status"] == "RESOLVED"
    assert resolved["retry_count"] == 2
    assert state["holding"].quantity == 5


def test_retry_classifies_repository_cas_failure_as_state_conflict(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, state = _storage(before)
    storage.replace_holding.side_effect = HoldingMutationConflictError(
        "holding fresh base digest changed"
    )
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="boom",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert result["error_type"] == "state_conflict"
    assert state["holding"].quantity == 10
    storage.replace_holding.assert_called_once()


def test_snapshot_retry_accepts_failed_details_and_is_idempotent_when_complete(tmp_path):
    desired = HoldingSnapshot(
        as_of="2026-03-19",
        account="a",
        asset_id="000001",
        broker="futu",
        quantity=10,
        currency="CNY",
        price=2,
        cny_price=2,
        market_value_cny=20,
        dedup_key="a:2026-03-19:futu:000001",
    )
    plan = SnapshotExactSetPlan.build(
        account="a",
        as_of="2026-03-19",
        target_digest="1" * 64,
        before=(),
        desired=(desired,),
    )
    authority = SnapshotWriteAuthority(
        account="a",
        as_of="2026-03-19",
        run_id="run-snapshot-retry",
        issuer="daily-job",
        overwrite_existing=False,
        confirmed=True,
        target_digest=plan.target_digest,
    ).bind(plan, require_confirm=True)
    prepared_details, failed_details, complete_details = (
        _snapshot_recovery_details(plan, authority)
    )
    nav = NAVHistory(
        record_id="nav-1",
        date=date(2026, 3, 19),
        account="a",
        total_value=1000,
        details=failed_details,
    )
    storage = Mock()
    storage.get_nav_on_date.return_value = nav
    storage.nav_history = Mock()
    snapshot_state = []
    storage.list_holding_snapshots_fresh.side_effect = (
        lambda **_kwargs: list(snapshot_state)
    )

    def apply_actions(*, actions, current, dry_run=False):
        assert dry_run is False
        by_id = {row.record_id: row for row in snapshot_state}
        for index, row in enumerate(actions.creates, start=1):
            created = row.model_copy(update={"record_id": f"snapshot-{index}"})
            by_id[created.record_id] = created
        for record_id, row in actions.updates:
            by_id[record_id] = row.model_copy(update={"record_id": record_id})
        for record_id in actions.deletes:
            by_id.pop(record_id, None)
        snapshot_state[:] = list(by_id.values())
        return {
            "created": len(actions.creates),
            "updated": len(actions.updates),
            "deleted": len(actions.deletes),
        }

    storage.apply_holding_snapshot_actions.side_effect = apply_actions

    def patch_details(record_id, details, *, dry_run=False):
        assert record_id == "nav-1"
        assert dry_run is False
        nav.details = details

    storage.nav_history.patch_nav_details.side_effect = patch_details
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    target = SnapshotService.recovery_target(
        plan=plan,
        authority=authority,
        planned_nav_details=prepared_details,
        complete_nav_details=complete_details,
    )
    task = service.record(
        operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
        account="a",
        payload={"targets": [target]},
        error="snapshot boom",
        related_record_id="nav-1",
    )

    resolved = service.retry(task.task_id, confirm=True)

    assert resolved["success"] is True
    assert nav.details == complete_details
    assert len(snapshot_state) == 1
    storage.nav_history.patch_nav_details.assert_called_once()

    duplicate = service.record(
        operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
        account="a",
        payload={"targets": [target]},
        error="orphaned running task",
        related_record_id="nav-1",
    )
    already_complete = service.retry(duplicate.task_id, confirm=True)

    assert already_complete["success"] is True
    assert already_complete["target_outcomes"][0]["status"] == "already_applied"
    assert already_complete["target_outcomes"][0]["snapshot_readback_verified"] is True
    assert storage.apply_holding_snapshot_actions.call_count == 2
    storage.nav_history.patch_nav_details.assert_called_once()

    tampered_target = json.loads(json.dumps(target))
    tampered_target["plan_digest"] = "0" * 64
    tampered = service.record(
        operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
        account="a",
        payload={"targets": [tampered_target]},
        error="tampered payload",
    )
    apply_count = storage.apply_holding_snapshot_actions.call_count
    patch_count = storage.nav_history.patch_nav_details.call_count

    conflict = service.retry(tampered.task_id, confirm=True)

    assert conflict["success"] is False
    assert conflict["error_type"] == "state_conflict"
    assert storage.apply_holding_snapshot_actions.call_count == apply_count
    assert storage.nav_history.patch_nav_details.call_count == patch_count

    details_tamper = json.loads(json.dumps(target))
    details_tamper["complete_nav_details"]["cash_flow_basis"] = {
        "financial_fingerprint": "tampered"
    }
    tampered_details_task = service.record(
        operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
        account="a",
        payload={"targets": [details_tamper]},
        error="tampered details payload",
    )

    details_conflict = service.retry(
        tampered_details_task.task_id,
        confirm=True,
    )

    assert details_conflict["success"] is False
    assert details_conflict["error_type"] == "state_conflict"
    assert storage.apply_holding_snapshot_actions.call_count == apply_count
    assert storage.nav_history.patch_nav_details.call_count == patch_count


def test_snapshot_retry_refuses_non_snapshot_nav_base_drift_before_mutation(
    tmp_path,
):
    desired = HoldingSnapshot(
        as_of="2026-03-19",
        account="a",
        asset_id="000001",
        broker="futu",
        quantity=10,
        currency="CNY",
        price=2,
        cny_price=2,
        market_value_cny=20,
        dedup_key="a:2026-03-19:futu:000001",
    )
    plan = SnapshotExactSetPlan.build(
        account="a",
        as_of="2026-03-19",
        target_digest="3" * 64,
        before=(),
        desired=(desired,),
    )
    authority = SnapshotWriteAuthority(
        account="a",
        as_of="2026-03-19",
        run_id="run-drift",
        issuer="daily-job",
        overwrite_existing=False,
        confirmed=True,
        target_digest=plan.target_digest,
    ).bind(plan, require_confirm=True)
    prepared, failed, complete = _snapshot_recovery_details(plan, authority)
    drifted = {
        **failed,
        "cash_flow_basis": {"financial_fingerprint": "cash-flow-v2"},
    }
    nav = NAVHistory(
        record_id="nav-1",
        date=date(2026, 3, 19),
        account="a",
        total_value=1000,
        details=drifted,
    )
    storage = Mock()
    storage.nav_history = Mock()
    storage.get_nav_on_date.return_value = nav
    target = SnapshotService.recovery_target(
        plan=plan,
        authority=authority,
        planned_nav_details=prepared,
        complete_nav_details=complete,
    )
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    task = service.record(
        operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
        account="a",
        payload={"targets": [target]},
        error="snapshot boom",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is False
    assert result["error_type"] == "state_conflict"
    assert "base details drifted" in result["error"]
    storage.apply_holding_snapshot_actions.assert_not_called()
    storage.nav_history.patch_nav_details.assert_not_called()


def test_snapshot_compensation_requires_exact_readback_before_resolved(tmp_path):
    desired = HoldingSnapshot(
        as_of="2026-03-19",
        account="a",
        asset_id="000001",
        broker="futu",
        quantity=10,
        currency="CNY",
        price=2,
        cny_price=2,
        market_value_cny=20,
        dedup_key="a:2026-03-19:futu:000001",
    )
    plan = SnapshotExactSetPlan.build(
        account="a",
        as_of="2026-03-19",
        target_digest="2" * 64,
        before=(),
        desired=(desired,),
    )
    authority = SnapshotWriteAuthority(
        account="a",
        as_of="2026-03-19",
        run_id="run-readback",
        issuer="daily-job",
        overwrite_existing=False,
        confirmed=True,
        target_digest=plan.target_digest,
    ).bind(plan, require_confirm=True)
    prepared_details, failed_details, complete_details = (
        _snapshot_recovery_details(plan, authority)
    )
    nav = NAVHistory(
        record_id="nav-1",
        date=date(2026, 3, 19),
        account="a",
        total_value=1000,
        details=failed_details,
    )
    storage = Mock()
    storage.get_nav_on_date.return_value = nav
    storage.list_holding_snapshots_fresh.return_value = []
    storage.apply_holding_snapshot_actions.return_value = {
        "created": 1,
        "updated": 0,
        "deleted": 0,
    }
    target = SnapshotService.recovery_target(
        plan=plan,
        authority=authority,
        planned_nav_details=prepared_details,
        complete_nav_details=complete_details,
    )
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    task = service.record(
        operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
        account="a",
        payload={"targets": [target]},
        error="readback missing",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert "fresh readback" in result["error"]
    storage.nav_history.patch_nav_details.assert_not_called()
    assert service.get_task(task.task_id)["status"] == "FAILED"


def test_retry_refuses_state_conflict_without_overwrite(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    legitimate_later_state = _holding(7)
    storage, state = _storage(legitimate_later_state)
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="boom",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is False
    assert result["error_type"] == "state_conflict"
    assert state["holding"].quantity == 7
    storage.replace_holding.assert_not_called()


def test_zero_delete_retry_refuses_reused_business_key_with_new_record(tmp_path):
    recorded = _holding(0)
    replacement = _holding(0).model_copy(update={"record_id": "holding-2"})
    mutation = HoldingTarget.from_holdings(
        base=recorded,
        target=recorded,
        owned_fields={"quantity"},
    )
    storage, _state = _storage(replacement)
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    target = {
        "type": "HOLDING_ZERO_DELETE",
        "identity": {"asset_id": "000001", "account": "a", "broker": "manual"},
        "before": service.serialize_holding(recorded),
        "target": None,
        "mutation": mutation.to_payload(),
    }
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [target]},
        error="delete completion was interrupted",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is False
    assert result["error_type"] == "state_conflict"
    storage.delete_holding_target_if_zero.assert_not_called()


def test_zero_delete_retry_passes_bound_target_and_proves_absence(tmp_path):
    recorded = _holding(0)
    mutation = HoldingTarget.from_holdings(
        base=recorded,
        target=recorded,
        owned_fields={"quantity"},
    )
    storage, state = _storage(recorded)

    def delete_bound_target(target):
        assert target == mutation
        state["holding"] = None
        return True

    storage.delete_holding_target_if_zero.side_effect = delete_bound_target
    service = CompensationService(
        storage=storage,
        queue_file=tmp_path / "compensation.jsonl",
    )
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={
            "targets": [{
                "type": "HOLDING_ZERO_DELETE",
                "identity": {
                    "asset_id": "000001",
                    "account": "a",
                    "broker": "manual",
                },
                "before": service.serialize_holding(recorded),
                "target": None,
                "mutation": mutation.to_payload(),
            }]
        },
        error="delete completion was interrupted",
    )

    result = service.retry(task.task_id, confirm=True)

    assert result["success"] is True
    storage.delete_holding_target_if_zero.assert_called_once_with(mutation)
    assert state["holding"] is None


def test_two_concurrent_retries_apply_transition_once(tmp_path):
    before = _holding(10)
    desired = _holding(5)
    storage, state = _storage(before)
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    task = service.record(
        operation_type="SELL_TARGETS_INCOMPLETE",
        account="a",
        payload={"targets": [_target(service, before, desired)]},
        error="boom",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.retry(task.task_id, confirm=True), range(2)))

    assert all(result["success"] for result in results)
    assert state["holding"].quantity == 5
    storage.replace_holding.assert_called_once()


def test_legacy_delta_task_is_listed_but_not_retried(tmp_path):
    service = CompensationService(queue_file=tmp_path / "compensation.jsonl")
    task = service.record(
        operation_type="BUY_CASH_DEDUCT_FAILED",
        account="a",
        payload={"cash_delta": -10},
        error="legacy",
    )

    listed = service.get_task(task.task_id)
    result = service.retry(task.task_id, confirm=True)

    assert listed["supported"] is False
    assert result["success"] is False
    assert result["supported"] is False
