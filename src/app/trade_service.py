"""Cash-flow write application service."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.app.compensation_service import PartialWriteError
from src.models import CashFlow, Holding
from src.process_lock import account_lock_key, process_lock
from src.write_guard import validate_and_normalize_cash_flow_input


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
        self._require_valid(validate_and_normalize_cash_flow_input(
            amount=amount, cny_amount=cny_amount, exchange_rate=exchange_rate,
        ))
        with process_lock(account_lock_key(account)):
            return self._cash_flow_locked(
                flow_type="DEPOSIT",
                flow_date=flow_date,
                account=account,
                amount=amount,
                currency=currency,
                cny_amount=cny_amount,
                exchange_rate=exchange_rate,
                source=source,
                remark=remark,
            )

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
        self._require_valid(validate_and_normalize_cash_flow_input(
            amount=amount, cny_amount=cny_amount, exchange_rate=exchange_rate,
        ))
        with process_lock(account_lock_key(account)):
            return self._cash_flow_locked(
                flow_type="WITHDRAW",
                flow_date=flow_date,
                account=account,
                amount=amount,
                currency=currency,
                cny_amount=cny_amount,
                exchange_rate=exchange_rate,
                source="",
                remark=remark,
            )

    def _cash_flow_locked(self, **kwargs) -> CashFlow:
        cf_payload = self.manager._normalize_cash_flow_payload(
            amount=kwargs["amount"],
            currency=kwargs["currency"],
            cny_amount=kwargs["cny_amount"],
            exchange_rate=kwargs["exchange_rate"],
        )
        direction = 1 if kwargs["flow_type"] == "DEPOSIT" else -1
        before_cash, target_cash = self.cash_service.plan_cash_holding_target(
            kwargs["account"], direction * cf_payload["amount"], kwargs["currency"]
        )
        targets = [self._holding_target(
            target_type="CASH_TARGET_SET", before=before_cash, target=target_cash,
        )]
        cf = CashFlow(
            flow_date=kwargs["flow_date"],
            account=kwargs["account"],
            amount=direction * cf_payload["amount"],
            currency=kwargs["currency"],
            cny_amount=direction * cf_payload["cny_amount"],
            exchange_rate=cf_payload["exchange_rate"],
            flow_type=kwargs["flow_type"],
            source=kwargs["source"],
            remark=kwargs["remark"],
        )
        cf = self.storage.add_cash_flow(cf)
        if getattr(cf, "was_replayed", False) is True:
            return cf
        self._apply_targets_after_ledger(
            operation=kwargs["flow_type"],
            account=kwargs["account"],
            related_record_id=cf.record_id,
            ledger=cf,
            ledger_step="cash_flow_created",
            targets=targets,
        )
        return cf
