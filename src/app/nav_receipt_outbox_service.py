"""Durable delivery for consolidated NAV receipts."""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from .nav_history_receipt_service import NavHistoryReceiptService
from .operation_state_store import OperationStateStore


class NavReceiptOutboxService:
    def __init__(
        self,
        *,
        store: Optional[OperationStateStore] = None,
        sender: Optional[Any] = None,
    ):
        self.store = store or OperationStateStore()
        self.sender = sender or NavHistoryReceiptService()

    def enqueue_and_dispatch(self, job_result: Dict[str, Any]) -> Dict[str, Any]:
        if bool(job_result.get("dry_run", True)):
            return self.sender.send(job_result)
        run_id = str(job_result.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("real NAV receipt requires run_id")
        receipt_key = f"nav:{run_id}"
        self.store.enqueue_nav_receipt(
            receipt_key=receipt_key,
            payload=dict(job_result),
        )
        result = self.dispatch_pending(limit=1, receipt_key=receipt_key)
        if result["results"]:
            return result["results"][0]
        row = self.store.get_nav_receipt(receipt_key)
        if row and row.get("status") == "sent":
            return {
                "success": True,
                "status": "sent",
                "channel": "feishu",
                "bot": "刘看山",
                "receipt_key": receipt_key,
                "message_id": row.get("message_id"),
                "deduplicated": True,
            }
        return {
            "success": False,
            "status": "queued",
            "channel": "feishu",
            "bot": "刘看山",
            "receipt_key": receipt_key,
            "error": (row or {}).get("last_error") or "receipt is waiting for retry",
        }

    def dispatch_pending(
        self,
        *,
        limit: int = 100,
        receipt_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        claim_id = uuid4().hex
        rows = self.store.claim_due_nav_receipts(
            limit=limit,
            receipt_key=receipt_key,
            claim_id=claim_id,
        )
        results = []
        sent = failed = 0
        for row in rows:
            try:
                delivery = dict(self.sender.send(dict(row["payload"])))
            except Exception as exc:
                delivery = {
                    "success": False,
                    "status": "failed",
                    "error": str(exc) or exc.__class__.__name__,
                }
            success = bool(delivery.get("success"))
            try:
                self.store.mark_nav_receipt(
                    row["receipt_key"],
                    claim_id=claim_id,
                    success=success,
                    message_id=delivery.get("message_id"),
                    error=delivery.get("error"),
                )
            except Exception as exc:
                raise ReceiptDispatchStateUnknown(
                    receipt_key=row["receipt_key"],
                    delivery=delivery,
                    cause=exc,
                ) from exc
            result = {
                **delivery,
                "receipt_key": row["receipt_key"],
            }
            if not success:
                result["status"] = "queued"
                result["queued"] = True
            sent += int(success)
            failed += int(not success)
            results.append(result)
        return {
            "success": failed == 0,
            "attempted": len(rows),
            "sent": sent,
            "failed": failed,
            "results": results,
        }


class ReceiptDispatchStateUnknown(RuntimeError):
    """The remote send was attempted but the local terminal state was not saved."""

    def __init__(
        self,
        *,
        receipt_key: str,
        delivery: Dict[str, Any],
        cause: Exception,
    ):
        self.receipt_key = receipt_key
        self.delivery = dict(delivery)
        super().__init__(
            "NAV receipt delivery state is unknown after send attempt: "
            f"{receipt_key}: {str(cause) or cause.__class__.__name__}"
        )
