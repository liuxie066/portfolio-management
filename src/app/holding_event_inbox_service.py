"""Durable callback acceptance and leased holdings event processing."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Dict, Optional

from .holdings_event_service import (
    ACTIONABLE_HOLDING_ACTIONS,
    HoldingEventTargetMismatch,
    HoldingsEventTarget,
    normalize_holding_event,
)
from .holdings_workflow_service import HoldingsWorkflowService
from .operation_state_store import OperationStateStore


class HoldingEventInboxService:
    def __init__(
        self,
        *,
        storage: Any,
        store: Optional[OperationStateStore] = None,
        workflow: Optional[HoldingsWorkflowService] = None,
        target: Optional[HoldingsEventTarget] = None,
        receiver_budget_seconds: float = 2.0,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.storage = storage
        self.store = store or OperationStateStore()
        self.workflow = workflow or HoldingsWorkflowService(
            storage=storage,
            store=self.store,
        )
        self.target = target or HoldingsEventTarget.from_config()
        self.receiver_budget_seconds = float(receiver_budget_seconds)
        self.monotonic = monotonic

    def accept(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Persist the canonical trigger before returning to the SDK callback."""

        started_at = self.monotonic()
        try:
            event = normalize_holding_event(payload, target=self.target)
        except HoldingEventTargetMismatch as exc:
            return {
                "success": True,
                "accepted": False,
                "filtered": True,
                "reason": str(exc),
            }
        inserted = self.store.accept_holding_event(
            event_id=event.event_id,
            event_type=event.event_type,
            file_token=event.file_token,
            table_id=event.table_id,
            revision=event.revision,
            action_list=list(event.action_list),
            payload_digest=event.payload_digest,
        )
        elapsed = self.monotonic() - started_at
        if elapsed > self.receiver_budget_seconds:
            raise TimeoutError(
                "holding event durable acceptance exceeded receiver budget: "
                f"{elapsed:.3f}s"
            )
        return {
            "success": True,
            "accepted": True,
            "inserted": inserted,
            "duplicate": not inserted,
            "event_id": event.event_id,
        }

    def process_due(self, *, limit: int = 100) -> Dict[str, Any]:
        claimed = self.store.claim_holding_events(limit=limit)
        results = []
        for event in claimed:
            try:
                results.append(self._process_claimed(event))
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                self.store.mark_holding_event_failed(
                    event_id=event["event_id"],
                    claim_id=event["claim_id"],
                    error=error,
                )
                results.append(
                    {
                        "event_id": event["event_id"],
                        "success": False,
                        "status": "failed_retryable",
                        "error": error,
                    }
                )
        return {
            "success": all(item.get("success") for item in results),
            "claimed": len(claimed),
            "processed": sum(item.get("status") == "processed" for item in results),
            "failed": sum(not item.get("success") for item in results),
            "results": results,
        }

    def _process_claimed(self, event: Dict[str, Any]) -> Dict[str, Any]:
        actions_by_record: Dict[str, set[str]] = {}
        ignored = []
        for item in event["action_list"]:
            action = str(item.get("action") or "")
            record_id = str(item.get("record_id") or "")
            if action in ACTIONABLE_HOLDING_ACTIONS:
                actions_by_record.setdefault(record_id, set()).add(action)
            else:
                ignored.append({"action": action, "record_id": record_id})

        materializations = []
        validations = []
        for record_id in sorted(actions_by_record):
            trigger = {
                "mode": "event_validate_notify",
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "file_token": event["file_token"],
                "table_id": event["table_id"],
                "record_id": record_id,
                "actions": sorted(actions_by_record[record_id]),
                "revision": event.get("revision"),
            }
            planned = self.workflow.plan_event_notification(
                record_id=record_id,
                trigger=trigger,
            )
            validations.append(planned.pop("validation"))
            materializations.append(planned)
        outcome = self.store.complete_holding_event(
            event_id=event["event_id"],
            claim_id=event["claim_id"],
            materializations=materializations,
            outcome={
                "status": "processed",
                "record_ids": sorted(actions_by_record),
                "validations": validations,
                "record_statuses": {
                    item["record_id"]: item.get("record_status", "validated")
                    for item in materializations
                },
                "ignored_actions": ignored,
            },
        )
        return {
            "event_id": event["event_id"],
            "success": True,
            **outcome,
        }

    def run_worker_loop(
        self,
        *,
        stop_event: threading.Event,
        poll_seconds: float = 1.0,
        limit: int = 100,
    ) -> None:
        while not stop_event.is_set():
            try:
                self.process_due(limit=limit)
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                print(
                    f"holdings event worker cycle failed: {error}",
                    file=sys.stderr,
                    flush=True,
                )
            stop_event.wait(max(float(poll_seconds), 0.1))
