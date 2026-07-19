"""Trade and cash-flow application service."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.app.compensation_service import PartialWriteError
from src.models import AssetClass, AssetType, CashFlow, Holding, Transaction, TransactionType
from src.process_lock import account_lock_key, process_lock
from src.write_guard import validate_and_normalize_cash_flow_input, validate_and_normalize_trade_input


class TradeService:
    """Coordinate ledger writes and absolute holding/cash targets."""

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
        ledger: Transaction | CashFlow,
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

    def buy(
        self,
        tx_date: date,
        asset_id: str,
        asset_name: str,
        asset_type: AssetType,
        account: str,
        quantity: float,
        price: float,
        currency: str,
        broker: Optional[str] = None,
        fee: float = 0,
        remark: str = "",
        asset_class: Optional[AssetClass] = None,
        industry: Optional[str] = None,
        auto_deduct_cash: bool = True,
        request_id: str = None,
    ) -> Transaction:
        self._require_valid(validate_and_normalize_trade_input(
            tx_type="BUY", quantity=quantity, price=price, fee=fee,
        ))
        with process_lock(account_lock_key(account)):
            return self._buy_locked(
                tx_date=tx_date,
                asset_id=asset_id,
                asset_name=asset_name,
                asset_type=asset_type,
                account=account,
                quantity=quantity,
                price=price,
                currency=currency,
                broker=broker,
                fee=fee,
                remark=remark,
                asset_class=asset_class,
                industry=industry,
                auto_deduct_cash=auto_deduct_cash,
                request_id=request_id,
            )

    def _buy_locked(self, **kwargs) -> Transaction:
        account = kwargs["account"]
        full_asset_name = self.manager._get_asset_name(kwargs["asset_id"], kwargs["asset_name"])
        tx_payload = self.manager._normalize_transaction_payload(
            quantity=kwargs["quantity"], price=kwargs["price"], fee=kwargs["fee"]
        )
        total_cost = float(self.manager._quantize_money(
            self.manager._to_decimal(tx_payload["amount"]) + self.manager._to_decimal(tx_payload["fee"])
        ))

        before_holding = self.storage.get_holding(kwargs["asset_id"], account, kwargs["broker"])
        if before_holding:
            target_holding = Holding(**before_holding.model_dump())
            target_holding.quantity = before_holding.quantity + tx_payload["quantity"]
            target_holding.asset_name = full_asset_name or before_holding.asset_name
        else:
            target_holding = Holding(
                asset_id=kwargs["asset_id"],
                asset_name=full_asset_name,
                asset_type=kwargs["asset_type"],
                account=account,
                broker=kwargs["broker"],
                quantity=tx_payload["quantity"],
                currency=kwargs["currency"],
                asset_class=kwargs["asset_class"],
                industry=kwargs["industry"],
            )
        targets = [self._holding_target(
            target_type="HOLDING_TARGET_SET", before=before_holding, target=target_holding,
        )]
        if kwargs["auto_deduct_cash"] and kwargs["currency"] == "CNY":
            for before, target in self.cash_service.plan_deduct_cash_targets(account, total_cost):
                targets.append(self._holding_target(target_type="CASH_TARGET_SET", before=before, target=target))

        tx = Transaction(
            tx_date=kwargs["tx_date"],
            tx_type=TransactionType.BUY,
            asset_id=kwargs["asset_id"],
            asset_name=full_asset_name,
            asset_type=kwargs["asset_type"],
            account=account,
            broker=kwargs["broker"],
            quantity=tx_payload["quantity"],
            price=tx_payload["price"],
            amount=tx_payload["amount"],
            currency=kwargs["currency"],
            fee=tx_payload["fee"],
            remark=kwargs["remark"],
            request_id=kwargs["request_id"],
        )
        tx = self.storage.add_transaction(tx)
        if getattr(tx, "was_replayed", False) is True:
            return tx
        self._apply_targets_after_ledger(
            operation="BUY",
            account=account,
            related_record_id=tx.record_id,
            ledger=tx,
            ledger_step="transaction_created",
            targets=targets,
        )
        return tx

    def sell(
        self,
        tx_date: date,
        asset_id: str,
        account: str,
        quantity: float,
        price: float,
        currency: str,
        broker: Optional[str] = None,
        fee: float = 0,
        remark: str = "",
        auto_add_cash: bool = True,
        request_id: str = None,
    ) -> Transaction:
        self._require_valid(validate_and_normalize_trade_input(
            tx_type="SELL", quantity=quantity, price=price, fee=fee,
        ))
        with process_lock(account_lock_key(account)):
            return self._sell_locked(
                tx_date=tx_date,
                asset_id=asset_id,
                account=account,
                quantity=quantity,
                price=price,
                currency=currency,
                broker=broker,
                fee=fee,
                remark=remark,
                auto_add_cash=auto_add_cash,
                request_id=request_id,
            )

    def _sell_locked(self, **kwargs) -> Transaction:
        account = kwargs["account"]
        holding = self.storage.get_holding(kwargs["asset_id"], account, kwargs["broker"])
        if not holding:
            raise ValueError(
                f"未找到持仓: {kwargs['asset_id']} (account={account}, broker={kwargs['broker'] or ''})"
            )
        if self.manager._to_decimal(holding.quantity) < self.manager._to_decimal(kwargs["quantity"]):
            raise ValueError(f"持仓不足: {kwargs['asset_id']} 可用 {holding.quantity}，尝试卖出 {kwargs['quantity']}")

        tx_payload = self.manager._normalize_transaction_payload(
            quantity=-kwargs["quantity"], price=kwargs["price"], fee=kwargs["fee"]
        )
        total_proceeds = float(self.manager._quantize_money(
            -self.manager._to_decimal(tx_payload["amount"]) - self.manager._to_decimal(tx_payload["fee"])
        ))
        target_holding = Holding(**holding.model_dump())
        target_holding.quantity = holding.quantity + tx_payload["quantity"]
        if abs(target_holding.quantity) <= 1e-8:
            target_holding.quantity = 0.0
            holding_target = self._holding_target(
                target_type="HOLDING_ZERO_DELETE", before=holding, target=None,
            )
        else:
            holding_target = self._holding_target(
                target_type="HOLDING_TARGET_SET", before=holding, target=target_holding,
            )
        targets = [holding_target]
        if kwargs["auto_add_cash"] and kwargs["currency"] == "CNY":
            before_cash, target_cash = self.cash_service.plan_add_cash_target(account, total_proceeds)
            targets.append(self._holding_target(
                target_type="CASH_TARGET_SET", before=before_cash, target=target_cash,
            ))

        tx = Transaction(
            tx_date=kwargs["tx_date"],
            tx_type=TransactionType.SELL,
            asset_id=kwargs["asset_id"],
            asset_name=holding.asset_name,
            asset_type=holding.asset_type,
            account=account,
            broker=kwargs["broker"],
            quantity=tx_payload["quantity"],
            price=tx_payload["price"],
            amount=tx_payload["amount"],
            currency=kwargs["currency"],
            fee=tx_payload["fee"],
            remark=kwargs["remark"],
            request_id=kwargs["request_id"],
        )
        tx = self.storage.add_transaction(tx)
        if getattr(tx, "was_replayed", False) is True:
            return tx
        self._apply_targets_after_ledger(
            operation="SELL",
            account=account,
            related_record_id=tx.record_id,
            ledger=tx,
            ledger_step="transaction_created",
            targets=targets,
        )
        return tx

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
