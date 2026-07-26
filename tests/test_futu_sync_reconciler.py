from __future__ import annotations

from src.app.futu_balance_sync_service import FutuPortfolioSnapshot, FutuPositionSnapshot
from src.app.futu_sync_reconciler import FutuSyncReconciler
from src.models import AssetType, Holding


def _holding(asset_id: str, quantity: float, *, avg_cost: float | None = None) -> Holding:
    asset_type = {
        "CNY-CASH": AssetType.CASH,
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
        currency="CNY" if asset_id.startswith("CNY-") else "USD",
    )


def _snapshot() -> FutuPortfolioSnapshot:
    return FutuPortfolioSnapshot(
        cash=100,
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
        self.cash = {
            "CNY-CASH": _holding("CNY-CASH", 100),
            "CNY-MMF": _holding("CNY-MMF", 200),
        }

    def get_holdings(self, account=None, include_empty=False):
        index = min(self.read_count, len(self.position_reads) - 1)
        self.read_count += 1
        return self.position_reads[index]

    def get_holding(self, asset_id, account, broker=None):
        return self.cash.get(asset_id)


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
    assert result["datasets"]["pm.securities_cash"]["status"] == "trusted"
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
