from __future__ import annotations

from dataclasses import replace

from src.app.futu_balance_sync_service import (
    FutuBalanceSyncService,
    FutuPortfolioSnapshot,
    FutuPositionSnapshot,
)
from src.app.holdings_reconciliation_service import HoldingsReconciliationService
from src.domain.holdings import RawHoldingRecord


class _RawStorage:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def get_raw_holdings(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.records)


def _snapshot(*positions):
    return FutuPortfolioSnapshot(
        cash_by_currency={"CNY": 1.0, "USD": 2.0, "HKD": 3.0},
        mmf=4.0,
        positions=tuple(positions),
        source="futu-openapi",
        account_id=123,
        profile_fingerprint="profile",
        cash_source_fields={"CNY": "cn_cash", "USD": "us_cash", "HKD": "hk_cash"},
        cash_present_by_currency={"CNY": True, "USD": True, "HKD": True},
        mmf_source_field="fund_assets",
        mmf_present=True,
        source_snapshot_id="snap-1",
        observed_at_utc="2026-07-31T12:00:00Z",
        account_fingerprint="sha256:" + __import__("hashlib").sha256(b"123").hexdigest(),
        trd_env="REAL",
        trd_market="US",
        refresh_cache=True,
        account_verified=True,
        pagination_complete=True,
    )


def test_reconcile_reads_futu_at_most_once_per_account_and_never_writes():
    storage = _RawStorage(
        [
            RawHoldingRecord(
                "rec_1",
                {
                    "asset_id": "AAPL",
                    "asset_name": "Apple",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 1,
                    "currency": "USD",
                },
            ),
            RawHoldingRecord(
                "rec_2",
                {
                    "asset_id": "MSFT",
                    "asset_name": "Microsoft",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 2,
                    "currency": "USD",
                },
            ),
        ]
    )
    calls = []

    def observe(account):
        calls.append(account)
        return _snapshot(
            FutuPositionSnapshot("AAPL", "Apple", "STOCK", 1, 100, "USD", "US", raw_code="US.AAPL"),
            FutuPositionSnapshot("MSFT", "Microsoft", "STOCK", 2, 200, "USD", "US", raw_code="US.MSFT"),
        )

    result = HoldingsReconciliationService(
        storage=storage,
        futu_observer=observe,
    ).reconcile(account="lx")

    assert calls == ["lx"]
    assert storage.calls == [{"account": "lx", "record_id": None}]
    assert result["read_only"] is True
    assert result["futu_observation_count"] == 1
    assert result["success"] is True


def test_reconcile_provider_failure_stays_visible_and_does_not_guess_hk_currency():
    storage = _RawStorage(
        [
            RawHoldingRecord(
                "rec_hk",
                {
                    "asset_id": "00700",
                    "asset_type": "hk_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 1,
                    "currency": "",
                },
            )
        ]
    )

    result = HoldingsReconciliationService(
        storage=storage,
        futu_observer=lambda _account: (_ for _ in ()).throw(RuntimeError("OpenD unavailable")),
    ).reconcile(record_id="rec_hk")

    assert result["evidence_errors"] == {"lx": "OpenD unavailable"}
    assert result["success"] is False
    assert result["status"] == "holdings_evidence_unavailable"
    currency = next(
        item
        for item in result["records"][0]["outcomes"]
        if item["field"] == "currency"
    )
    assert currency["status"] == "missing_manual"
    assert currency["proposed"] is None


def test_reconcile_never_reports_success_when_futu_evidence_fetch_failed():
    storage = _RawStorage(
        [
            RawHoldingRecord(
                "rec_us",
                {
                    "asset_id": "AAPL",
                    "asset_name": "Apple Inc.",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 1,
                    "currency": "USD",
                },
            )
        ]
    )

    result = HoldingsReconciliationService(
        storage=storage,
        futu_observer=lambda _account: (_ for _ in ()).throw(RuntimeError("OpenD unavailable")),
    ).reconcile(account="lx")

    assert result["blocking_record_count"] == 0
    assert result["success"] is False
    assert result["status"] == "holdings_evidence_unavailable"


def test_observe_portfolio_is_read_only_and_requires_authoritative_snapshot():
    expected = _snapshot()

    class Provider:
        def fetch_portfolio(self):
            return expected

    class NoWriteStorage:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected storage access: {name}")

    observed = FutuBalanceSyncService(
        NoWriteStorage(),
        provider=Provider(),
    ).observe_portfolio(account="lx")

    assert observed is expected


def test_observe_portfolio_rejects_incomplete_pagination():
    incomplete = replace(_snapshot(), pagination_complete=False)

    class Provider:
        def fetch_portfolio(self):
            return incomplete

    service = FutuBalanceSyncService(object(), provider=Provider())
    try:
        service.observe_portfolio(account="lx")
    except RuntimeError as exc:
        assert "pagination is incomplete" in str(exc)
    else:
        raise AssertionError("expected fail-closed pagination validation")


def test_reconcile_rejects_invalid_explicit_futu_currency_evidence():
    storage = _RawStorage(
        [
            RawHoldingRecord(
                "rec_us",
                {
                    "asset_id": "AAPL",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 1,
                    "currency": "USD",
                },
            )
        ]
    )
    snapshot = _snapshot(
        FutuPositionSnapshot(
            "AAPL",
            "Apple",
            "STOCK",
            1,
            100,
            "US D",
            "US",
            raw_code="US.AAPL",
            currency_explicit=True,
        )
    )

    result = HoldingsReconciliationService(
        storage=storage,
        futu_observer=lambda _account: snapshot,
    ).reconcile(account="lx")

    assert result["success"] is False
    assert result["status"] == "holdings_evidence_unavailable"
    assert "explicit currency is invalid" in result["evidence_errors"]["lx"]
