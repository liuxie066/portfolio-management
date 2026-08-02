from __future__ import annotations

from types import SimpleNamespace

from src.app.futu_balance_sync_service import FutuPortfolioSnapshot, FutuPositionSnapshot
from src.app.futu_sync_reconciler import FutuSyncReconciler
from src.models import AssetType, Holding


def _holding(asset_id: str, quantity: float, *, avg_cost: float | None = None) -> Holding:
    asset_type = {
        "CNY-CASH": AssetType.CASH,
        "USD-CASH": AssetType.CASH,
        "HKD-CASH": AssetType.CASH,
        "CNY-MMF": AssetType.MMF,
    }.get(asset_id, AssetType.US_STOCK)
    return Holding(
        asset_id=asset_id,
        asset_name=asset_id,
        asset_type=asset_type,
        account="lx",
        broker="富途",
        quantity=quantity,
        avg_cost=avg_cost,
        currency=asset_id.split("-", 1)[0] if asset_id.endswith("-CASH") else (
            "CNY" if asset_id.startswith("CNY-") else "USD"
        ),
    )


def _snapshot() -> FutuPortfolioSnapshot:
    return FutuPortfolioSnapshot(
        cash_by_currency={"CNY": 999, "USD": 12, "HKD": 34},
        mmf=200,
        positions=(
            FutuPositionSnapshot(
                asset_id="FUTU",
                asset_name="Futu",
                security_type="STOCK",
                quantity=10,
                average_cost=100.126,
                currency="USD",
                market="US",
            ),
        ),
        source="futu-openapi",
    )


class _Storage:
    def __init__(self, position_reads: list[list[Holding]]) -> None:
        self.position_reads = position_reads
        self.read_count = 0
        self.cached_read_count = 0
        self.cash = {
            "CNY-CASH": _holding("CNY-CASH", 100),
            "CNY-MMF": _holding("CNY-MMF", 200),
        }

    def get_holdings(self, account=None, include_empty=False):
        self.cached_read_count += 1
        raise AssertionError("reconciliation must not read the optimistic cache")

    def get_holdings_fresh(self, account=None, include_empty=False):
        index = min(self.read_count, len(self.position_reads) - 1)
        self.read_count += 1
        return [
            *self.position_reads[index],
            *self.cash.values(),
        ]

    def get_holding(self, asset_id, account, broker=None):
        raise AssertionError("reconciliation must use one complete fresh slice")


def test_readback_immediate_match_never_waits() -> None:
    storage = _Storage([[_holding("FUTU", 10, avg_cost=100.13)]])
    waits = []
    result = FutuSyncReconciler(storage, wait=waits.append).reconcile(
        _snapshot(),
        account="lx",
        broker="富途",
    )
    assert result["status"] == "trusted"
    assert result["retry_performed"] is False
    assert waits == []
    assert storage.read_count == 1
    assert storage.cached_read_count == 0


def test_first_mismatch_then_read_only_retry_recovers() -> None:
    storage = _Storage([
        [_holding("FUTU", 9, avg_cost=100.13)],
        [_holding("FUTU", 10, avg_cost=100.13)],
    ])
    waits = []
    result = FutuSyncReconciler(storage, wait=waits.append).reconcile(
        _snapshot(),
        account="lx",
        broker="富途",
    )
    assert result["status"] == "trusted"
    assert result["retry_performed"] is True
    assert waits == [30]
    assert storage.read_count == 2


def test_persistent_quantity_and_cost_mismatch_are_dataset_scoped() -> None:
    storage = _Storage([[_holding("FUTU", 9, avg_cost=99)]])
    result = FutuSyncReconciler(storage, wait=lambda _seconds: None).reconcile(
        _snapshot(),
        account="lx",
        broker="富途",
    )
    assert result["status"] == "untrusted"
    assert result["datasets"]["pm.holdings_quantity"]["status"] == "untrusted"
    assert result["datasets"]["pm.cost_basis"]["status"] == "untrusted"
    assert result["datasets"]["pm.holdings_quantity"]["differences"] == [{
        "identity": {"asset_id": "FUTU", "account": "lx", "broker": "富途"},
        "field": "quantity",
        "actual": "9",
        "requested": "10",
        "record_id": None,
    }]
    assert result["datasets"]["pm.cash_aggregate"] == {
        "status": "trusted",
        "reason_code": "AGGREGATE_CASH_STRUCTURALLY_VALID",
        "diff_count": 0,
        "diff_subjects": [],
    }
    assert result["datasets"]["pm.fund_mmf"]["status"] == "trusted"


def test_cost_mismatch_does_not_change_quantity_verdict() -> None:
    storage = _Storage([[_holding("FUTU", 10, avg_cost=99)]])
    result = FutuSyncReconciler(storage, wait=lambda _seconds: None).reconcile(
        _snapshot(),
        account="lx",
        broker="富途",
    )
    assert result["datasets"]["pm.holdings_quantity"]["status"] == "trusted"
    assert result["datasets"]["pm.cost_basis"]["status"] == "untrusted"


