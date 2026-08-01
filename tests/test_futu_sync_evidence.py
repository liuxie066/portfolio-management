from __future__ import annotations

from pathlib import Path

from src.app.futu_balance_sync_service import FutuBalanceSnapshot, FutuBalanceSyncService
from src.app.futu_sync_evidence import FutuSyncEvidenceStore
from src.app.futu_sync_reconciler import FutuSyncReconciler
from src.domain.holding_mutations import HoldingTarget
from src.models import AssetType, Holding


class _Storage:
    def __init__(self, *, fail_asset_id: str | None = None) -> None:
        self.fail_asset_id = fail_asset_id
        self.holdings = {
            (asset_id, "lx", "富途"): Holding(
                asset_id=asset_id,
                asset_name=asset_id,
                asset_type=AssetType.CASH,
                account="lx",
                broker="富途",
                quantity=quantity,
                currency=currency,
            )
            for currency, asset_id, quantity in (
                ("CNY", "CNY-CASH", 100),
            )
        }
        self.writes = []

    def get_holding(self, asset_id, account, broker=None):
        return self.holdings.get((asset_id, account, broker))

    def get_holding_fresh(self, asset_id, account, broker):
        return self.holdings.get((asset_id, account, broker))

    def get_holdings_fresh(self, *, account=None, include_empty=True):
        return [
            holding
            for (_asset_id, holding_account, _broker), holding in self.holdings.items()
            if account is None or holding_account == account
        ]

    def upsert_holding(self, holding):
        self.holdings[(holding.asset_id, holding.account, holding.broker)] = holding
        self.writes.append(holding.asset_id)
        return holding

    def replace_holding(self, target):
        assert isinstance(target, HoldingTarget)
        if target.identity.asset_id == self.fail_asset_id:
            raise RuntimeError("injected write failure")
        holding = target.to_holding(record_id=f"rec_{target.identity.asset_id}")
        self.holdings[(holding.asset_id, holding.account, holding.broker)] = holding
        self.writes.append(holding.asset_id)
        return holding

    def update_holding_quantity(self, asset_id, account, quantity_change, broker=None):
        raise AssertionError("new holdings should use upsert")


class _Provider:
    def fetch_balances(self) -> FutuBalanceSnapshot:
        return FutuBalanceSnapshot(
            cash_by_currency={"CNY": 100, "USD": 0, "HKD": 0},
            mmf=200,
            source="futu-openapi",
            account_id=123,
            profile_fingerprint="sha256:redacted",
            cash_source_fields={
                "CNY": "cn_cash",
                "USD": "us_cash",
                "HKD": "hk_cash",
            },
            cash_present_by_currency={"CNY": True, "USD": True, "HKD": True},
            mmf_source_field="fund_assets",
            mmf_present=True,
            source_snapshot_id="snapshot-redacted-001",
            observed_at_utc="2026-07-26T00:00:00Z",
            account_fingerprint=(
                "sha256:"
                "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3"
            ),
            trd_env="REAL",
            trd_market="US",
            account_verified=True,
            pagination_complete=True,
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
    assert latest["source_metadata"]["cash"]["source_fields"] == {
        "CNY": "cn_cash",
        "USD": "us_cash",
        "HKD": "hk_cash",
    }
    assert latest["source_metadata"]["source_snapshot_id"] == "snapshot-redacted-001"
    assert latest["source_metadata"]["refresh_cache"] is True
    assert latest["source_metadata"]["account_verified"] is True
    assert latest["source_metadata"]["pagination_complete"] is True
    assert latest["source_metadata"]["position_snapshot_included"] is False
    assert latest["source_metadata"]["position_count"] is None
    assert len(latest["source_metadata"]["payload_sha256"]) == 64
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
    assert receipt["reconciliation"]["datasets"]["pm.cash_aggregate"] == {
        "status": "trusted",
        "reason_code": "AGGREGATE_CASH_STRUCTURALLY_VALID",
        "diff_count": 0,
        "diff_subjects": [],
    }
    mmf = receipt["reconciliation"]["datasets"]["pm.fund_mmf"]
    assert {
        key: mmf[key]
        for key in ("status", "reason_code", "diff_count", "diff_subjects")
    } == {
        "status": "untrusted",
        "reason_code": "FUND_MMF_MISMATCH",
        "diff_count": 1,
        "diff_subjects": ["CNY-MMF"],
    }
    assert mmf["differences"] == [{
        "identity": {
            "asset_id": "CNY-MMF",
            "account": "lx",
            "broker": "富途",
        },
        "field": "quantity",
        "actual": None,
        "requested": "200",
        "record_id": None,
    }]


def test_source_query_failure_replaces_latest_with_redacted_failed_attempt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = FutuSyncEvidenceStore(tmp_path / "receipts")
    service = FutuBalanceSyncService(
        _Storage(),
        evidence_store=store,
    )

    def fail_query(_account: str):
        raise RuntimeError("sensitive upstream detail")

    monkeypatch.setattr(service, "_fetch_balances", fail_query)
    result = service.sync_cash_and_mmf(account="lx", sync_run_id="sync-run-failed")

    assert result["success"] is False
    assert result["receipt_persisted"] is True
    latest = store.latest("lx")
    assert latest["sync_run_id"] == "sync-run-failed"
    assert latest["success"] is False
    assert latest["failure"] == {
        "reason_code": "FUTU_SOURCE_QUERY_FAILED",
        "phase": "source_query",
    }
    assert latest["stages"]["source"]["status"] == "failed"
    assert "sensitive upstream detail" not in str(latest)
