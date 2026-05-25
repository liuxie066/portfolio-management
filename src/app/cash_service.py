"""Cash holding side-effect service."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from src.models import (
    AssetClass,
    AssetType,
    CASH_ASSET_ID,
    Currency,
    HKD_CASH_ASSET_ID,
    MMF_ASSET_ID,
    USD_CASH_ASSET_ID,
    Holding,
)


class CashService:
    MONEY_QUANT = Decimal("0.01")

    def __init__(self, storage: Any):
        self.storage = storage

    @staticmethod
    def to_decimal(value: Any) -> Decimal:
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @classmethod
    def quantize_money(cls, value: Any) -> Decimal:
        return cls.to_decimal(value).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def cash_asset_id_for_currency(cls, currency: str) -> str:
        if currency == Currency.CNY:
            return CASH_ASSET_ID
        if currency == Currency.USD:
            return USD_CASH_ASSET_ID
        if currency == Currency.HKD:
            return HKD_CASH_ASSET_ID
        return f"{currency}-CASH"

    def update_cash_holding(self, account: str, amount: float, currency: str, cny_amount: float = None) -> None:
        asset_id = self.cash_asset_id_for_currency(currency)
        cash_holding = self.storage.get_holding(asset_id, account)
        quantity = float(self.quantize_money(amount))

        if cash_holding:
            self.storage.update_holding_quantity(asset_id, account, quantity)
            return

        holding = Holding(
            asset_id=asset_id,
            asset_name=f"{currency}现金",
            asset_type=AssetType.CASH,
            account=account,
            quantity=quantity,
            currency=currency,
            asset_class=AssetClass.CASH,
            industry="现金",
        )
        self.storage.upsert_holding(holding)

    def sync_cash_like_balance(
        self,
        *,
        account: str,
        asset_id: str,
        asset_name: str,
        asset_type: AssetType,
        target: float,
        broker: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Sync a cash-like holding to an absolute target balance.

        Use this for broker/API balance sync. Delta-style cash operations should
        keep using ``update_cash_holding`` / ``add_cash`` / ``deduct_cash``.
        """
        target_qty = float(self.quantize_money(target))
        existing = self.storage.get_holding(asset_id, account, broker)
        current_qty = float(self.quantize_money(existing.quantity if existing else 0))
        delta = float(self.quantize_money(target_qty - current_qty))
        created = existing is None
        replacement = Holding(
            asset_id=asset_id,
            asset_name=asset_name,
            asset_type=asset_type,
            account=account,
            broker=broker,
            quantity=target_qty,
            currency="CNY",
            asset_class=AssetClass.CASH,
            industry="现金",
        )

        field_updates = {}
        if existing:
            comparable_fields = {
                "asset_name": asset_name,
                "asset_type": asset_type,
                "currency": "CNY",
                "asset_class": AssetClass.CASH,
                "industry": "现金",
            }
            for field, target_value in comparable_fields.items():
                current_value = getattr(existing, field, None)
                if hasattr(current_value, "value"):
                    current_value = current_value.value
                if hasattr(target_value, "value"):
                    target_value = target_value.value
                if current_value != target_value:
                    field_updates[field] = target_value

        fields_changed = bool(field_updates)
        updated = bool(created or delta != 0 or fields_changed)

        if not dry_run and updated:
            if existing:
                replace_holding = getattr(self.storage, "replace_holding", None)
                if callable(replace_holding) and hasattr(type(self.storage), "replace_holding"):
                    replace_holding(replacement)
                else:
                    self.storage.update_holding_quantity(asset_id, account, delta, broker)
            else:
                self.storage.upsert_holding(replacement)

        return {
            "asset_id": asset_id,
            "asset_name": asset_name,
            "current": current_qty,
            "target": target_qty,
            "delta": delta,
            "created": created,
            "updated": updated,
            "fields_changed": fields_changed,
            "field_updates": field_updates,
        }

    def get_cash_like_holdings(self, account: str):
        cash_holding = self.storage.get_holding(CASH_ASSET_ID, account)
        mmf_holding = self.storage.get_holding(MMF_ASSET_ID, account)
        return cash_holding, mmf_holding

    def get_cash(self, account: str) -> dict[str, Any]:
        try:
            holdings = self.storage.get_holdings(account=account)
            cash_holdings = [h for h in holdings if h.asset_type in [AssetType.CASH, AssetType.MMF]]

            items = []
            by_currency = {}
            for holding in cash_holdings:
                currency = holding.currency or "CNY"
                asset_type = holding.asset_type.value if hasattr(holding.asset_type, "value") else holding.asset_type
                items.append({
                    "code": holding.asset_id,
                    "name": holding.asset_name,
                    "amount": holding.quantity,
                    "currency": currency,
                    "type": asset_type,
                })
                by_currency[currency] = by_currency.get(currency, 0) + holding.quantity

            return {
                "success": True,
                "by_currency": by_currency,
                "items": items,
                "count": len(items),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def has_sufficient_cash(self, account: str, amount: float) -> bool:
        if amount <= 0:
            return True

        cash_holding, mmf_holding = self.get_cash_like_holdings(account)
        total_cash = Decimal("0")
        if cash_holding and cash_holding.quantity > 0:
            total_cash += self.to_decimal(cash_holding.quantity)
        if mmf_holding and mmf_holding.quantity > 0:
            total_cash += self.to_decimal(mmf_holding.quantity)
        return total_cash >= self.to_decimal(amount)

    def deduct_cash(self, account: str, amount: float) -> bool:
        if amount <= 0:
            return True

        remaining = self.to_decimal(amount)
        cash_holding, mmf_holding = self.get_cash_like_holdings(account)

        # Pre-validate: check total available before any writes
        total_available = Decimal("0")
        if cash_holding and cash_holding.quantity > 0:
            total_available += self.to_decimal(cash_holding.quantity)
        if mmf_holding and mmf_holding.quantity > 0:
            total_available += self.to_decimal(mmf_holding.quantity)
        if total_available < remaining:
            print(f"  ✗ 现金不足，需要: ¥{float(self.quantize_money(remaining)):,.2f}，可用: ¥{float(self.quantize_money(total_available)):,.2f}")
            return False

        if cash_holding and cash_holding.quantity > 0:
            cash_qty = self.to_decimal(cash_holding.quantity)
            deduct_from_cash = min(cash_qty, remaining)
            self.storage.update_holding_quantity(CASH_ASSET_ID, account, float(-self.quantize_money(deduct_from_cash)))
            remaining -= deduct_from_cash
            print(f"  从 {CASH_ASSET_ID} 扣除: ¥{float(self.quantize_money(deduct_from_cash)):,.2f}")

        if remaining > 0 and mmf_holding and mmf_holding.quantity > 0:
            mmf_qty = self.to_decimal(mmf_holding.quantity)
            deduct_from_mmf = min(mmf_qty, remaining)
            self.storage.update_holding_quantity(MMF_ASSET_ID, account, float(-self.quantize_money(deduct_from_mmf)))
            remaining -= deduct_from_mmf
            print(f"  从 {MMF_ASSET_ID} 扣除: ¥{float(self.quantize_money(deduct_from_mmf)):,.2f}")

        return True

    def add_cash(self, account: str, amount: float) -> bool:
        if amount <= 0:
            return True

        amount_dec = self.quantize_money(amount)
        cash_holding = self.storage.get_holding(CASH_ASSET_ID, account)

        if cash_holding:
            self.storage.update_holding_quantity(CASH_ASSET_ID, account, float(amount_dec))
        else:
            holding = Holding(
                asset_id=CASH_ASSET_ID,
                asset_name="人民币现金",
                asset_type=AssetType.CASH,
                account=account,
                quantity=float(amount_dec),
                currency="CNY",
                asset_class=AssetClass.CASH,
                industry="现金",
            )
            self.storage.upsert_holding(holding)

        print(f"  增加到 {CASH_ASSET_ID}: ¥{float(amount_dec):,.2f}")
        return True
