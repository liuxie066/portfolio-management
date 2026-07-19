import json
import multiprocessing
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from src.app.compensation_service import CompensationService
from src.feishu.repositories.nav_history_repository import NavHistoryRepository
from src.models import AssetType, Holding, NAVHistory


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
        quantity=quantity,
        currency="CNY",
    )


def _target(service, before, target):
    return {
        "type": "HOLDING_TARGET_SET",
        "identity": {"asset_id": "000001", "account": "a", "broker": ""},
        "before": service.serialize_holding(before),
        "target": service.serialize_holding(target),
    }


def _storage(current):
    storage = Mock()
    state = {"holding": current}
    storage.get_holding.side_effect = lambda *_args: state["holding"]

    def replace(holding):
        state["holding"] = holding
        return holding

    storage.replace_holding.side_effect = replace
    return storage, state


def test_nav_recovery_reads_details_from_the_nav_index():
    assert "details" in NavHistoryRepository.NAV_INDEX_PROJECTION_FIELDS


def test_compensation_service_persists_local_before_best_effort_mirror(tmp_path):
    queue_file = tmp_path / "compensation.jsonl"
    storage = Mock()
    storage.add_compensation_task.side_effect = RuntimeError("mirror unavailable")
    service = CompensationService(storage=storage, queue_file=queue_file)

    task = service.record(
        operation_type="BUY_TARGETS_INCOMPLETE",
        account="test",
        payload={"targets": []},
        error="failed",
        related_record_id="rec1",
    )

    rows = [json.loads(line) for line in queue_file.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["task_id"] == task.task_id
    assert rows[0]["status"] == "PENDING"
    storage.add_compensation_task.assert_called_once()


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


def test_snapshot_retry_accepts_failed_details_and_is_idempotent_when_complete(tmp_path):
    original_details = {"source": "daily-job"}
    failed_details = {
        **original_details,
        "snapshot_persisted": False,
        "snapshot_status": "failed",
        "snapshot_error": "snapshot boom",
        "snapshot_task_id": "repair-snapshot",
    }
    complete_details = {
        **original_details,
        "snapshot_persisted": True,
        "snapshot_status": "complete",
        "snapshot_digest": "digest-1",
    }
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

    def patch_details(record_id, details, *, dry_run=False):
        assert record_id == "nav-1"
        assert dry_run is False
        nav.details = details

    storage.nav_history.patch_nav_details.side_effect = patch_details
    service = CompensationService(storage=storage, queue_file=tmp_path / "compensation.jsonl")
    target = {
        "type": "HOLDINGS_SNAPSHOT_TARGET_SET",
        "account": "a",
        "as_of": "2026-03-19",
        "nav_record_id": "nav-1",
        "before": {"one_of": [original_details, failed_details]},
        "target": {"details": complete_details},
        "snapshots": [{
            "as_of": "2026-03-19",
            "account": "a",
            "asset_id": "000001",
            "broker": "futu",
            "quantity": 10,
            "currency": "CNY",
            "dedup_key": "2026-03-19:a:000001:futu",
        }],
        "digest": "digest-1",
    }
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
    storage.batch_upsert_holding_snapshots.assert_called_once()
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
    assert already_complete["target_outcomes"] == [{
        "index": 0,
        "type": "HOLDINGS_SNAPSHOT_TARGET_SET",
        "status": "already_applied",
    }]
    storage.batch_upsert_holding_snapshots.assert_called_once()
    storage.nav_history.patch_nav_details.assert_called_once()


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
