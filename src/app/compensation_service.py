"""Durable compensation tasks for partially completed financial writes."""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from src import config
from src.domain.compensation_contracts import (
    CompensationStatus,
    MIRROR_ACTIVATING_STATUS_VALUES,
    validate_compensation_transition,
)
from src.domain.holding_mutations import (
    HoldingIdentity,
    HoldingMutationConflictError,
    HoldingTarget,
    canonical_holding,
    holding_owned_fields_match,
    holding_state_digest,
)
from src.domain.snapshot_contracts import SnapshotSetConflictError
from src.models import Holding
from src.process_lock import account_lock_key, process_lock
from src.time_utils import bj_now_naive


SUPPORTED_TARGET_TYPES = {
    "HOLDING_TARGET_SET",
    "HOLDING_ZERO_DELETE",
    "CASH_TARGET_SET",
    "HOLDINGS_SNAPSHOT_TARGET_SET",
}

logger = logging.getLogger(__name__)


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
        data = canonical_holding(holding).model_dump(mode="json")
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
            status=CompensationStatus.PENDING.value,
            payload=payload,
            error=str(error),
            related_record_id=related_record_id,
        )
        mirror_eligible = self._payload_is_supported(payload)
        with process_lock(f"compensation:{task.task_id}"):
            self._append_event({
                "event": "CREATED",
                **asdict(task),
                "mirror_eligible": mirror_eligible,
            })
            if mirror_eligible:
                self._mirror_current_state(task.task_id)
        return task

    def prepare(
        self,
        *,
        operation_type: str,
        account: str,
        payload: Dict[str, Any],
        task_id: Optional[str] = None,
    ) -> CompensationTask:
        """Fsync one recovery target before its first remote mutation.

        Prepared targets intentionally remain local.  The optional Feishu table
        is a task-status mirror, not authority for a transient write intent.
        """

        task = CompensationTask(
            task_id=task_id or self.new_task_id(),
            operation_type=operation_type,
            account=account,
            status=CompensationStatus.PREPARED.value,
            payload=payload,
            error="",
        )
        targets = payload.get("targets") if isinstance(payload, dict) else None
        if not isinstance(targets, list) or not targets:
            raise ValueError("prepared compensation payload requires targets")
        if not all(self._target_is_supported(target) for target in targets):
            raise ValueError("prepared compensation payload contains unsupported target")
        with process_lock(f"compensation:{task.task_id}"):
            self._append_event({
                "event": "CREATED",
                **asdict(task),
                "mirror_eligible": False,
            })
        return task

    def update_status(self, task_id: str, status: str, **metadata: Any) -> None:
        """Append a durable lifecycle event for an existing prepared task."""

        with process_lock(f"compensation:{task_id}"):
            if self.get_task(task_id) is None:
                raise ValueError(f"compensation task not found: {task_id}")
            self._append_status(task_id, status, **metadata)

    def list_tasks(self, *, include_resolved: bool = False) -> list[Dict[str, Any]]:
        tasks = list(self._fold_events().values())
        if not include_resolved:
            tasks = [
                task
                for task in tasks
                if task.get("status") != CompensationStatus.RESOLVED.value
            ]
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

        target_accounts = {
            str((target.get("identity") or {}).get("account") or target.get("account") or "")
            for target in (initial.get("payload") or {}).get("targets") or []
            if isinstance(target, dict)
        }
        target_accounts.discard("")
        if not target_accounts:
            target_accounts.add(str(initial.get("account") or ""))
        with ExitStack() as lock_stack:
            for account in sorted(target_accounts):
                lock_stack.enter_context(process_lock(account_lock_key(account)))
            lock_stack.enter_context(process_lock(f"compensation:{task_id}"))
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"compensation task not found: {task_id}")
            if task.get("status") == CompensationStatus.RESOLVED.value:
                return {"success": True, **task, "already_resolved": True}

            retry_count = int(task.get("retry_count") or 0) + 1
            self._append_status(
                task_id,
                CompensationStatus.RUNNING.value,
                retry_count=retry_count,
                target_outcomes=[],
            )
            outcomes: list[Dict[str, Any]] = []
            try:
                for index, target in enumerate(task["payload"]["targets"]):
                    outcome = self._apply_target(target)
                    outcomes.append({
                        "index": index,
                        "type": target.get("type"),
                        **outcome,
                    })
                    self._append_status(
                        task_id,
                        CompensationStatus.RUNNING.value,
                        retry_count=retry_count,
                        target_outcomes=list(outcomes),
                    )
            except Exception as exc:
                error_type = (
                    "state_conflict"
                    if isinstance(
                        exc,
                        (
                            CompensationStateConflict,
                            HoldingMutationConflictError,
                            SnapshotSetConflictError,
                        ),
                    )
                    else "target_apply_failed"
                )
                self._append_status(
                    task_id,
                    CompensationStatus.FAILED.value,
                    retry_count=retry_count,
                    error=str(exc),
                    error_type=error_type,
                    target_outcomes=outcomes,
                )
                failed = self.get_task(task_id) or {}
                return {"success": False, **failed}

            self._append_status(
                task_id,
                CompensationStatus.RESOLVED.value,
                retry_count=retry_count,
                error="",
                resolution="targets_applied_and_read_back",
                target_outcomes=outcomes,
                resolved_at=bj_now_naive().isoformat(),
            )
            resolved = self.get_task(task_id) or {}
            return {"success": True, **resolved}

    def apply_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        """Apply one target during the original write under its account lock."""
        return self._apply_target(target)

    def _append_status(self, task_id: str, status: str, **metadata: Any) -> None:
        current = self.get_task(task_id)
        if current is None:
            raise ValueError(f"compensation task not found: {task_id}")
        status = validate_compensation_transition(current.get("status"), status)
        mirror_eligible = bool(current.get("mirror_eligible"))
        if (
            not mirror_eligible
            and current.get("supported")
            and status in MIRROR_ACTIVATING_STATUS_VALUES
        ):
            mirror_eligible = True
        self._append_event({
            "event": "STATUS",
            **metadata,
            "task_id": task_id,
            "status": status,
            "updated_at": bj_now_naive().isoformat(),
            "mirror_eligible": mirror_eligible,
        })
        if mirror_eligible:
            self._mirror_current_state(task_id)

    def _mirror_current_state(self, task_id: str) -> None:
        """Best-effort projection after the authoritative local append."""

        task = self.get_task(task_id)
        if task is None or not task.get("mirror_eligible"):
            return
        mirror = getattr(self.storage, "mirror_compensation_task", None)
        if not callable(mirror):
            return

        attempted_at = bj_now_naive().isoformat()
        try:
            outcome = mirror(
                task,
                mirror_record_id=task.get("mirror_record_id"),
            )
            if not isinstance(outcome, dict) or not outcome.get("status"):
                raise RuntimeError("compensation mirror returned an invalid outcome")
            receipt = {
                "status": str(outcome["status"]),
                "task_id": task_id,
                "attempted_at": attempted_at,
                **{
                    key: value
                    for key, value in outcome.items()
                    if key not in {"status", "task_id"}
                },
            }
        except Exception as exc:
            receipt = {
                "status": "failed",
                "task_id": task_id,
                "attempted_at": attempted_at,
                "error": str(exc),
            }

        event = {
            "event": "MIRROR",
            "task_id": task_id,
            "mirror_receipt": receipt,
        }
        record_id = str(receipt.get("record_id") or "").strip()
        if record_id:
            event["mirror_record_id"] = record_id
        try:
            self._append_event(event)
        except Exception:
            logger.exception(
                "compensation mirror receipt append failed: task_id=%s status=%s",
                task_id,
                receipt.get("status"),
            )
            return

        if receipt.get("status") in {"failed", "duplicate"}:
            logger.warning(
                "compensation mirror did not converge: task_id=%s status=%s error=%s",
                task_id,
                receipt.get("status"),
                receipt.get("error") or "",
            )

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
                current.setdefault("status", CompensationStatus.PENDING.value)
                current.setdefault("retry_count", 0)
                current.setdefault("payload", {})
                current.setdefault("target_outcomes", [])
                folded[task_id] = current
            else:
                for key, value in event.items():
                    if key not in {"event", "operation_type", "account", "payload", "created_at"}:
                        current[key] = value
            targets = (current.get("payload") or {}).get("targets")
            current["supported"] = bool(
                isinstance(targets, list)
                and targets
                and all(self._target_is_supported(target) for target in targets)
            )
            current["target_count"] = len(targets) if isinstance(targets, list) else 0
            current.setdefault(
                "mirror_eligible",
                bool(
                    current["supported"]
                    and current.get("status") != CompensationStatus.PREPARED.value
                ),
            )
        return folded

    def _payload_is_supported(self, payload: Any) -> bool:
        targets = payload.get("targets") if isinstance(payload, dict) else None
        return bool(
            isinstance(targets, list)
            and targets
            and all(self._target_is_supported(target) for target in targets)
        )

    @staticmethod
    def _target_is_supported(target: Any) -> bool:
        if not isinstance(target, dict) or target.get("type") not in SUPPORTED_TARGET_TYPES:
            return False
        if target.get("type") == "HOLDINGS_SNAPSHOT_TARGET_SET":
            return True
        mutation = target.get("mutation")
        if not isinstance(mutation, dict):
            return False
        try:
            HoldingTarget.from_payload(mutation)
        except (TypeError, ValueError):
            return False
        return True

    def _apply_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        target_type = target.get("type")
        if target_type in {"HOLDING_TARGET_SET", "CASH_TARGET_SET", "HOLDING_ZERO_DELETE"}:
            return self._apply_holding_target(target)
        if target_type == "HOLDINGS_SNAPSHOT_TARGET_SET":
            return self._apply_snapshot_target(target)
        raise ValueError(f"unsupported compensation target type: {target_type}")

    def _apply_holding_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        identity = target.get("identity") or {}
        canonical_identity = HoldingIdentity(
            identity.get("asset_id"),
            identity.get("account"),
            identity.get("broker"),
        )
        mutation = HoldingTarget.from_payload(target.get("mutation") or {})
        if mutation.identity != canonical_identity:
            raise ValueError("compensation target identity disagrees with mutation")
        current = self.storage.get_holding_fresh(
            canonical_identity.asset_id,
            canonical_identity.account,
            canonical_identity.broker,
        )
        current_state = self.serialize_holding(current)
        before = target.get("before")
        desired = target.get("target")

        if target.get("type") == "HOLDING_ZERO_DELETE":
            if current is None:
                return {"status": "already_applied"}
            base_matches = (
                mutation.base_record_id is not None
                and current.record_id == mutation.base_record_id
                and holding_state_digest(current) == mutation.base_digest
                and self._state_matches(current_state, before)
            )
            if not base_matches or abs(float(current.quantity or 0.0)) > 1e-8:
                raise CompensationStateConflict(
                    canonical_identity.as_dict(),
                    current_state,
                    before,
                    desired,
                )
            deleted = self.storage.delete_holding_target_if_zero(mutation)
            readback = self.storage.get_holding_fresh(
                canonical_identity.asset_id,
                canonical_identity.account,
                canonical_identity.broker,
            )
            if readback is not None:
                raise RuntimeError(
                    f"holding delete fresh readback still exists: {canonical_identity}"
                )
            return {"status": "applied" if deleted else "already_applied"}

        if desired != self.serialize_holding(mutation.to_holding()):
            raise ValueError("compensation desired state disagrees with mutation")
        if (
            current is not None
            and holding_owned_fields_match(current, mutation)
        ):
            return {"status": "already_applied"}
        if not self._state_matches(current_state, before):
            raise CompensationStateConflict(
                canonical_identity.as_dict(),
                current_state,
                before,
                desired,
            )
        if not isinstance(desired, dict):
            raise ValueError(f"holding target must be an object: {canonical_identity}")
        self.storage.replace_holding(mutation)
        readback = self.storage.get_holding_fresh(
            canonical_identity.asset_id,
            canonical_identity.account,
            canonical_identity.broker,
        )
        if (
            readback is None
            or not holding_owned_fields_match(readback, mutation)
        ):
            raise RuntimeError(
                "holding compensation fresh readback does not match target: "
                f"{canonical_identity}"
            )
        return {"status": "applied"}

    def _apply_snapshot_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        from src.app.snapshot_service import SnapshotService

        try:
            plan, authority = SnapshotService.parse_recovery_target(target)
        except (TypeError, ValueError, PermissionError) as exc:
            raise CompensationStateConflict(
                {
                    "account": target.get("account"),
                    "as_of": target.get("as_of"),
                },
                "invalid_prepared_target",
                "bound_scope_and_digests",
                str(exc),
            ) from exc
        account = plan.account
        as_of = plan.as_of
        snapshot_service = SnapshotService(storage=self.storage)
        current_nav = self._get_nav_fresh(account=account, as_of=as_of)
        if current_nav is None:
            return {"status": "nav_absent_no_replay"}

        current_details = dict(current_nav.details or {})
        desired_details = dict(target.get("complete_nav_details") or {})
        initial_nav_state = snapshot_service.classify_recovery_nav_state(
            target=target,
            details=current_details,
            plan=plan,
            authority=authority,
        )

        snapshot_result = snapshot_service.apply_exact_set(
            plan=plan,
            authority=authority,
            dry_run=False,
        )
        prepatch_nav = self._get_nav_fresh(account=account, as_of=as_of)
        if (
            prepatch_nav is None
            or prepatch_nav.record_id != current_nav.record_id
        ):
            raise SnapshotSetConflictError(
                "fresh NAV identity changed before compensation completion patch"
            )
        prepatch_state = snapshot_service.classify_recovery_nav_state(
            target=target,
            details=dict(prepatch_nav.details or {}),
            plan=plan,
            authority=authority,
        )
        patch = getattr(getattr(self.storage, "nav_history", None), "patch_nav_details", None)
        if not callable(patch):
            patch = getattr(self.storage, "patch_nav_details", None)
        if not callable(patch):
            raise AttributeError("storage does not support patch_nav_details")
        if prepatch_state == "incomplete":
            patch(prepatch_nav.record_id, desired_details, dry_run=False)
        readback_nav = self._get_nav_fresh(account=account, as_of=as_of)
        if (
            readback_nav is None
            or readback_nav.record_id != current_nav.record_id
            or snapshot_service.classify_recovery_nav_state(
                target=target,
                details=dict(readback_nav.details or {}),
                plan=plan,
                authority=authority,
            )
            != "complete"
        ):
            raise RuntimeError(
                "NAV fresh readback does not confirm snapshot completion details"
            )
        mutations = sum(
            int(snapshot_result.get(name) or 0)
            for name in ("created", "updated", "deleted")
        )
        return {
            "status": (
                "already_applied"
                if mutations == 0 and initial_nav_state == "complete"
                else "applied"
            ),
            "snapshot_readback_verified": True,
            "nav_readback_verified": True,
        }

    def _get_nav_fresh(self, *, account: str, as_of: str) -> Any:
        nav_repo = getattr(self.storage, "nav_history", None)
        preload = getattr(nav_repo, "preload_nav_index", None)
        if not callable(preload):
            preload = getattr(self.storage, "preload_nav_index", None)
        if callable(preload):
            preload(account, force_refresh=True)
        return self.storage.get_nav_on_date(account, date.fromisoformat(as_of))

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
