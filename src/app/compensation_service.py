"""Durable compensation tasks for partially completed financial writes."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from src import config
from src.models import Holding
from src.process_lock import account_lock_key, process_lock
from src.snapshot_models import HoldingSnapshot
from src.time_utils import bj_now_naive


SUPPORTED_TARGET_TYPES = {
    "HOLDING_TARGET_SET",
    "HOLDING_ZERO_DELETE",
    "CASH_TARGET_SET",
    "HOLDINGS_SNAPSHOT_TARGET_SET",
}


@dataclass
class CompensationTask:
    task_id: str
    operation_type: str
    account: str
    status: str
    payload: Dict[str, Any]
    error: str
    related_record_id: Optional[str] = None
    retry_count: int = 0
    created_at: str = field(default_factory=lambda: bj_now_naive().isoformat())
    updated_at: str = field(default_factory=lambda: bj_now_naive().isoformat())


class PartialWriteError(RuntimeError):
    """A primary ledger write succeeded but one or more target writes did not."""

    def __init__(
        self,
        *,
        operation: str,
        account: str,
        related_record_id: Optional[str],
        completed_steps: Iterable[str],
        failed_step: str,
        task_id: Optional[str],
        target_count: int,
        compensation_persisted: bool,
        original_error: Exception | str,
    ):
        self.operation = operation
        self.account = account
        self.related_record_id = related_record_id
        self.completed_steps = list(completed_steps)
        self.failed_step = failed_step
        self.task_id = task_id
        self.target_count = int(target_count)
        self.compensation_persisted = bool(compensation_persisted)
        self.original_error = str(original_error)
        super().__init__(
            f"partial write: operation={operation}, failed_step={failed_step}, "
            f"task_id={task_id or 'unavailable'}, error={self.original_error}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": False,
            "status": "partial",
            "operation": self.operation,
            "account": self.account,
            "related_record_id": self.related_record_id,
            "completed_steps": list(self.completed_steps),
            "failed_step": self.failed_step,
            "task_id": self.task_id,
            "target_count": self.target_count,
            "compensation_persisted": self.compensation_persisted,
            "error": self.original_error,
        }


class CompensationService:
    """Append, fold, inspect, and retry same-host compensation tasks."""

    def __init__(self, storage=None, queue_file: Optional[Path] = None):
        self.storage = storage
        self.queue_file = Path(queue_file) if queue_file else (config.get_data_dir() / "compensation_tasks.jsonl")

    @staticmethod
    def new_task_id() -> str:
        return f"repair_{uuid.uuid4().hex}"

    @staticmethod
    def serialize_holding(holding: Optional[Holding]) -> Optional[Dict[str, Any]]:
        if holding is None:
            return None
        data = holding.model_dump(mode="json")
        return {
            key: data.get(key)
            for key in (
                "asset_id",
                "asset_name",
                "asset_type",
                "account",
                "broker",
                "quantity",
                "avg_cost",
                "currency",
                "asset_class",
                "industry",
                "tag",
            )
        }

    def record(
        self,
        *,
        operation_type: str,
        account: str,
        payload: Dict[str, Any],
        error: Exception | str,
        related_record_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> CompensationTask:
        task = CompensationTask(
            task_id=task_id or self.new_task_id(),
            operation_type=operation_type,
            account=account,
            status="PENDING",
            payload=payload,
            error=str(error),
            related_record_id=related_record_id,
        )
        self._append_event({"event": "CREATED", **asdict(task)})

        if self.storage is not None and hasattr(self.storage, "add_compensation_task"):
            try:
                self.storage.add_compensation_task(task)
            except Exception:
                # The fsync'd local event log is authoritative; Feishu is a mirror.
                pass
        return task

    def list_tasks(self, *, include_resolved: bool = False) -> list[Dict[str, Any]]:
        tasks = list(self._fold_events().values())
        if not include_resolved:
            tasks = [task for task in tasks if task.get("status") != "RESOLVED"]
        tasks.sort(key=lambda task: (task.get("created_at") or "", task.get("task_id") or ""))
        return tasks

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._fold_events().get(task_id)

    def find_unresolved_by_related_record(self, related_record_id: str) -> Optional[Dict[str, Any]]:
        matches = [
            task for task in self.list_tasks(include_resolved=False)
            if task.get("related_record_id") == related_record_id
        ]
        return matches[-1] if matches else None

    def retry(self, task_id: str, *, confirm: bool = False) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("compensation retry requires confirm=True")
        initial = self.get_task(task_id)
        if initial is None:
            raise ValueError(f"compensation task not found: {task_id}")
        if not initial.get("supported"):
            return {
                "success": False,
                "status": initial.get("status"),
                "task_id": task_id,
                "supported": False,
                "error": "legacy or unsupported compensation payload; automatic retry refused",
            }

        with process_lock(account_lock_key(str(initial.get("account") or ""))):
            with process_lock(f"compensation:{task_id}"):
                task = self.get_task(task_id)
                if task is None:
                    raise ValueError(f"compensation task not found: {task_id}")
                if task.get("status") == "RESOLVED":
                    return {"success": True, **task, "already_resolved": True}

                retry_count = int(task.get("retry_count") or 0) + 1
                self._append_status(task_id, "RUNNING", retry_count=retry_count, target_outcomes=[])
                outcomes: list[Dict[str, Any]] = []
                try:
                    for index, target in enumerate(task["payload"]["targets"]):
                        outcome = self._apply_target(target)
                        outcomes.append({"index": index, "type": target.get("type"), **outcome})
                        self._append_status(
                            task_id,
                            "RUNNING",
                            retry_count=retry_count,
                            target_outcomes=list(outcomes),
                        )
                except Exception as exc:
                    error_type = "state_conflict" if isinstance(exc, CompensationStateConflict) else "target_apply_failed"
                    self._append_status(
                        task_id,
                        "FAILED",
                        retry_count=retry_count,
                        error=str(exc),
                        error_type=error_type,
                        target_outcomes=outcomes,
                    )
                    return {
                        "success": False,
                        "status": "FAILED",
                        "task_id": task_id,
                        "supported": True,
                        "error_type": error_type,
                        "error": str(exc),
                        "target_outcomes": outcomes,
                    }

                self._append_status(
                    task_id,
                    "RESOLVED",
                    retry_count=retry_count,
                    error="",
                    target_outcomes=outcomes,
                    resolved_at=bj_now_naive().isoformat(),
                )
                resolved = self.get_task(task_id) or {}
                return {"success": True, **resolved}

    def apply_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        """Apply one target during the original write under its account lock."""
        return self._apply_target(target)

    def _append_status(self, task_id: str, status: str, **metadata: Any) -> None:
        self._append_event({
            "event": "STATUS",
            "task_id": task_id,
            "status": status,
            "updated_at": bj_now_naive().isoformat(),
            **metadata,
        })

    def _append_event(self, event: Dict[str, Any]) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        lock_key = f"compensation-log:{self.queue_file.resolve()}"
        with process_lock(lock_key):
            fd = os.open(self.queue_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise

    def _read_events(self) -> list[Dict[str, Any]]:
        if not self.queue_file.exists():
            return []
        with process_lock(f"compensation-log:{self.queue_file.resolve()}"):
            lines = self.queue_file.read_text(encoding="utf-8").splitlines()
        events = []
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("task_id"):
                events.append(event)
        return events

    def _fold_events(self) -> Dict[str, Dict[str, Any]]:
        folded: Dict[str, Dict[str, Any]] = {}
        for event in self._read_events():
            task_id = str(event["task_id"])
            current = folded.get(task_id)
            if current is None:
                current = dict(event)
                current.pop("event", None)
                current.setdefault("status", "PENDING")
                current.setdefault("retry_count", 0)
                current.setdefault("payload", {})
                current.setdefault("target_outcomes", [])
                folded[task_id] = current
            else:
                for key, value in event.items():
                    if key not in {"event", "operation_type", "account", "payload", "related_record_id", "created_at"}:
                        current[key] = value
            targets = (current.get("payload") or {}).get("targets")
            current["supported"] = bool(
                isinstance(targets, list)
                and targets
                and all(isinstance(target, dict) and target.get("type") in SUPPORTED_TARGET_TYPES for target in targets)
            )
            current["target_count"] = len(targets) if isinstance(targets, list) else 0
        return folded

    def _apply_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        target_type = target.get("type")
        if target_type in {"HOLDING_TARGET_SET", "CASH_TARGET_SET", "HOLDING_ZERO_DELETE"}:
            return self._apply_holding_target(target)
        if target_type == "HOLDINGS_SNAPSHOT_TARGET_SET":
            return self._apply_snapshot_target(target)
        raise ValueError(f"unsupported compensation target type: {target_type}")

    def _apply_holding_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        identity = target.get("identity") or {}
        current = self.storage.get_holding(
            identity.get("asset_id"),
            identity.get("account"),
            identity.get("broker"),
        )
        current_state = self.serialize_holding(current)
        before = target.get("before")
        desired = target.get("target")

        if target.get("type") == "HOLDING_ZERO_DELETE":
            if current is None:
                return {"status": "already_applied"}
            if abs(float(current.quantity or 0.0)) <= 1e-8:
                if current.record_id:
                    self.storage.delete_holding_by_record_id(current.record_id)
                    return {"status": "applied"}
                return {"status": "already_applied"}
            if not self._state_matches(current_state, before):
                raise CompensationStateConflict(identity, current_state, before, desired)
            if not current.record_id:
                raise RuntimeError(f"holding delete target lacks record_id: {identity}")
            self.storage.delete_holding_by_record_id(current.record_id)
            return {"status": "applied"}

        if self._state_matches(current_state, desired):
            return {"status": "already_applied"}
        if not self._state_matches(current_state, before):
            raise CompensationStateConflict(identity, current_state, before, desired)
        if not isinstance(desired, dict):
            raise ValueError(f"holding target must be an object: {identity}")
        self.storage.replace_holding(Holding(**desired))
        return {"status": "applied"}

    def _apply_snapshot_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        account = str(target.get("account") or "")
        as_of = str(target.get("as_of") or "")
        nav_record_id = target.get("nav_record_id")
        current_nav = self.storage.get_nav_on_date(account, date.fromisoformat(as_of))
        if current_nav is None or (nav_record_id and current_nav.record_id != nav_record_id):
            raise CompensationStateConflict(
                {"account": account, "as_of": as_of, "record_id": nav_record_id},
                None if current_nav is None else {"record_id": current_nav.record_id},
                target.get("before"),
                target.get("target"),
            )

        current_details = dict(current_nav.details or {})
        desired_details = dict((target.get("target") or {}).get("details") or {})
        if current_details == desired_details:
            return {"status": "already_applied"}
        if not self._state_matches(current_details, target.get("before")):
            raise CompensationStateConflict(
                {"account": account, "as_of": as_of, "record_id": nav_record_id},
                current_details,
                target.get("before"),
                desired_details,
            )

        snapshots = [HoldingSnapshot(**row) for row in target.get("snapshots") or []]
        self.storage.batch_upsert_holding_snapshots(snapshots, dry_run=False)
        patch = getattr(getattr(self.storage, "nav_history", None), "patch_nav_details", None)
        if not callable(patch):
            patch = getattr(self.storage, "patch_nav_details", None)
        if not callable(patch):
            raise AttributeError("storage does not support patch_nav_details")
        patch(nav_record_id, desired_details, dry_run=False)
        return {"status": "applied"}

    @staticmethod
    def _state_matches(current: Any, expected: Any) -> bool:
        if isinstance(expected, dict) and set(expected) == {"one_of"}:
            return any(CompensationService._state_matches(current, item) for item in expected["one_of"])
        return current == expected


class CompensationStateConflict(RuntimeError):
    def __init__(self, identity: Dict[str, Any], current: Any, before: Any, target: Any):
        self.identity = identity
        self.current = current
        self.before = before
        self.target = target
        super().__init__(f"compensation state conflict for {identity}: current matches neither before nor target")