def test_explicit_zero_requires_fresh_average_cost_clear() -> None:
    snapshot = _snapshot()
    snapshot = FutuPortfolioSnapshot(
        **{
            **snapshot.__dict__,
            "positions": (
                FutuPositionSnapshot(
                    asset_id="FUTU",
                    asset_name="Futu",
                    security_type="STOCK",
                    quantity=0,
                    average_cost=None,
                    currency="USD",
                    market="US",
                ),
            ),
        }
    )
    storage = _Storage([[_holding("FUTU", 0, avg_cost=99)]])

    result = FutuSyncReconciler(
        storage,
        wait=lambda _seconds: None,
    ).reconcile(snapshot, account="lx", broker="富途")

    assert result["datasets"]["pm.holdings_quantity"]["status"] == "trusted"
    cost = result["datasets"]["pm.cost_basis"]
    assert cost["status"] == "untrusted"
    assert cost["differences"][0]["actual"] == "99"
    assert cost["differences"][0]["requested"] is None


def test_missing_aggregate_cash_is_untrusted_without_cash_retry() -> None:
    storage = _Storage([[_holding("FUTU", 10, avg_cost=100.13)]])
    storage.cash.pop("CNY-CASH")
    waits = []

    result = FutuSyncReconciler(storage, wait=waits.append).reconcile(
        _snapshot(),
        account="lx",
        broker="富途",
    )

    assert result["status"] == "untrusted"
    assert result["retry_performed"] is False
    aggregate = result["datasets"]["pm.cash_aggregate"]
    assert {
        key: aggregate[key]
        for key in ("status", "reason_code", "diff_count", "diff_subjects")
    } == {
        "status": "untrusted",
        "reason_code": "AGGREGATE_CASH_INVALID",
        "diff_count": 1,
        "diff_subjects": ["CNY-CASH"],
    }
    assert aggregate["differences"][0]["actual"] is None
    assert aggregate["differences"][0]["requested"]["quantity"] == "finite"
    assert waits == []


def test_invalid_aggregate_cash_shape_is_untrusted_without_value_comparison() -> None:
    storage = _Storage([[_holding("FUTU", 10, avg_cost=100.13)]])
    storage.cash["CNY-CASH"] = SimpleNamespace(
        asset_id="CNY-CASH",
        account="lx",
        broker="富途",
        asset_type=AssetType.CASH,
        currency="USD",
        quantity=float("inf"),
    )

    result = FutuSyncReconciler(storage, wait=lambda _seconds: None).reconcile(
        _snapshot(),
        account="lx",
        broker="富途",
    )

    assert result["status"] == "untrusted"
    assert result["retry_performed"] is False
    assert result["datasets"]["pm.cash_aggregate"]["reason_code"] == (
        "AGGREGATE_CASH_INVALID"
    )


def test_balance_readback_observes_futu_cash_without_comparing_amounts() -> None:
    storage = _Storage([[]])
    waits = []

    result = FutuSyncReconciler(storage, wait=waits.append).reconcile_balances(
        _snapshot(),
        account="lx",
        broker="富途",
    )

    assert result["status"] == "trusted"
    assert result["datasets"]["pm.cash_aggregate"]["status"] == "trusted"
    assert storage.read_count == 1
    assert storage.cached_read_count == 0
    assert waits == []


def test_fresh_remote_mismatch_cannot_be_trusted_from_matching_cache() -> None:
    storage = _Storage([[_holding("FUTU", 9, avg_cost=99)]])
    cached = [_holding("FUTU", 10, avg_cost=100.13)]
    storage.get_holdings = lambda **_kwargs: cached

    result = FutuSyncReconciler(
        storage,
        wait=lambda _seconds: None,
    ).reconcile(_snapshot(), account="lx", broker="富途")

    assert result["status"] == "untrusted"
    assert result["datasets"]["pm.holdings_quantity"]["differences"][0][
        "actual"
    ] == "9"
    assert storage.read_count == 2


def test_fresh_read_failure_is_unavailable_and_never_falls_back_to_cache() -> None:
    storage = _Storage([[_holding("FUTU", 10, avg_cost=100.13)]])
    storage.get_holdings_fresh = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("remote unavailable")
    )
    storage.get_holdings = lambda **_kwargs: [
        _holding("FUTU", 10, avg_cost=100.13)
    ]

    result = FutuSyncReconciler(
        storage,
        wait=lambda _seconds: None,
    ).reconcile(_snapshot(), account="lx", broker="富途")

    assert result["status"] == "unavailable"
    assert result["retry_performed"] is True
    assert {
        verdict["reason_code"]
        for verdict in result["datasets"].values()
    } == {"REPOSITORY_READ_FAILED"}
