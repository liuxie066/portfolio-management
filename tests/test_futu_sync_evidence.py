from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.app.futu_balance_sync_service import FutuBalanceSnapshot, FutuBalanceSyncService
from src.app.futu_sync_evidence import FutuSyncEvidenceStore
from src.app.futu_sync_reconciler import FutuSyncReconciler
from src.domain.holding_mutations import HoldingTarget
from src.models import AssetType, Holding


def _fx():
    return {"USDCNY": 7.25, "HKDCNY": 0.9}, {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "sources": {"USDCNY": "test", "HKDCNY": "test"},
    }


class _Storage:
    def __init__(self, *, fail_asset_id: str | None = None) -> None:
        self.fail_asset_id = fail_asset_id
        self.holdings = {
            (asset_id, "lx", "富途"): Holding(
                asset_id=asset_id,
                record_id=f"rec_{asset_id}",
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
            cash_by_currency={"CNY": 100, "USD": 20, "HKD": -10},
            mmf=200,
            source="futu-openapi",
            pending_asset=0, pending_asset_currency="CNH",
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
        fx_loader=_fx,
        evidence_store=store,
    ).sync_cash_and_mmf(account="lx", sync_run_id="sync-run-001")

    assert result["success"] is True
    assert result["receipt_persisted"] is True
    assert result["stages"]["securities_cash"]["status"] == "succeeded"
    assert result["stages"]["fund_mmf"]["status"] == "succeeded"
    latest = FutuSyncEvidenceStore(tmp_path / "receipts").latest("lx")
    assert latest["sync_run_id"] == "sync-run-001"
    assert latest["cash_write"]["status"] == "verified"
    assert latest["cash_write"]["target"] == 236
    assert latest["cash_write"]["fx"]["rates"] == {"USDCNY": 7.25, "HKDCNY": 0.9}
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
        fx_loader=_fx,
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


def test_formal_nav_uses_matching_morning_futu_readback(tmp_path, monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    from src.app.business_calendar_service import BusinessCalendarService
    from src.app.futu_sync_evidence import futu_holdings_digest

    nav_date = date(2026, 11, 18)
    run_date = date(2026, 11, 19)
    monkeypatch.setattr("src.app.futu_sync_evidence.bj_today", lambda: run_date)
    monkeypatch.setattr(
        BusinessCalendarService, "from_config",
        lambda: SimpleNamespace(default_nav_date=lambda *, run_date: nav_date),
    )
    store = FutuSyncEvidenceStore(tmp_path)
    holdings = _Storage().get_holdings_fresh(account="lx")
    sync = {
        "source_snapshot_id": "one",
        "reconciliation": {"datasets": {
            "pm.pending_asset": {"status": "trusted"}, "pm.total_assets": {"status": "trusted"},
        }},
        "success": True,
        "holdings_digest": futu_holdings_digest(holdings),
        "cash_write": {
            "observed_at_utc": "2026-11-19T00:10:01+00:00",
            "cash_read_started_at": "2026-11-19T00:10:00+00:00",
            "status": "verified",
            "snapshot_id": "one",
        },
    }
    store.save("lx", "first", sync)
    assert store.require_nav_capture("lx", nav_date, holdings) == sync["cash_write"]

    import pytest

    with pytest.raises(ValueError, match="do not match"):
        store.require_nav_capture("lx", nav_date, [])
    sync["cash_write"]["observed_at_utc"] = "2026-11-19T01:00:00+00:00"
    store.save("lx", "late", sync)
    with pytest.raises(ValueError, match="outside the early NAV window"):
        store.require_nav_capture("lx", nav_date, holdings)


def test_nav_capture_survives_option_freeze_and_rejects_multiplier_drift(tmp_path, monkeypatch):
    from datetime import date

    import pytest

    from src.app.business_calendar_service import BusinessCalendarService
    from src.app.futu_sync_evidence import futu_holdings_digest
    from src.app.holdings_nav_preflight_service import FrozenHoldingRow
    from src.domain.snapshot_contracts import NormalizedValuationRow

    account = "synthetic"
    capture_day = date(2026, 11, 20)
    nav_day = date(2026, 11, 19)
    monkeypatch.setattr("src.app.futu_sync_evidence.bj_today", lambda: capture_day)
    monkeypatch.setattr(BusinessCalendarService, "default_nav_date", lambda self, **kwargs: nav_day)
    option = Holding(
        asset_id="SYNTHETIC_OPTION", asset_name="Synthetic option", asset_type=AssetType.OPTION,
        account=account, broker="富途", quantity=-2, currency="USD", contract_multiplier=100,
    )
    cash = Holding(
        asset_id="CNY-CASH", asset_name="Synthetic cash", asset_type=AssetType.CASH,
        account=account, broker="富途", quantity=1, currency="CNY",
    )
    store = FutuSyncEvidenceStore(tmp_path)
    proof = {
        "status": "verified", "snapshot_id": "synthetic-snapshot",
        "observed_at_utc": "2026-11-20T00:10:07+00:00",
        "cash_read_started_at": "2026-11-20T00:10:06+00:00",
    }
    store.save(account, "synthetic-capture", {
        "success": True, "source_snapshot_id": "synthetic-snapshot",
        "reconciliation": {"datasets": {
            "pm.pending_asset": {"status": "trusted"}, "pm.total_assets": {"status": "trusted"},
        }},
        "cash_write": proof, "holdings_digest": futu_holdings_digest([option, cash]),
    })

    frozen_cash = FrozenHoldingRow.from_holding(cash)
    assert "contract_multiplier" not in frozen_cash.as_dict()
    frozen_option = FrozenHoldingRow.from_holding(option)
    rows = [frozen_option.to_holding(), frozen_cash.to_holding()]
    assert store.require_nav_capture(account, nav_day, rows) == proof
    assert frozen_option.as_dict()["contract_multiplier"] == rows[0].contract_multiplier == 100
    valued = NormalizedValuationRow.from_holding(
        rows[0], account=account, normalized_type="option", price="2", cny_price="14",
        source="futu_opend_option_close",
    )
    assert valued.to_snapshot_row(as_of=nav_day.isoformat()).market_value_cny == -2800

    rows[0] = rows[0].model_copy(update={"contract_multiplier": 200})
    with pytest.raises(ValueError, match="do not match verified NAV capture"):
        store.require_nav_capture(account, nav_day, rows)


def _pending_snapshot(amount=9745, **changes):
    from dataclasses import replace

    return replace(
        _Provider().fetch_balances(),
        cash_by_currency={"CNY": -9745, "USD": 0, "HKD": 0}, mmf=86899.37,
        pending_asset=amount, pending_asset_currency="CNH",
        account_assets={
            "currency": "CNH", "cash": -9745, "fund_assets": 86899.37,
            "market_val": 158117.6122, "pending_asset": amount,
            "total_assets": -9745 + 86899.37 + 158117.6122 + amount,
        },
        **changes,
    )


def test_pending_sync_replay_settlement_and_account_isolation(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from src.models import FUTU_PENDING_ASSET_ID, AssetClass

    storage = _Storage()
    snapshot = _pending_snapshot()
    provider = SimpleNamespace(fetch_balances=lambda: snapshot)
    service = FutuBalanceSyncService(storage, provider, fx_loader=_fx,
        evidence_store=FutuSyncEvidenceStore(tmp_path),
        reconciler=FutuSyncReconciler(storage, wait=lambda _: None))
    for _ in range(2):
        result = service.sync_cash_and_mmf(account="lx")
        assert result["success"]
    pending = storage.get_holding_fresh(FUTU_PENDING_ASSET_ID, "lx", "富途")
    assert pending.quantity == 9745
    assert pending.asset_type == AssetType.OTHER
    assert pending.asset_class == AssetClass.ALTERNATIVE
    assert pending.asset_name == "富途在途资产"
    assert storage.writes.count(FUTU_PENDING_ASSET_ID) == 1
    assert result["reconciliation"]["datasets"]["pm.total_assets"]["status"] == "trusted"
    service.sync_cash_and_mmf(account="sy")
    snapshot = replace(snapshot, pending_asset=0,
        account_assets={**snapshot.account_assets, "pending_asset": 0,
                        "total_assets": snapshot.account_assets["total_assets"] - 9745})
    assert service.sync_cash_and_mmf(account="lx")["success"]
    assert storage.get_holding_fresh(FUTU_PENDING_ASSET_ID, "lx", "富途").quantity == 0
    assert storage.get_holding_fresh(FUTU_PENDING_ASSET_ID, "sy", "富途").quantity == 9745


def test_pending_write_failure_and_bad_total_are_not_success(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from src.models import FUTU_PENDING_ASSET_ID

    snapshot = _pending_snapshot()
    storage = _Storage(fail_asset_id=FUTU_PENDING_ASSET_ID)
    service = FutuBalanceSyncService(storage, SimpleNamespace(fetch_balances=lambda: snapshot),
        fx_loader=_fx, evidence_store=FutuSyncEvidenceStore(tmp_path),
        reconciler=FutuSyncReconciler(storage, wait=lambda _: None))
    result = service.sync_cash_and_mmf(account="lx")
    assert not result["success"]
    assert result["stages"]["pending_asset"]["reason_code"] == "FUTU_PENDING_ASSET_WRITE_FAILED"
    storage.fail_asset_id = None
    # A total that omits pending must be flagged, not 'repaired' by another addition.
    snapshot = replace(snapshot, account_assets={**snapshot.account_assets,
        "total_assets": snapshot.account_assets["total_assets"] - 9745})
    result = service.sync_cash_and_mmf(account="lx")
    assert not result["success"]
    assert result["reconciliation"]["datasets"]["pm.total_assets"]["reason_code"] == "FUTU_TOTAL_ASSETS_MISMATCH"
    assert storage.get_holding_fresh(FUTU_PENDING_ASSET_ID, "lx", "富途").quantity == 9745
    snapshot = replace(snapshot, account_assets={**snapshot.account_assets, "total_assets": None})
    result = service.sync_cash_and_mmf(account="lx")
    assert not result["success"]
    assert result["reconciliation"]["datasets"]["pm.total_assets"]["status"] == "unavailable"


def test_pending_missing_invalid_and_wrong_currency_do_not_write(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    for amount, currency in ((None, "CNH"), (-1, "CNH"), (float("nan"), "CNH"), (1, "USD")):
        snapshot = replace(_pending_snapshot(), pending_asset=amount, pending_asset_currency=currency)
        storage = _Storage()
        result = FutuBalanceSyncService(storage, SimpleNamespace(fetch_balances=lambda: snapshot),
            fx_loader=_fx, evidence_store=FutuSyncEvidenceStore(tmp_path)).sync_cash_and_mmf(account="lx")
        assert not result["success"]
        assert storage.writes == []


def test_pending_dry_run_and_payload_identity(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from src.models import FUTU_PENDING_ASSET_ID

    snapshot = _pending_snapshot()
    storage = _Storage()
    result = FutuBalanceSyncService(storage, SimpleNamespace(fetch_balances=lambda: snapshot),
        fx_loader=_fx, evidence_store=FutuSyncEvidenceStore(tmp_path)).sync_cash_and_mmf(account="lx", dry_run=True)
    assert result["success"]
    assert storage.writes == []
    assert next(i for i in result["items"] if i["asset_id"] == FUTU_PENDING_ASSET_ID)["target"] == 9745
    assert FutuBalanceSyncService._snapshot_payload_sha256(snapshot) != FutuBalanceSyncService._snapshot_payload_sha256(replace(snapshot, pending_asset=0))


def test_portfolio_sync_preserves_pending_through_balance_stage(tmp_path):
    from dataclasses import asdict
    from types import SimpleNamespace
    from src.app.futu_balance_sync_service import FutuPortfolioSnapshot
    from src.models import FUTU_PENDING_ASSET_ID

    snapshot = FutuPortfolioSnapshot(**asdict(_pending_snapshot()))
    storage = _Storage()
    result = FutuBalanceSyncService(storage, SimpleNamespace(fetch_portfolio=lambda: snapshot),
        fx_loader=_fx, evidence_store=FutuSyncEvidenceStore(tmp_path),
        reconciler=FutuSyncReconciler(storage, wait=lambda _: None)).sync_portfolio(account="lx", dry_run=False, confirm=True)
    assert result["success"]
    assert result["stages"]["pending_asset"]["status"] == "succeeded"
    assert storage.get_holding_fresh(FUTU_PENDING_ASSET_ID, "lx", "富途").quantity == 9745
    assert result["holdings_digest"]
    assert result["reconciliation"]["datasets"]["pm.pending_asset"]["status"] == "trusted"
