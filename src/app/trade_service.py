"""Trade and cash-flow application service."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.models import AssetClass, AssetType, CashFlow, Holding, Transaction, TransactionType
from src.process_lock import account_lock_key, process_lock
from src.write_guard import validate_and_normalize_cash_flow_input, validate_and_normalize_trade_input


class TradeService:
    """Coordinate transaction/cash-flow writes and repair-task recording."""

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

    def _buy_locked(
        self,
        *,
        tx_date: date,
        asset_id: str,
        asset_name: str,
        asset_type: AssetType,
        account: str,
        quantity: float,
        price: float,
        currency: str,
        broker: Optional[str],
        fee: float,
        remark: str,
        asset_class: Optional[AssetClass],
        industry: Optional[str],
        auto_deduct_cash: bool,
        request_id: Optional[str],
    ) -> Transaction:
        full_asset_name = self.manager._get_asset_name(asset_id, asset_name)
        if full_asset_name != asset_name:
            print(f"[名称自动补全] {asset_name} -> {full_asset_name}")

        tx_payload = self.manager._normalize_transaction_payload(quantity=quantity, price=price, fee=fee)
        total_cost = float(self.manager._quantize_money(
            self.manager._to_decimal(tx_payload["amount"]) + self.manager._to_decimal(tx_payload["fee"])
        ))
        if auto_deduct_cash and currency == "CNY" and not self.cash_service.has_sufficient_cash(account, total_cost):
            raise ValueError(f"账户 {account} 现金不足，需要 ¥{total_cost:,.2f}")

        tx = Transaction(
            tx_date=tx_date,
            tx_type=TransactionType.BUY,
            asset_id=asset_id,
            asset_name=full_asset_name,
            asset_type=asset_type,
            account=account,
            broker=broker,
            quantity=tx_payload["quantity"],
            price=tx_payload["price"],
            amount=tx_payload["amount"],
            currency=currency,
            fee=tx_payload["fee"],
            remark=remark,
            request_id=request_id,
        )
        try:
            tx = self.storage.add_transaction(tx)
        except Exception as exc:
            print(f"[买入失败] 记录交易失败: {exc}")
            raise
        if getattr(tx, "was_replayed", False) is True:
            return tx

        holding_payload = self.manager._normalize_holding_payload(quantity=quantity)
        holding = Holding(
            asset_id=asset_id,
            asset_name=full_asset_name,
            asset_type=asset_type,
            account=account,
            broker=broker,
            quantity=holding_payload["quantity"],
            currency=currency,
            asset_class=asset_class,
            industry=industry,
        )
        try:
            self.storage.upsert_holding(holding)
        except Exception as exc:
            print(f"[警告] 持仓更新失败，但交易已记录: {exc}")
            self.manager._record_compensation(
                operation_type="BUY_HOLDING_UPSERT_FAILED",
                account=account,
                related_record_id=tx.record_id,
                payload={"transaction": tx.model_dump(mode="json"), "holding_delta": holding.model_dump(mode="json")},
                error=exc,
            )

        if auto_deduct_cash and currency == "CNY":
            try:
                cash_deducted = self.cash_service.deduct_cash(account, total_cost)
                if not cash_deducted:
                    print(f"[警告] 买入交易已记录，但现金扣减失败。请手动调整账户 {account} 的现金余额 ¥{total_cost:,.2f}")
                    self.manager._record_compensation(
                        operation_type="BUY_CASH_DEDUCT_FAILED",
                        account=account,
                        related_record_id=tx.record_id,
                        payload={"transaction": tx.model_dump(mode="json"), "cash_delta": -total_cost, "currency": currency},
                        error="cash deduction returned False",
                    )
            except Exception as exc:
                print(f"[警告] 现金扣减异常: {exc}")
                self.manager._record_compensation(
                    operation_type="BUY_CASH_DEDUCT_EXCEPTION",
                    account=account,
                    related_record_id=tx.record_id,
                    payload={"transaction": tx.model_dump(mode="json"), "cash_delta": -total_cost, "currency": currency},
                    error=exc,
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

    def _sell_locked(
        self,
        *,
        tx_date: date,
        asset_id: str,
        account: str,
        quantity: float,
        price: float,
        currency: str,
        broker: Optional[str],
        fee: float,
        remark: str,
        auto_add_cash: bool,
        request_id: Optional[str],
    ) -> Transaction:
        holding = self.storage.get_holding(asset_id, account, broker)
        if not holding:
            raise ValueError(f"未找到持仓: {asset_id} (account={account}, broker={broker or ''})")
        if self.manager._to_decimal(holding.quantity) < self.manager._to_decimal(quantity):
            raise ValueError(f"持仓不足: {asset_id} 可用 {holding.quantity}，卖出 {quantity}")

        tx_payload = self.manager._normalize_transaction_payload(quantity=-quantity, price=price, fee=fee)
        tx = Transaction(
            tx_date=tx_date,
            tx_type=TransactionType.SELL,
            asset_id=asset_id,
            asset_name=holding.asset_name,
            asset_type=holding.asset_type,
            account=account,
            broker=broker,
            quantity=tx_payload["quantity"],
            price=tx_payload["price"],
            amount=tx_payload["amount"],
            currency=currency,
            fee=tx_payload["fee"],
            remark=remark,
            request_id=request_id,
        )
        try:
            tx = self.storage.add_transaction(tx)
        except Exception as exc:
            print(f"[卖出失败] 记录交易失败: {exc}")
            raise
        if getattr(tx, "was_replayed", False) is True:
            return tx

        sell_holding_payload = self.manager._normalize_holding_payload(quantity=-quantity)
        try:
            self.storage.update_holding_quantity(asset_id, account, sell_holding_payload["quantity"], broker)
        except Exception as exc:
            print(f"[警告] 持仓更新失败，但交易已记录: {exc}")
            self.manager._record_compensation(
                operation_type="SELL_HOLDING_UPDATE_FAILED",
                account=account,
                related_record_id=tx.record_id,
                payload={
                    "transaction": tx.model_dump(mode="json"),
                    "asset_id": asset_id,
                    "broker": broker,
                    "quantity_delta": sell_holding_payload["quantity"],
                },
                error=exc,
            )

        try:
            self.storage.delete_holding_if_zero(asset_id, account, broker)
        except Exception as exc:
            print(f"[警告] 零持仓清理失败，但交易已记录: {exc}")
            self.manager._record_compensation(
                operation_type="SELL_ZERO_HOLDING_DELETE_FAILED",
                account=account,
                related_record_id=tx.record_id,
                payload={"transaction": tx.model_dump(mode="json"), "asset_id": asset_id, "broker": broker},
                error=exc,
            )

        if auto_add_cash and currency == "CNY":
            gross_proceeds = self.manager._quantize_money(
                self.manager._to_decimal(quantity) * self.manager._to_decimal(price)
            )
            total_proceeds = float(self.manager._quantize_money(
                self.manager._to_decimal(gross_proceeds) - self.manager._to_decimal(tx_payload["fee"])
            ))
            try:
                self.cash_service.add_cash(account, total_proceeds)
            except Exception as exc:
                print(f"[警告] 现金增加异常: {exc}")
                self.manager._record_compensation(
                    operation_type="SELL_CASH_ADD_FAILED",
                    account=account,
                    related_record_id=tx.record_id,
                    payload={"transaction": tx.model_dump(mode="json"), "cash_delta": total_proceeds, "currency": currency},
                    error=exc,
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
            return self._deposit_locked(
                flow_date=flow_date,
                account=account,
                amount=amount,
                currency=currency,
                cny_amount=cny_amount,
                exchange_rate=exchange_rate,
                source=source,
                remark=remark,
            )

    def _deposit_locked(self, **kwargs) -> CashFlow:
        cf_payload = self.manager._normalize_cash_flow_payload(
            amount=kwargs["amount"],
            currency=kwargs["currency"],
            cny_amount=kwargs["cny_amount"],
            exchange_rate=kwargs["exchange_rate"],
        )
        cf = CashFlow(
            flow_date=kwargs["flow_date"],
            account=kwargs["account"],
            amount=cf_payload["amount"],
            currency=kwargs["currency"],
            cny_amount=cf_payload["cny_amount"],
            exchange_rate=cf_payload["exchange_rate"],
            flow_type="DEPOSIT",
            source=kwargs["source"],
            remark=kwargs["remark"],
        )
        cf = self.storage.add_cash_flow(cf)
        if getattr(cf, "was_replayed", False) is True:
            return cf
        try:
            self.cash_service.update_cash_holding(kwargs["account"], cf_payload["amount"], kwargs["currency"], cf_payload["cny_amount"])
        except Exception as exc:
            self.manager._record_compensation(
                operation_type="DEPOSIT_CASH_HOLDING_UPDATE_FAILED",
                account=kwargs["account"],
                related_record_id=cf.record_id,
                payload={
                    "cash_flow": cf.model_dump(mode="json"),
                    "cash_delta": cf_payload["amount"],
                    "currency": kwargs["currency"],
                    "cny_amount": cf_payload["cny_amount"],
                },
                error=exc,
            )
            raise
        return cf

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
            return self._withdraw_locked(
                flow_date=flow_date,
                account=account,
                amount=amount,
                currency=currency,
                cny_amount=cny_amount,
                exchange_rate=exchange_rate,
                remark=remark,
            )

    def _withdraw_locked(self, **kwargs) -> CashFlow:
        cf_payload = self.manager._normalize_cash_flow_payload(
            amount=kwargs["amount"],
            currency=kwargs["currency"],
            cny_amount=kwargs["cny_amount"],
            exchange_rate=kwargs["exchange_rate"],
        )
        cf = CashFlow(
            flow_date=kwargs["flow_date"],
            account=kwargs["account"],
            amount=-cf_payload["amount"],
            currency=kwargs["currency"],
            cny_amount=-cf_payload["cny_amount"],
            exchange_rate=cf_payload["exchange_rate"],
            flow_type="WITHDRAW",
            remark=kwargs["remark"],
        )
        cf = self.storage.add_cash_flow(cf)
        if getattr(cf, "was_replayed", False) is True:
            return cf
        try:
            self.cash_service.update_cash_holding(kwargs["account"], -cf_payload["amount"], kwargs["currency"], -cf_payload["cny_amount"])
        except Exception as exc:
            self.manager._record_compensation(
                operation_type="WITHDRAW_CASH_HOLDING_UPDATE_FAILED",
                account=kwargs["account"],
                related_record_id=cf.record_id,
                payload={
                    "cash_flow": cf.model_dump(mode="json"),
                    "cash_delta": -cf_payload["amount"],
                    "currency": kwargs["currency"],
                    "cny_amount": -cf_payload["cny_amount"],
                },
                error=exc,
            )
            raise
        return cf
