"""Exact-record automatic completion policy for cash-flow events."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Dict, Optional

from src.domain.cash_flow_contracts import CASH_FLOW_MANUAL_REQUIRED_FIELDS
from src.process_lock import cash_flow_record_lock_key, process_lock

from .cash_flow_event_service import ACTIONABLE_CASH_FLOW_ACTIONS
from .cash_flow_fx_confirmation import (
    evaluate_cash_flow_fx_confirmation,
    frozen_fx_confirmation_identity,
)
from .operation_state_store import OperationStateStore


CASH_FLOW_ATTENTION_RECEIPT_TYPE = "cash_flow_reconcile_attention_required"
_FX_ATTENTION_MESSAGES = {
    "fx_confirmation_missing": "foreign cash-flow FX confirmation is missing",
    "fx_confirmation_stale": "foreign cash-flow FX confirmation is stale",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalize_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            normalized = Decimal(str(value)).normalize()
            return format(normalized, "f")
        except (InvalidOperation, TypeError, ValueError):
            return str(value)
    return str(value).strip()


def normalized_manual_inputs(row: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(row.get("fields") or row)
    return {
        field: _normalize_value(source.get(field))
        for field in CASH_FLOW_MANUAL_REQUIRED_FIELDS
    }


class CashFlowEventCompletionService:
    def __init__(
        self,
        *,
        storage: Any,
        operation_store: Optional[OperationStateStore] = None,
        lock_factory: Any = process_lock,
    ) -> None:
        self.storage = storage
        self.operation_store = operation_store or OperationStateStore()
        self.lock_factory = lock_factory
        self._legacy_fx_imported = False

    def __call__(self, *, record_id: str, trigger: Dict[str, Any]) -> Dict[str, Any]:
        return self.process_record(record_id=record_id, trigger=trigger)

    def process_record(
        self,
        *,
        record_id: str,
        trigger: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved_record_id = str(record_id or "").strip()
        if not resolved_record_id:
            raise ValueError("cash flow completion requires record_id")
        preview = self._preview(resolved_record_id)
        decision = self._decide(preview)
        terminal = self._terminal_without_write(
            record_id=resolved_record_id,
            decision=decision,
        )
        if terminal is not None:
            return terminal

        with self.lock_factory(cash_flow_record_lock_key(resolved_record_id)):
            locked_preview = self._preview(resolved_record_id)
            locked_decision = self._decide(locked_preview)
            terminal = self._terminal_without_write(
                record_id=resolved_record_id,
                decision=locked_decision,
            )
            if terminal is not None:
                return terminal
            apply_result = self.storage.reconcile_cash_flows(
                record_id=resolved_record_id,
                dry_run=False,
            )
            if not isinstance(apply_result, dict) or apply_result.get("success") is False:
                raise RuntimeError(
                    "cash flow exact-record apply failed: "
                    + str(
                        (apply_result or {}).get("error")
                        if isinstance(apply_result, dict)
                        else apply_result
                    )
                )
            readback = self._preview(resolved_record_id)
            readback_decision = self._decide(readback)
            post_terminal = self._terminal_without_write(
                record_id=resolved_record_id,
                decision=readback_decision,
            )
            if post_terminal is not None:
                if post_terminal["status"] == "already_complete":
                    return {
                        **post_terminal,
                        "status": "completed",
                        "updated_count": int(apply_result.get("updated_count") or 0),
                    }
                return post_terminal
            raise RuntimeError(
                "cash flow generated fields did not converge after exact-record apply"
            )

    def _preview(self, record_id: str) -> Dict[str, Any]:
        result = self.storage.reconcile_cash_flows(
            record_id=record_id,
            dry_run=True,
        )
        if not isinstance(result, dict) or result.get("success") is False:
            raise RuntimeError(
                "cash flow exact-record preview failed: "
                + str(result.get("error") if isinstance(result, dict) else result)
            )
        scanned = int(result.get("scanned") or 0)
        rows = list(result.get("rows") or ())
        if scanned == 0:
            return {"kind": "missing", "record_id": record_id}
        if scanned != 1 or len(rows) != 1:
            raise RuntimeError(
                "cash flow exact-record preview requires exactly one row: "
                f"scanned={scanned}, rows={len(rows)}"
            )
        return {
            "kind": "row",
            "record_id": record_id,
            "change_count": int(result.get("change_count") or 0),
            "error_count": int(result.get("error_count") or 0),
            "row": dict(rows[0]),
        }

    def _decide(self, preview: Dict[str, Any]) -> Dict[str, Any]:
        if preview["kind"] == "missing":
            return {"kind": "missing"}
        row = dict(preview["row"])
        if preview["error_count"] or row.get("status") == "error":
            return {
                "kind": "attention",
                "reason_code": "cash_flow_reconcile_error",
                "error": str(row.get("error") or "cash_flow reconcile failed"),
                "row": row,
                "fx_confirmation": None,
            }
        currency = str(row.get("currency") or "").upper()
        confirmation = None
        if currency != "CNY":
            self._ensure_legacy_fx_imported()
            confirmation = self.operation_store.latest_fx_confirmation(
                str(row.get("record_id") or preview["record_id"])
            )
            evaluation = evaluate_cash_flow_fx_confirmation(row, confirmation)
            if not evaluation["valid"]:
                return {
                    "kind": "attention",
                    "reason_code": evaluation["reason_code"],
                    "error": (
                        "foreign cash-flow FX evidence is missing or stale"
                        + (
                            f": mismatch={evaluation['mismatch_field']}"
                            if evaluation.get("mismatch_field")
                            else ""
                        )
                    ),
                    "row": row,
                    "fx_confirmation": confirmation,
                }
        if preview["change_count"]:
            return {
                "kind": "eligible",
                "row": row,
                "fx_confirmation": confirmation,
            }
        return {
            "kind": "complete",
            "row": row,
            "fx_confirmation": confirmation,
        }

    def _terminal_without_write(
        self,
        *,
        record_id: str,
        decision: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if decision["kind"] == "missing":
            return {
                "record_id": record_id,
                "status": "stale_record_missing",
                "receipts": [],
            }
        if decision["kind"] == "complete":
            return {
                "record_id": record_id,
                "status": "already_complete",
                "currency": str(decision["row"].get("currency") or "").upper(),
                "receipts": [],
            }
        if decision["kind"] == "attention":
            receipt = self._attention_receipt(
                record_id=record_id,
                reason_code=str(decision["reason_code"]),
                error=str(decision["error"]),
                row=dict(decision.get("row") or {}),
                confirmation=decision.get("fx_confirmation"),
            )
            return {
                "record_id": record_id,
                "status": "attention_required",
                "reason_code": decision["reason_code"],
                "receipts": [receipt],
            }
        return None

    def _ensure_legacy_fx_imported(self) -> None:
        if self._legacy_fx_imported:
            return
        self.operation_store.import_default_legacy_fx_confirmations()
        self._legacy_fx_imported = True

    @classmethod
    def _attention_receipt(
        cls,
        *,
        record_id: str,
        reason_code: str,
        error: str,
        row: Dict[str, Any],
        confirmation: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        manual_inputs = normalized_manual_inputs(row)
        issue = {
            "record_id": record_id,
            "reason_code": reason_code,
            "manual_inputs": manual_inputs,
        }
        if reason_code.startswith("fx_confirmation_"):
            issue["fx_confirmation"] = frozen_fx_confirmation_identity(confirmation)
        else:
            issue["error"] = error
        digest = hashlib.sha256(_canonical_json(issue).encode("utf-8")).hexdigest()
        payload = {
            **issue,
            "issue_digest": digest,
            "account": manual_inputs.get("account"),
            "error": _FX_ATTENTION_MESSAGES.get(reason_code, error),
            "action": {
                "command": (
                    f"pm cash-flow reconcile --record-id {record_id} "
                    "--apply --confirm"
                )
            },
        }
        return {
            "receipt_key": f"cash-flow:reconcile:attention:{record_id}:{digest}",
            "receipt_type": CASH_FLOW_ATTENTION_RECEIPT_TYPE,
            "payload": payload,
        }

    @classmethod
    def terminal_failure_receipts(
        cls,
        *,
        event: Dict[str, Any],
        error: str,
    ) -> list[Dict[str, Any]]:
        record_ids = sorted(
            {
                str(item.get("record_id") or "")
                for item in event.get("action_list") or ()
                if str(item.get("action") or "") in ACTIONABLE_CASH_FLOW_ACTIONS
                and str(item.get("record_id") or "")
            }
        )
        return [
            cls._attention_receipt(
                record_id=record_id,
                reason_code="event_processing_failed",
                error=str(error),
                row={"record_id": record_id},
                confirmation=None,
            )
            for record_id in record_ids
        ]


__all__ = [
    "CASH_FLOW_ATTENTION_RECEIPT_TYPE",
    "CashFlowEventCompletionService",
    "normalized_manual_inputs",
]
