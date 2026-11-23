from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.domain.mmf_transit import (
    ConfirmedMmfTransfer,
    MmfSettlementFlow,
    TransferDirection,
    adjustments_by_currency,
    reconciled_amount,
)


@pytest.mark.parametrize("direction", list(TransferDirection))
def test_only_debited_uncredited_amount_is_valued(direction):
    transfer = ConfirmedMmfTransfer(
        "sy", "broker-sy", "receipt-1", "balance-1", direction, "HKD",
        Decimal("84134.88"), Decimal("84134.88"), Decimal("34000"),
    )
    scope = {"account": "sy", "broker_account": "broker-sy", "balance_snapshot_id": "balance-1"}
    assert adjustments_by_currency([transfer], **scope) == {"HKD": Decimal("50134.88")}
    settled = replace(transfer, target_credit=transfer.amount)
    assert adjustments_by_currency([settled], **scope) == {}
    submitted = replace(transfer, source_debit=Decimal(0), target_credit=Decimal(0))
    assert adjustments_by_currency([submitted], **scope) == {}
    with pytest.raises(ValueError, match="snapshot"):
        adjustments_by_currency([transfer], **{**scope, "balance_snapshot_id": "balance-2"})
    with pytest.raises(ValueError, match="Duplicate"):
        adjustments_by_currency([transfer, transfer], **scope)


@pytest.mark.parametrize(
    ("target_credit", "flow_visible", "expected_adjustment", "expected_reconciled"),
    [
        ("0", False, "100", "0"),
        ("0", True, "100", "0"),
        ("100", False, "0", "0"),
        ("100", True, "0", "100"),
        ("25", True, "75", "25"),
    ],
)
def test_valuation_and_reconciliation_follow_different_evidence(
    target_credit, flow_visible, expected_adjustment, expected_reconciled,
):
    transfer = ConfirmedMmfTransfer(
        "sy", "broker-sy", "receipt-1", "balance-1", TransferDirection.REDEMPTION,
        "HKD", Decimal("100"), Decimal("100"), Decimal(target_credit),
    )
    flow = MmfSettlementFlow(
        "flow-1", "broker-sy", TransferDirection.REDEMPTION, "HKD", Decimal("100"),
    )
    assert transfer.in_transit == Decimal(expected_adjustment)
    assert reconciled_amount(transfer, [flow, flow] if flow_visible else []) == Decimal(
        expected_reconciled
    )


def test_confirmations_are_durable_scoped_and_idempotent(tmp_path):
    from src.app.operation_state import OperationStateStore

    path = tmp_path / "operation.sqlite3"
    transfer = ConfirmedMmfTransfer(
        "sy", "broker-sy", "receipt-1", "balance-1", TransferDirection.REDEMPTION,
        "HKD", Decimal("100"), Decimal("100"), Decimal("0"),
    )
    scope = {"account": "sy", "broker_account": "broker-sy", "balance_snapshot_id": "balance-1"}
    store = OperationStateStore(path)
    store.record_mmf_transfer(transfer)
    store.record_mmf_transfer(transfer)
    assert OperationStateStore(path).read_mmf_transfers(**scope) == [transfer]
    assert store.read_mmf_transfers(**{**scope, "account": "lx"}) == []
    assert store.read_mmf_transfers(**{**scope, "balance_snapshot_id": "balance-2"}) == []
    with pytest.raises(ValueError, match="Conflicting"):
        store.record_mmf_transfer(replace(transfer, target_credit=Decimal("25")))


