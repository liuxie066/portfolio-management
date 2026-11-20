from dataclasses import replace
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
            {"cashflow_id": "f1", "settlement_date": day, "cashflow_direction": "IN",
             "currency": "HKD", "cashflow_amount": 100},
        ],
    )
    monkeypatch.setattr(service, "current_mmf_balance", lambda account: (provider, "balance-1"))
    monkeypatch.setattr(service, "OperationStateStore", lambda: OperationStateStore(path))
    kwargs = {
        "snapshot_id": "balance-1", "receipt_id": "app-1", "direction": "redemption",
        "currency": "HKD", "amount": "100", "source_debit": "100", "target_credit": "100",
    }
    assert service.record_app_receipt("sy", **kwargs)["recorded"] is False
    assert not path.exists()
    assert service.record_app_receipt("sy", **kwargs, confirm=True)["recorded"] is True
    recon = {
        "snapshot_id": "balance-1", "receipt_id": "app-1",
        "clearing_date": "2026-11-20", "flow_ids": ["f1"],
    }
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
        "source_metadata": {"account_fingerprint": "broker-sy"},
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
    monkeypatch.setattr(mmf_transit_service, "read_mmf_funding", lambda account: funding)
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
    transit = [row for row in view["holdings"] if row["code"] == "CNY-MMF-TRANSIT"]
    assert len(transit) == 1
    assert transit[0]["name"] == "货基互转在途"
    assert nav["valuation"].total_value_cny == view["total_value"] == 270
    assert len(balances) == 2
    assert nav["normalized_valuation"].canonical_payload()["holdings_provenance"]["mmf_transit"] == {
        "snapshot_id": "balance-1", "amounts": {"HKD": "100", "USD": "10"},
        "value_cny": "155.00",
    }
    with pytest.raises(ValueError, match="formal NAV"):
        service.build_snapshot(
            holdings=balances, holdings_provenance={"futu_capture": {"snapshot_id": "balance-2"}},
        )
    funding["adjustments"] = {}
    settled = service.get_holdings(include_price=True)
    assert all(row["code"] != "CNY-MMF-TRANSIT" for row in settled["holdings"])
