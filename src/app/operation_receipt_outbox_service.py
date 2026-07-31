"""Durable dispatch for typed operation receipts."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from .holdings_receipt_service import HoldingsReceiptService
from .operation_state_store import OperationStateStore


class OperationReceiptOutboxService:
    def __init__(
        self,
        *,
        store: Optional[OperationStateStore] = None,
        renderers: Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = None,
        holdings_sender: Optional[HoldingsReceiptService] = None,
    ) -> None:
        self.store = store or OperationStateStore()
        sender = holdings_sender or HoldingsReceiptService()
        self.renderers = renderers or {
            receipt_type: (
                lambda payload, resolved_type=receipt_type: sender.send(
                    resolved_type,
                    payload,
                )
            )
            for receipt_type in sender.SUPPORTED_TYPES
        }

    def dispatch_pending(
        self,
        *,
        limit: int = 100,
        receipt_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        claim_id = uuid4().hex
        rows = self.store.claim_due_operation_receipts(
            limit=limit,
            receipt_key=receipt_key,
            claim_id=claim_id,
        )
        results = []
        counts = {"sent": 0, "failed": 0, "unknown": 0}
        for row in rows:
            self.store.start_operation_receipt_send(
                receipt_key=row["receipt_key"],
                claim_id=claim_id,
            )
            renderer = self.renderers.get(row["receipt_type"])
            if renderer is None:
                delivery = {
                    "success": False,
                    "delivery_state": "failed",
                    "status": "failed",
                    "error": f"no renderer for {row['receipt_type']}",
                }
            else:
                try:
                    delivery = dict(renderer(dict(row["payload"])))
                except Exception as exc:
                    delivery = {
                        "success": False,
                        "delivery_state": "unknown",
                        "status": "unknown",
                        "error": str(exc) or exc.__class__.__name__,
                    }
            delivery_state = str(delivery.get("delivery_state") or "").lower()
            outcome = (
                "sent"
                if bool(delivery.get("success")) and delivery_state == "accepted"
                else "failed"
                if delivery_state == "failed"
                else "unknown"
            )
            try:
                self.store.mark_operation_receipt(
                    receipt_key=row["receipt_key"],
                    claim_id=claim_id,
                    outcome=outcome,
                    message_id=delivery.get("message_id"),
                    error=delivery.get("error"),
                )
            except Exception as exc:
                raise OperationReceiptDispatchStateUnknown(
                    receipt_key=row["receipt_key"],
                    delivery=delivery,
                    cause=exc,
                ) from exc
            counts[outcome] += 1
            results.append(
                {
                    **delivery,
                    "status": outcome,
                    "receipt_key": row["receipt_key"],
                    "receipt_type": row["receipt_type"],
                }
            )
        return {
            "success": counts["failed"] == 0 and counts["unknown"] == 0,
            "attempted": len(rows),
            **counts,
            "results": results,
        }

    def resolve_unknown(
        self,
        *,
        receipt_key: str,
        decision: str,
        operator_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.store.resolve_operation_receipt(
            receipt_key=receipt_key,
            decision=decision,
            operator_context=operator_context,
        )
        return {
            "success": True,
            "receipt_key": receipt_key,
            "decision": decision,
            "receipt": self.store.get_operation_receipt(receipt_key),
        }


class OperationReceiptDispatchStateUnknown(RuntimeError):
    def __init__(
        self,
        *,
        receipt_key: str,
        delivery: Dict[str, Any],
        cause: Exception,
    ) -> None:
        self.receipt_key = receipt_key
        self.delivery = dict(delivery)
        super().__init__(
            "operation receipt delivery state is unknown after send attempt: "
            f"{receipt_key}: {str(cause) or cause.__class__.__name__}"
        )


__all__ = [
    "OperationReceiptOutboxService",
    "OperationReceiptDispatchStateUnknown",
]
