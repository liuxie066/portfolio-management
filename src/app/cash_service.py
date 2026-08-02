"""Cash holding side-effect service."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from src.domain.holding_mutations import (
    HOLDING_REQUIRED_VALUE_FIELDS,
    HoldingTarget,
    canonical_holding,
)
from src.models import (
    AssetClass,
    AssetType,
    CASH_ASSET_ID,
    Currency,
    HKD_CASH_ASSET_ID,
    Industry,
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
        raise RuntimeError("cash_flow_entry_disabled")

    def sync_cash_like_balance(
        self,
        *,
        account: str,
        asset_id: str,
        asset_name: str,
        asset_type: AssetType,
        target: float,
        broker: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Sync a cash-like holding to an absolute target balance.

        CASH is rejected here and must use a confirmed cash-flow effect.
        The current broker sync caller uses this method only for MMF.
        """
        if asset_type == AssetType.CASH:
            raise RuntimeError("cash_effect_confirmation_required")
        target_qty = float(self.quantize_money(target))
        existing = self.storage.get_holding_fresh(asset_id, account, broker)
        current_qty = float(self.quantize_money(existing.quantity if existing else 0))
        delta = float(self.quantize_money(target_qty - current_qty))
        created = existing is None
        replacement = (
            canonical_holding(existing)
            if existing is not None
            else Holding(
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
        )

        field_updates = {}
        if existing:
            comparable_fields = {
                "asset_name": asset_name,
                "asset_type": asset_type,
                "currency": "CNY",
                "asset_class": AssetClass.CASH,
                "industry": Industry.CASH,
            }
            for field, declared_target_value in comparable_fields.items():
                current_value = getattr(existing, field, None)
                if hasattr(current_value, "value"):
                    current_value = current_value.value
                target_value = declared_target_value
                if hasattr(target_value, "value"):
                    target_value = target_value.value
                if current_value != target_value:
                    field_updates[field] = target_value
                    setattr(replacement, field, declared_target_value)
            replacement.quantity = target_qty

        fields_changed = bool(field_updates)
        updated = bool(created or delta != 0 or fields_changed)

        if not dry_run and updated:
            owned_fields = (
                ({"quantity"} if delta != 0 else set()) | set(field_updates)
                if existing is not None
                else set(HOLDING_REQUIRED_VALUE_FIELDS) | {
                    "asset_class",
                    "industry",
                }
            )
            self.storage.replace_holding(HoldingTarget.from_holdings(
                base=existing,
                target=replacement,
                owned_fields=owned_fields,
            ))

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
            "projected_fields": {
                **replacement.model_dump(
                    mode="json",
                    exclude={
                        "record_id",
                        "current_price",
                        "cny_price",
                        "market_value_cny",
                        "weight",
                    },
                ),
                "record_id": getattr(existing, "record_id", None),
            },
        }

    def get_cash_like_holdings(self, account: str, broker: str):
        cash_holding = self.storage.get_holding_fresh(CASH_ASSET_ID, account, broker)
        mmf_holding = self.storage.get_holding_fresh(MMF_ASSET_ID, account, broker)
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

    @staticmethod
    def _copy_with_quantity(holding: Holding, quantity: Any) -> Holding:
        replacement = Holding(**holding.model_dump())
        replacement.quantity = float(quantity)
        return replacement

    def plan_cash_holding_target(
        self,
        account: str,
        amount: float,
        currency: str,
        broker: str,
    ) -> tuple[Holding | None, Holding]:
        """Return the current and absolute target cash holding without writing."""
        asset_id = self.cash_asset_id_for_currency(currency)
        before = self.storage.get_holding_fresh(asset_id, account, broker)
        delta = self.quantize_money(amount)
        if before:
            target = self._copy_with_quantity(before, self.quantize_money(self.to_decimal(before.quantity) + delta))
        else:
            target = Holding(
                asset_id=asset_id,
                asset_name=f"{currency}现金",
                asset_type=AssetType.CASH,
                account=account,
                broker=broker,
                quantity=float(delta),
                currency=currency,
                asset_class=AssetClass.CASH,
                industry="现金",
            )
        return before, target

    def plan_add_cash_target(
        self,
        account: str,
        amount: float,
        broker: str,
    ) -> tuple[Holding | None, Holding]:
        return self.plan_cash_holding_target(account, amount, "CNY", broker)

    def plan_deduct_cash_targets(
        self,
        account: str,
        amount: float,
        broker: str,
    ) -> list[tuple[Holding, Holding]]:
        """Plan absolute CASH/MMF targets in the same order as cash deduction."""
        remaining = self.to_decimal(amount)
        cash_holding, mmf_holding = self.get_cash_like_holdings(account, broker)
        total_available = sum(
            (self.to_decimal(holding.quantity) for holding in (cash_holding, mmf_holding) if holding and holding.quantity > 0),
            Decimal("0"),
        )
        if total_available < remaining:
            raise ValueError(
                f"账户 {account} 现金不足，需要 ¥{float(self.quantize_money(remaining)):,.2f}，"
                f"可用 ¥{float(self.quantize_money(total_available)):,.2f}"
            )

        targets: list[tuple[Holding, Holding]] = []
        for holding in (cash_holding, mmf_holding):
            if remaining <= 0 or not holding or holding.quantity <= 0:
                continue
            deduction = min(self.to_decimal(holding.quantity), remaining)
            target_quantity = self.quantize_money(self.to_decimal(holding.quantity) - deduction)
            targets.append((holding, self._copy_with_quantity(holding, target_quantity)))
            remaining -= deduction
        return targets

    def deduct_cash(self, account: str, amount: float, broker: str) -> bool:
        if amount <= 0:
            return True

        remaining = self.to_decimal(amount)
        cash_holding, mmf_holding = self.get_cash_like_holdings(account, broker)

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
            self.storage.update_holding_quantity(
                CASH_ASSET_ID,
                account,
                float(-self.quantize_money(deduct_from_cash)),
                broker,
            )
            remaining -= deduct_from_cash
            print(f"  从 {CASH_ASSET_ID} 扣除: ¥{float(self.quantize_money(deduct_from_cash)):,.2f}")

        if remaining > 0 and mmf_holding and mmf_holding.quantity > 0:
            mmf_qty = self.to_decimal(mmf_holding.quantity)
            deduct_from_mmf = min(mmf_qty, remaining)
            self.storage.update_holding_quantity(
                MMF_ASSET_ID,
                account,
                float(-self.quantize_money(deduct_from_mmf)),
                broker,
            )
            remaining -= deduct_from_mmf
            print(f"  从 {MMF_ASSET_ID} 扣除: ¥{float(self.quantize_money(deduct_from_mmf)):,.2f}")

        return True

    def add_cash(self, account: str, amount: float) -> bool:
        raise RuntimeError("cash_flow_entry_disabled")