def test_app_intake_and_opend_reconciliation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.app import mmf_transit_service as service
    from src.app.operation_state import OperationStateStore

    path = tmp_path / "operation.sqlite3"
    provider = SimpleNamespace(
        account_fingerprint="broker-sy",
        fetch_mmf_cash_flows=lambda day: [
            {"cashflow_id": "f1", "clearing_date": day, "settlement_date": day, "cashflow_direction": "IN",
             "currency": "HKD", "cashflow_amount": 100},
        ],
    )
    monkeypatch.setattr(service, "current_mmf_balance", lambda account: (provider, "balance-1"))
    monkeypatch.setattr(OperationStateStore, "resolve_db_path_read_only", staticmethod(lambda *args: path))
    monkeypatch.setattr(service, "_balance_date", lambda *args: date.fromisoformat("2026-11-20"))
    monkeypatch.setattr("src.config.get_data_dir", lambda: tmp_path)
    kwargs = {
        "snapshot_id": "balance-1", "receipt_id": "app-1", "direction": "redemption",
        "currency": "HKD", "amount": "100", "source_debit": "100", "target_credit": "100",
        "request_date": "2026-11-20",
    }
    with pytest.raises(ValueError, match="later than"):
        service.record_app_receipt("sy", **{**kwargs, "request_date": "2099-01-01"})
    assert service.record_app_receipt("sy", **kwargs)["recorded"] is False
    assert not path.exists()
    assert service.record_app_receipt("sy", **kwargs, confirm=True)["recorded"] is True
    recon = {
        "snapshot_id": "balance-1", "receipt_id": "app-1",
        "clearing_date": "2026-11-20", "flow_ids": ["f1"],
    }
    with pytest.raises(ValueError, match="outside"):
        service.reconcile_app_receipt("sy", **{**recon, "clearing_date": "2026-11-19"})
    assert service.reconcile_app_receipt("sy", **recon)["closed"] is False
    for _ in range(2):
        assert service.reconcile_app_receipt("sy", **recon, confirm=True)["closed"] is True
    service.record_app_receipt("sy", **{**kwargs, "receipt_id": "app-2"}, confirm=True)
    with pytest.raises(ValueError, match="already attributed"):
        service.reconcile_app_receipt(
            "sy", **{**recon, "receipt_id": "app-2"}, confirm=True,
        )


def test_mmf_funding_returns_only_snapshot_bound_confirmations(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.app import mmf_transit_service as service
    from src.app.operation_state import OperationStateStore

    path = tmp_path / "operation.sqlite3"
    provider = SimpleNamespace(account_fingerprint="broker-sy", trd_env="REAL")
    receipt = {
        "account": "sy", "source_snapshot_id": "balance-1", "success": True,
        "source_metadata": {"account_fingerprint": "broker-sy", "trd_env": "REAL"},
        "cash_write": {
            "snapshot_id": "balance-1", "status": "verified",
            "balances": {"CNY": 0, "HKD": -100, "USD": 0},
            "observed_at_utc": "2026-11-20T03:00:00Z", "fund_assets": 200,
        },
    }
    monkeypatch.setattr(service.FutuOpenApiBalanceProvider, "from_account", lambda account: provider)
    monkeypatch.setattr(service.FutuSyncEvidenceStore, "latest", lambda self, account: receipt)
    monkeypatch.setattr(OperationStateStore, "resolve_db_path_read_only", staticmethod(lambda *args: path))
    assert service.read_mmf_funding("sy")["status"] == "none"
    assert not path.exists()
    store = OperationStateStore(path)
    store.record_mmf_transfer(ConfirmedMmfTransfer(
        "sy", "broker-sy", "receipt-1", "balance-1", TransferDirection.REDEMPTION,
        "HKD", Decimal("100"), Decimal("100"), Decimal("0"),
    ))
    funding = service.read_mmf_funding("sy")
    assert funding["status"] == "verified"
    assert funding["cash"]["HKD"] == -100
    assert funding["adjustments"] == {"HKD": "100"}
    receipt["source_snapshot_id"] = "balance-2"
    assert service.read_mmf_funding("sy")["status"] == "unverified"


def test_priced_holdings_and_nav_share_one_transit_row(monkeypatch):
    from types import SimpleNamespace

    from src import config
    from src.app import mmf_transit_service
    from src.app.portfolio_read_service import PortfolioReadService
    from src.app.valuation_service import ValuationService
    from src.models import AssetType, Holding

    balances = [
        Holding(asset_id=code, asset_name=code, asset_type=kind, account="sy",
                broker="富途", currency="CNY", quantity=quantity)
        for code, kind, quantity in (
            ("CNY-CASH", AssetType.CASH, -85),
            ("CNY-MMF", AssetType.MMF, 200),
            ("CNY-FUTU-PENDING", AssetType.OTHER, 155),
        )
    ]
    funding = {
        "account": "sy", "status": "verified", "snapshot_id": "balance-1",
        "adjustments": {"HKD": "100", "USD": "10"},
        "fx": {"HKDCNY": "0.85", "USDCNY": "7"},
        "cash_target_cny": -85, "fund_assets_cny": 200,
    }
    original_get = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        {"sy": {}} if key == "futu.profiles" else original_get(key, default)
    ))
    monkeypatch.setattr(mmf_transit_service, "read_mmf_valuation_funding", lambda account: funding)
    storage = SimpleNamespace(get_holdings=lambda **kw: balances, get_price=lambda code: None, get_total_shares=lambda account: 100)
    valuation = ValuationService(manager=None, storage=storage, price_fetcher=None)
    portfolio = SimpleNamespace(calculate_valuation=valuation.calculate_valuation)
    service = PortfolioReadService(
        account="sy", storage=storage, portfolio=portfolio, reporting_service=None,
    )

    view = service.get_holdings(include_price=True)
    nav = service.build_snapshot(
        holdings=balances, holdings_provenance={"futu_capture": {"snapshot_id": "balance-1"}},
    )
    transit = [row for row in view["holdings"] if row["code"] == "CNY-FUTU-PENDING"]
    assert len(transit) == 1
    assert all(row["code"] != "CNY-MMF-TRANSIT" for row in view["holdings"])
    assert nav["valuation"].total_value_cny == view["total_value"] == 270
    assert len(balances) == 3
    assert nav["valuation"].stock_value_cny == 0
    assert nav["valuation"].pending_asset_value_cny == 155
    from src.app.reporting_service import ReportingService
    from src.app.account_service import report_value_breakdown
    from src.domain.report.holdings_projection import report_holdings_fields
    reporting = ReportingService(portfolio, storage)
    distribution = reporting.get_asset_distribution("sy")
    assert distribution["股票"] == 0
    assert distribution["富途在途资产"] == pytest.approx(155 / 270)
    position = reporting.build_position(nav)
    assert position["non_cash_value"] == position["pending_asset_value"] == 155
    report = report_holdings_fields(nav["holdings_data"], nav["position_data"])
    assert report_value_breakdown(report)["stock_value"] == 0
    assert report_value_breakdown(report)["non_cash_value"] == 155
    from src.domain.snapshot_contracts import NormalizedValuationSnapshot
    replay = NormalizedValuationSnapshot.from_compatibility_projection(nav["valuation"])
    assert replay.total_value == 270 and replay.equity_value == 0 and replay.pending_asset_value == 155
    from src.domain.nav_calculator import NavCalculator
    assert NavCalculator.project_valuation(nav["valuation"]).non_cash_value == 155
    evidence = service.build_valuation_evidence(holdings=balances)
    assert evidence["status"] == "complete"
    pending_evidence = [h for h in evidence["holdings"] if h["code"] == "CNY-FUTU-PENDING"]
    assert len(pending_evidence) == 1 and pending_evidence[0]["market_value_cny"] == 155
    assert "mmf_transit" not in nav["normalized_valuation"].canonical_payload()["holdings_provenance"]
    # A stale or conflicting old manual file cannot add to or block broker assets.
    funding["status"] = "unverified"
    assert service.get_holdings(include_price=True)["total_value"] == 270
    balances[-1].quantity = 0
    assert service.get_holdings(include_price=True)["total_value"] == 115


