"""Cash-flow write application service."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.app.compensation_service import PartialWriteError
from src.models import CashFlow, Holding


class TradeService:
    """Coordinate cash-flow writes and absolute cash holding targets."""

    def __init__(self, manager: Any, storage: Any):
        self.manager = manager
        self.storage = storage
        self.cash_service = manager.cash_service

    @staticmethod
    def _require_valid(result: dict[str, Any]) -> None:
        if result.get("ok"):
            return
        details = ", ".join(f"{item['field']} {item['message']}" for item in result.get("errors") or [])
        raise ValueError(f"invalid financial write input: {details}")

    def _holding_target(
        self,
        *,
        target_type: str,
        before: Optional[Holding],
        target: Optional[Holding],
    ) -> dict[str, Any]:
        identity_source = target or before
        if identity_source is None:
            raise ValueError("holding target requires before or target state")
        return {
            "type": target_type,
            "identity": {
                "asset_id": identity_source.asset_id,
                "account": identity_source.account,
                "broker": identity_source.broker or "",
            },
            "before": self.manager.compensation.serialize_holding(before),
            "target": self.manager.compensation.serialize_holding(target),
        }

    def _apply_targets_after_ledger(
        self,
        *,
        operation: str,
        account: str,
        related_record_id: Optional[str],
        ledger: CashFlow,
        ledger_step: str,
        targets: list[dict[str, Any]],
    ) -> None:
        completed_steps = [ledger_step]
        for index, target in enumerate(targets):
            step = f"target[{index}]/{target.get('type')}"
            try:
                self.manager.compensation.apply_target(target)
                completed_steps.append(step)
            except Exception as exc:
                payload = {
                    "ledger": ledger.model_dump(mode="json"),
                    "targets": targets,
                    "completed_target_indexes": list(range(index)),
                }
                try:
                    task = self.manager._record_compensation(
                        operation_type=f"{operation}_TARGETS_INCOMPLETE",
                        account=account,
                        related_record_id=related_record_id,
                        payload=payload,
                        error=exc,
                    )
                except Exception as compensation_error:
                    raise PartialWriteError(
                        operation=operation,
                        account=account,
                        related_record_id=related_record_id,
                        completed_steps=completed_steps,
                        failed_step=step,
                        task_id=None,
                        target_count=len(targets),
                        compensation_persisted=False,
                        original_error=f"{exc}; compensation persistence failed: {compensation_error}",
                    ) from exc
                raise PartialWriteError(
                    operation=operation,
                    account=account,
                    related_record_id=related_record_id,
                    completed_steps=completed_steps,
                    failed_step=step,
                    task_id=task.task_id,
                    target_count=len(targets),
                    compensation_persisted=True,
                    original_error=exc,
                ) from exc

    def deposit(
        self,
        flow_date: date,
        account: str,
        amount: float,
        currency: str,
        cny_amount: Optional[float] = None,
        exchange_rate: Optional[float] = None,
        source: str = "",
        remark: str = "",
    ) -> CashFlow:
        raise RuntimeError("cash_flow_entry_disabled")

    def withdraw(
        self,
        flow_date: date,
        account: str,
        amount: float,
        currency: str,
        cny_amount: Optional[float] = None,
        exchange_rate: Optional[float] = None,
        remark: str = "",
    ) -> CashFlow:
        raise RuntimeError("cash_flow_entry_disabled")

    def _cash_flow_locked(self, **kwargs) -> CashFlow:
        raise RuntimeError("cash_flow_entry_disabled")
