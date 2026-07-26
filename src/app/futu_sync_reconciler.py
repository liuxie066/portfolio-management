from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from src.models import (
    AssetType,
    CASH_ASSET_ID,
    HKD_CASH_ASSET_ID,
    MMF_ASSET_ID,
    USD_CASH_ASSET_ID,
)

_POSITION_TYPES = {
    AssetType.A_STOCK,
    AssetType.HK_STOCK,
    AssetType.US_STOCK,
    AssetType.EXCHANGE_FUND,
    AssetType.CN_FUND,
    AssetType.HK_FUND,
    AssetType.US_FUND,
}
_CASH_ASSETS = {
    "CNY": CASH_ASSET_ID,
    "USD": USD_CASH_ASSET_ID,
    "HKD": HKD_CASH_ASSET_ID,
}


class FutuSyncReconciler:
    def __init__(
        self,
        storage: Any,
        *,
        wait: Callable[[float], None] = time.sleep,
        retry_seconds: float = 30,
    ) -> None:
        self.storage = storage
        self.wait = wait
        self.retry_seconds = retry_seconds

    def reconcile(self, snapshot: Any, *, account: str, broker: str) -> dict[str, Any]:
        immediate = self._read_and_compare(snapshot, account=account, broker=broker)
        mismatched = [key for key, value in immediate.items() if value["status"] != "trusted"]
        if not mismatched:
            return {
                "status": "trusted",
                "retry_performed": False,
                "datasets": immediate,
            }
        self.wait(self.retry_seconds)
        retried = self._read_and_compare(snapshot, account=account, broker=broker)
        return {
            "status": _overall_status(retried),
            "retry_performed": True,
            "retry_seconds": self.retry_seconds,
            "initial_mismatches": mismatched,
            "datasets": retried,
        }

    def reconcile_balances(self, snapshot: Any, *, account: str, broker: str) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            return {
                "pm.securities_cash": self._cash_by_currency_verdict(
                    account=account,
                    broker=broker,
                    expected=snapshot.cash_by_currency,
                ),
                "pm.fund_mmf": self._cash_verdict(
                    account=account,
                    broker=broker,
                    asset_id=MMF_ASSET_ID,
                    expected=snapshot.mmf,
                    reason_code="FUND_MMF_MISMATCH",
                ),
            }

        immediate = read()
        mismatched = [key for key, value in immediate.items() if value["status"] != "trusted"]
        if not mismatched:
            return {"status": "trusted", "retry_performed": False, "datasets": immediate}
        self.wait(self.retry_seconds)
        retried = read()
        return {
            "status": _overall_status(retried),
            "retry_performed": True,
            "retry_seconds": self.retry_seconds,
            "initial_mismatches": mismatched,
            "datasets": retried,
        }

    def _read_and_compare(self, snapshot: Any, *, account: str, broker: str) -> dict[str, Any]:
        positions = self.storage.get_holdings(account=account, include_empty=True)
        actual = {
            item.asset_id: item
            for item in positions
            if (item.broker or "") == broker and item.asset_type in _POSITION_TYPES
        }
        expected_positions = {
            item.asset_id: item
            for item in snapshot.positions
            if item.security_type in {"STOCK", "ETF"} and item.quantity != 0
        }
        quantity_diff = []
        cost_diff = []
        for asset_id in sorted(set(expected_positions) | set(actual)):
            expected = expected_positions.get(asset_id)
            stored = actual.get(asset_id)
            expected_quantity = _decimal(expected.quantity if expected else 0)
            actual_quantity = _decimal(stored.quantity if stored else 0)
            if expected_quantity != actual_quantity:
                quantity_diff.append(asset_id)
            expected_cost = _money(expected.average_cost) if expected else None
            actual_cost = _money(stored.avg_cost) if stored else None
            if expected_quantity != 0 and expected_cost != actual_cost:
                cost_diff.append(asset_id)
        return {
            "pm.holdings_quantity": _verdict(quantity_diff, "HOLDINGS_QUANTITY_MISMATCH"),
            "pm.cost_basis": _verdict(cost_diff, "COST_BASIS_MISMATCH"),
            "pm.securities_cash": self._cash_by_currency_verdict(
                account=account,
                broker=broker,
                expected=snapshot.cash_by_currency,
            ),
            "pm.fund_mmf": self._cash_verdict(
                account=account,
                broker=broker,
                asset_id=MMF_ASSET_ID,
                expected=snapshot.mmf,
                reason_code="FUND_MMF_MISMATCH",
            ),
        }

    def _cash_verdict(
        self,
        *,
        account: str,
        broker: str,
        asset_id: str,
        expected: Any,
        reason_code: str,
    ) -> dict[str, Any]:
        try:
            stored = self.storage.get_holding(asset_id, account, broker=broker)
        except Exception:
            return {
                "status": "unavailable",
                "reason_code": "REPOSITORY_READ_FAILED",
                "diff_count": 0,
                "diff_subjects": [],
            }
        matches = stored is not None and _money(stored.quantity) == _money(expected)
        return _verdict([] if matches else [asset_id], reason_code)

    def _cash_by_currency_verdict(
        self,
        *,
        account: str,
        broker: str,
        expected: Any,
    ) -> dict[str, Any]:
        expected_by_currency = dict(expected or {})
        differences: list[str] = []
        for currency, asset_id in _CASH_ASSETS.items():
            try:
                stored = self.storage.get_holding(
                    asset_id,
                    account,
                    broker=broker,
                )
            except Exception:
                return {
                    "status": "unavailable",
                    "reason_code": "REPOSITORY_READ_FAILED",
                    "diff_count": 0,
                    "diff_subjects": [],
                }
            expected_amount = expected_by_currency.get(currency)
            if (
                expected_amount is None
                or stored is None
                or _money(stored.quantity) != _money(expected_amount)
            ):
                differences.append(asset_id)
        return _verdict(differences, "SECURITIES_CASH_MISMATCH")


def _verdict(diff: Sequence[str], reason_code: str) -> dict[str, Any]:
    return {
        "status": "trusted" if not diff else "untrusted",
        "reason_code": "REPLICA_MATCHED" if not diff else reason_code,
        "diff_count": len(diff),
        "diff_subjects": list(diff),
    }


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _overall_status(datasets: dict[str, dict[str, Any]]) -> str:
    statuses = {item["status"] for item in datasets.values()}
    if "unavailable" in statuses:
        return "unavailable"
    if statuses == {"trusted"}:
        return "trusted"
    return "untrusted"