def test_shared_publication_previews_then_atomically_replaces(monkeypatch, tmp_path):
    from contextlib import nullcontext
    import json
    from stat import S_IMODE

    from src.app import mmf_transit_service
    from src import process_lock

    funding = {
        "success": True, "account": "sy", "status": "none", "transfers": [], "adjustments": {},
        "account_fingerprint": "broker-sy", "environment": "REAL", "snapshot_id": "snapshot-1",
    }
    from types import SimpleNamespace
    provider = SimpleNamespace(account_fingerprint="broker-sy", trd_env="REAL")
    monkeypatch.setattr(mmf_transit_service.FutuOpenApiBalanceProvider, "from_account", lambda account: provider)
    monkeypatch.setattr(mmf_transit_service, "current_mmf_balance", lambda account: (provider, "snapshot-1"))
    shared = tmp_path / "shared"
    monkeypatch.setenv("FUTU_MMF_FUNDING_DIR", str(shared))
    monkeypatch.setattr(mmf_transit_service, "read_mmf_funding", lambda account: funding)
    monkeypatch.setattr(process_lock, "process_lock", lambda *args, **kwargs: nullcontext())
    target = shared / "sy.json"
    assert mmf_transit_service.publish_mmf_funding("sy")["path"] == str(target)
    assert not shared.exists()
    assert mmf_transit_service.publish_mmf_funding("sy", confirm=True)["published"]
    assert json.loads(target.read_bytes()) == {"schema": "futu.mmf-funding.v1", **funding}
    assert S_IMODE(target.stat().st_mode) == 0o600
    assert mmf_transit_service.read_mmf_valuation_funding("sy")["status"] == "none"
    with target.open("r") as previous:
        funding["status"] = "unverified"
        mmf_transit_service.publish_mmf_funding("sy", confirm=True)
        assert json.load(previous)["status"] == "none"
    assert mmf_transit_service.read_mmf_valuation_funding("sy")["status"] == "unverified"
    assert list(shared.iterdir()) == [target]



