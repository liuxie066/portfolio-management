from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from src.models import (
    AssetType,
    CASH_ASSET_ID,
    MMF_ASSET_ID,
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
_AGGREGATE_CASH_DATASET = "pm.cash_aggregate"


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
        mismatched = _retryable_mismatches(immediate)
        if not mismatched:
            return {
                "status": _overall_status(immediate),
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
            try:
                holdings = self._fresh_holdings(account)
            except Exception:
                return {
                    _AGGREGATE_CASH_DATASET: _unavailable_verdict(),
                    "pm.fund_mmf": _unavailable_verdict(),
                }
            return {
                _AGGREGATE_CASH_DATASET: self._aggregate_cash_verdict(
                    holdings,
                    account=account,
                    broker=broker,
                ),
                "pm.fund_mmf": self._cash_verdict(
                    holdings,
                    account=account,
                    broker=broker,
                    asset_id=MMF_ASSET_ID,
                    expected=snapshot.mmf,
                    reason_code="FUND_MMF_MISMATCH",
                ),
            }

        immediate = read()
        mismatched = _retryable_mismatches(immediate)
        if not mismatched:
            return {
                "status": _overall_status(immediate),
                "retry_performed": False,
                "datasets": immediate,
            }
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
        try:
            positions = self._fresh_holdings(account)
        except Exception:
            return {
                "pm.holdings_quantity": _unavailable_verdict(),
                "pm.cost_basis": _unavailable_verdict(),
                _AGGREGATE_CASH_DATASET: _unavailable_verdict(),
                "pm.fund_mmf": _unavailable_verdict(),
            }
        actual = {
            item.asset_id: item
            for item in positions
            if (item.broker or "") == broker and item.asset_type in _POSITION_TYPES
        }
        expected_positions = {
            item.asset_id: item
            for item in snapshot.positions
            if item.security_type in {"STOCK", "ETF"}
        }
        quantity_diff: list[dict[str, Any]] = []
        cost_diff: list[dict[str, Any]] = []
        for asset_id in sorted(set(expected_positions) | set(actual)):
            expected = expected_positions.get(asset_id)
            stored = actual.get(asset_id)
            expected_quantity = _decimal(expected.quantity if expected else 0)
            actual_quantity = _decimal(stored.quantity if stored else 0)
            if expected_quantity != actual_quantity:
                quantity_diff.append(_field_difference(
                    asset_id=asset_id,
                    account=account,
                    broker=broker,
                    field="quantity",
                    actual=actual_quantity,
                    requested=expected_quantity,
                    record_id=stored.record_id if stored is not None else None,
                ))
            expected_cost = (
                _money(expected.average_cost)
                if expected is not None and expected_quantity != 0
                else None
            )
            actual_cost = _money(stored.avg_cost) if stored else None
            if expected_cost != actual_cost:
                cost_diff.append(_field_difference(
                    asset_id=asset_id,
                    account=account,
                    broker=broker,
                    field="avg_cost",
                    actual=actual_cost,
                    requested=expected_cost,
                    record_id=stored.record_id if stored is not None else None,
                ))
        return {
            "pm.holdings_quantity": _verdict(quantity_diff, "HOLDINGS_QUANTITY_MISMATCH"),
            "pm.cost_basis": _verdict(cost_diff, "COST_BASIS_MISMATCH"),
            _AGGREGATE_CASH_DATASET: self._aggregate_cash_verdict(
                positions,
                account=account,
                broker=broker,
            ),
            "pm.fund_mmf": self._cash_verdict(
                positions,
                account=account,
                broker=broker,
                asset_id=MMF_ASSET_ID,
                expected=snapshot.mmf,
                reason_code="FUND_MMF_MISMATCH",
            ),
        }

    def _cash_verdict(
        self,
        holdings: Sequence[Any],
        *,
        account: str,
        broker: str,
        asset_id: str,
        expected: Any,
        reason_code: str,
    ) -> dict[str, Any]:
        candidates = [
            item
            for item in holdings
            if item.asset_id == asset_id
            and item.account == account
            and (item.broker or "") == broker
        ]
        stored = candidates[0] if len(candidates) == 1 else None
        matches = stored is not None and _money(stored.quantity) == _money(expected)
        differences = [] if matches else [_field_difference(
            asset_id=asset_id,
            account=account,
            broker=broker,
            field="quantity",
            actual=_money(stored.quantity) if stored is not None else None,
            requested=_money(expected),
            record_id=stored.record_id if stored is not None else None,
        )]
        return _verdict(differences, reason_code)

    def _aggregate_cash_verdict(
        self,
        holdings: Sequence[Any],
        *,
        account: str,
        broker: str,
    ) -> dict[str, Any]:
        candidates = [
            item
            for item in holdings
            if item.asset_id == CASH_ASSET_ID
            and item.account == account
            and (item.broker or "") == broker
        ]
        stored = candidates[0] if len(candidates) == 1 else None

        valid = bool(
            stored is not None
            and stored.asset_id == CASH_ASSET_ID
            and stored.account == account
            and (stored.broker or "") == broker
            and stored.asset_type == AssetType.CASH
            and str(stored.currency or "").upper() == "CNY"
            and _finite_decimal(stored.quantity)
        )
        differences = [] if valid else [{
            "identity": {
                "asset_id": CASH_ASSET_ID,
                "account": account,
                "broker": broker,
            },
            "field": "structure",
            "actual": (
                None
                if stored is None
                else {
                    "asset_id": getattr(stored, "asset_id", None),
                    "account": getattr(stored, "account", None),
                    "broker": getattr(stored, "broker", None),
                    "asset_type": _enum_text(getattr(stored, "asset_type", None)),
                    "currency": getattr(stored, "currency", None),
                    "quantity": _number_text(getattr(stored, "quantity", None)),
                }
            ),
            "requested": {
                "asset_id": CASH_ASSET_ID,
                "account": account,
                "broker": broker,
                "asset_type": AssetType.CASH.value,
                "currency": "CNY",
                "quantity": "finite",
            },
            "record_id": getattr(stored, "record_id", None),
        }]
        return _verdict(
            differences,
            "AGGREGATE_CASH_INVALID",
            trusted_reason="AGGREGATE_CASH_STRUCTURALLY_VALID",
        )

    def _fresh_holdings(self, account: str) -> list[Any]:
        rows = self.storage.get_holdings_fresh(
            account=account,
            include_empty=True,
        )
        if rows is None:
            raise RuntimeError("fresh holdings read returned no slice")
        return list(rows)


def _verdict(
    diff: Sequence[dict[str, Any]],
    reason_code: str,
    *,
    trusted_reason: str = "REPLICA_MATCHED",
) -> dict[str, Any]:
    result = {
        "status": "trusted" if not diff else "untrusted",
        "reason_code": trusted_reason if not diff else reason_code,
        "diff_count": len(diff),
        "diff_subjects": [
            str((item.get("identity") or {}).get("asset_id") or "")
            for item in diff
        ],
    }
    if diff:
        result["differences"] = list(diff)
    return result


def _unavailable_verdict() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason_code": "REPOSITORY_READ_FAILED",
        "diff_count": 0,
        "diff_subjects": [],
    }


def _field_difference(
    *,
    asset_id: str,
    account: str,
    broker: str,
    field: str,
    actual: Any,
    requested: Any,
    record_id: Any,
) -> dict[str, Any]:
    return {
        "identity": {
            "asset_id": asset_id,
            "account": account,
            "broker": broker,
        },
        "field": field,
        "actual": _number_text(actual),
        "requested": _number_text(requested),
        "record_id": record_id,
    }


def _number_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return str(value)
    if not number.is_finite():
        return str(value)
    if number == 0:
        return "0"
    if number == number.to_integral():
        return str(number.quantize(Decimal("1")))
    return format(number.normalize(), "f")


def _enum_text(value: Any) -> Any:
    return getattr(value, "value", value)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _finite_decimal(value: Any) -> bool:
    try:
        return Decimal(str(value)).is_finite()
    except (ArithmeticError, TypeError, ValueError):
        return False


def _retryable_mismatches(datasets: dict[str, dict[str, Any]]) -> list[str]:
    return [
        dataset_id
        for dataset_id, verdict in datasets.items()
        if dataset_id != _AGGREGATE_CASH_DATASET
        and verdict.get("status") != "trusted"
    ]


def _overall_status(datasets: dict[str, dict[str, Any]]) -> str:
    statuses = {item["status"] for item in datasets.values()}
    if "unavailable" in statuses:
        return "unavailable"
    if statuses == {"trusted"}:
        return "trusted"
    return "untrusted"
