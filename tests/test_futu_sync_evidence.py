from __future__ import annotations

from pathlib import Path

from src.app.futu_balance_sync_service import FutuBalanceSnapshot, FutuBalanceSyncService
from src.app.futu_sync_evidence import FutuSyncEvidenceStore
from src.app.futu_sync_reconciler import FutuSyncReconciler


class _Storage:
    def __init__(self, *, fail_asset_id: str | None = None) -> None:
        self.fail_asset_id = fail_asset_id
        self.holdings = {}
        self.writes = []

    def get_holding(self, asset_id, account, broker=None):
        if asset_id == self.fail_asset_id:
            raise RuntimeError("injected write failure")
        return self.holdings.get((asset_id, account, broker))

    def upsert_holding(self, holding):
        self.holdings[(holding.asset_id, holding.account, holding.broker)] = holding
        self.writes.append(holding.asset_id)
        return holding

    def update_holding_quantity(self, asset_id, account, quantity_change, broker=None):
        raise AssertionError("new holdings should use upsert")


class _Provider:
    def fetch_balances(self) -> FutuBalanceSnapshot:
        return FutuBalanceSnapshot(
            cash=100,
            mmf=200,
            source="futu-openapi",
            source_currency="CNH",
            cash_source_field="cash",
            cash_present=True,
            mmf_source_field="fund_assets",
            mmf_present=True,
            source_snapshot_id="snapshot-redacted-001",
            observed_at_utc="2026-07-26T00:00:00Z",
            account_fingerprint="sha256:redacted",
            trd_env="REAL",
            trd_market="US",
        )


def test_sync_receipt_persists_latest_and_history_across_restart(tmp_path: Path) -> None:
    store = FutuSyncEvidenceStore(tmp_path / "receipts")
    result = FutuBalanceSyncService(
        _Storage(),
        _Provider(),
        evidence_store=store,
    ).sync_cash_and_mmf(account="lx", sync_run_id="sync-run-001")

    assert result["success"] is True
    assert result["receipt_persisted"] is True
    assert result["stages"]["securities_cash"]["status"] == "succeeded"
    assert result["stages"]["fund_mmf"]["status"] == "succeeded"
    latest = FutuSyncEvidenceStore(tmp_path / "receipts").latest("lx")
    assert latest["sync_run_id"] == "sync-run-001"
    assert latest["source_snapshot_id"] == "snapshot-redacted-001"
    assert latest["source_metadata"]["source_currency"] == "CNH"
    assert "acc_id" not in str(latest)
    assert (tmp_path / "receipts/lx/history/sync-run-001.json").exists()


def test_cash_success_and_mmf_failure_is_dataset_scoped_partial_write(tmp_path: Path) -> None:
    storage = _Storage(fail_asset_id="CNY-MMF")
    result = FutuBalanceSyncService(
        storage,
        _Provider(),
        evidence_store=FutuSyncEvidenceStore(tmp_path / "receipts"),
        reconciler=FutuSyncReconciler(storage, wait=lambda _seconds: None),
    ).sync_cash_and_mmf(account="lx", sync_run_id="sync-run-partial")

    assert result["success"] is False
    assert result["stages"]["securities_cash"]["status"] == "succeeded"
    assert result["stages"]["fund_mmf"] == {
        "status": "failed",
        "partial_write_possible": True,
        "reason_code": "FUND_MMF_WRITE_FAILED",
    }
    assert result["receipt_persisted"] is True
    receipt = FutuSyncEvidenceStore(tmp_path / "receipts").latest("lx")
    assert receipt["stages"]["securities_cash"]["status"] == "succeeded"
    assert receipt["stages"]["fund_mmf"]["status"] == "failed"
    assert receipt["reconciliation"]["datasets"]["pm.securities_cash"]["status"] == "trusted"
    assert receipt["reconciliation"]["datasets"]["pm.fund_mmf"]["status"] == "unavailable"