def test_cancelled_app_receipt_invalidates_old_published_state(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.app import mmf_transit_service as service
    from src.app.futu_mmf_shared import read_mmf_shared
    from src.app.operation_state import OperationStateStore

    provider = SimpleNamespace(account_fingerprint="broker-sy", trd_env="REAL")
    receipt = {
        "account": "sy", "success": True, "source_snapshot_id": "balance-1",
        "source_metadata": {"account_fingerprint": "broker-sy", "trd_env": "REAL"},
        "cash_write": {
            "snapshot_id": "balance-1", "status": "verified", "fund_assets": 200,
            "balances": {"CNY": 0, "HKD": 0, "USD": 0},
            "observed_at_utc": "2026-11-20T03:00:00Z",
        },
    }
    shared = tmp_path / "shared"
    monkeypatch.setenv("FUTU_MMF_FUNDING_DIR", str(shared))
    monkeypatch.setattr(service.FutuOpenApiBalanceProvider, "from_account", lambda account: provider)
    monkeypatch.setattr(service.FutuSyncEvidenceStore, "latest", lambda self, account: receipt)
    monkeypatch.setattr(OperationStateStore, "resolve_db_path_read_only", staticmethod(
        lambda *args: tmp_path / "operation.sqlite3"
    ))
    monkeypatch.setattr("src.config.get_data_dir", lambda: tmp_path)
    assert service.publish_mmf_funding("sy", confirm=True)["status"] == "none"
    args = {
        "snapshot_id": "balance-1", "receipt_id": "withdrawn", "direction": "redemption",
        "currency": "HKD", "amount": "100", "source_debit": "0", "target_credit": "0",
        "request_date": "2026-11-20", "cancelled": True, "confirm": True,
    }
    service.record_app_receipt("sy", **args)
    assert read_mmf_shared("sy")["status"] == "unverified"
    assert service.publish_mmf_funding("sy", confirm=True)["status"] == "none"
    service.record_app_receipt("sy", **args)
    assert read_mmf_shared("sy")["status"] == "none"
    receipt["source_snapshot_id"] = receipt["cash_write"]["snapshot_id"] = "balance-2"
    receipt["cash_write"]["observed_at_utc"] = "2026-11-20T04:00:00Z"
    assert service.read_mmf_valuation_funding("sy")["status"] == "none"


def test_preview_without_database_does_not_create_it(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.app import mmf_transit_service as service
    from src.app.operation_state import OperationStateStore

    path = tmp_path / "operation.sqlite3"
    monkeypatch.setattr(service, "current_mmf_balance", lambda account: (
        SimpleNamespace(account_fingerprint="broker-sy"), "balance-1",
    ))
    monkeypatch.setattr(OperationStateStore, "resolve_db_path_read_only", staticmethod(lambda *args: path))
    with pytest.raises(ValueError, match="unconfirmed"):
        service.reconcile_app_receipt(
            "sy", snapshot_id="balance-1", receipt_id="no-receipt",
            clearing_date="2026-11-20", flow_ids=[],
        )
    assert not path.exists()



def test_historical_rebuild_ignores_retired_mmf_switch(monkeypatch, tmp_path):
    from src import config
    from src.app.nav_valuation_evidence_service import HistoricalNavValuationEvidenceService

    original_get = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        {"sy": {}} if key == "futu.profiles" else original_get(key, default)
    ))
    monkeypatch.setenv("FUTU_MMF_FUNDING_DIR", str(tmp_path))
    service = HistoricalNavValuationEvidenceService(storage=None, portfolio=None, evidence_store=object())
    with pytest.raises(ValueError, match="scope is incomplete"):
        service.prepare(
            account="sy", nav_date="2026-11-20", source_run_id="run-sy",
            expected_holdings_digest="", expected_cash_flow_fingerprint="",
            source_effect_store_revision="", valuation_as_of="", usdcny=7, hkdcny=0.85,
        )
