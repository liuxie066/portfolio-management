from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from src.app.portfolio_read_service import PortfolioReadService
from src.models import AssetType, Holding


def test_get_holdings_with_price_reuses_snapshot_contract():
    valuation = SimpleNamespace(
        holdings=[
            SimpleNamespace(
                asset_id="AAPL",
                asset_name="Apple",
                quantity=1,
                asset_type=AssetType.US_STOCK,
                broker="富途",
                currency="USD",
                current_price=100,
                cny_price=720,
                market_value_cny=720,
                weight=0.9,
            ),
            SimpleNamespace(
                asset_id="CNY-CASH",
                asset_name="人民币现金",
                quantity=80,
                asset_type=AssetType.CASH,
                broker="富途",
                currency="CNY",
                current_price=1,
                cny_price=1,
                market_value_cny=80,
                weight=0.1,
            ),
        ],
        total_value_cny=800,
        cash_value_cny=80,
        stock_value_cny=720,
        fund_value_cny=0,
        cash_ratio=0.1,
        stock_ratio=0.9,
        fund_ratio=0,
        warnings=["w"],
    )
    portfolio = SimpleNamespace(calculate_valuation=Mock(return_value=valuation))
    storage = Mock()
    service = PortfolioReadService(
        account="lx",
        storage=storage,
        portfolio=portfolio,
        reporting_service=Mock(),
    )

    result = service.get_holdings(include_price=True, include_cash=False, group_by_broker=True)

    portfolio.calculate_valuation.assert_called_once_with("lx")
    storage.get_holdings.assert_not_called()
    assert result["total_value"] == 800
    assert result["cash_value"] == 80
    assert result["warnings"] == ["w"]
    assert list(result["by_market"]) == ["富途"]
    assert result["market_values"] == {"富途": 720}
    assert [h["code"] for h in result["by_market"]["富途"]] == ["AAPL"]


def test_build_snapshot_passes_price_timeout_to_valuation():
    valuation = SimpleNamespace(
        holdings=[],
        total_value_cny=0,
        cash_value_cny=0,
        stock_value_cny=0,
        fund_value_cny=0,
        cash_ratio=0,
        stock_ratio=0,
        fund_ratio=0,
        warnings=[],
    )
    portfolio = SimpleNamespace(calculate_valuation=Mock(return_value=valuation))
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=portfolio,
        reporting_service=Mock(),
    )

    result = service.build_snapshot(price_timeout_seconds=9)

    assert result["valuation"] is valuation
    portfolio.calculate_valuation.assert_called_once_with("lx", price_timeout_seconds=9)


def test_build_snapshot_passes_optional_run_quote_pool_to_valuation():
    valuation = SimpleNamespace(
        holdings=[],
        total_value_cny=0,
        cash_value_cny=0,
        stock_value_cny=0,
        fund_value_cny=0,
        cash_ratio=0,
        stock_ratio=0,
        fund_ratio=0,
        warnings=[],
    )
    run_pool = object()
    portfolio = SimpleNamespace(calculate_valuation=Mock(return_value=valuation))
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=portfolio,
        reporting_service=Mock(),
    )

    result = service.build_snapshot(price_timeout_seconds=9, run_quote_pool=run_pool)

    assert result["valuation"] is valuation
    portfolio.calculate_valuation.assert_called_once_with(
        "lx",
        price_timeout_seconds=9,
        run_quote_pool=run_pool,
    )


def test_build_valuation_evidence_preserves_quote_and_fx_provenance():
    valuation = SimpleNamespace(
        holdings=[
            SimpleNamespace(
                asset_id="USD-CASH",
                asset_name="美元现金",
                quantity=100,
                asset_type=AssetType.CASH,
                account="lx",
                broker="富途",
                currency="USD",
                current_price=1,
                cny_price=7.2,
                market_value_cny=720,
            )
        ],
        price_evidence={
            "USD-CASH": {
                "price": 1,
                "cny_price": 7.2,
                "currency": "USD",
                "exchange_rate": 7.2,
                "source": "fixed",
                "source_chain": ["fixed", "fx"],
                "fetched_at": "2026-07-24T01:00:00+00:00",
            },
            "NVDA": {
                "price": 120,
                "cny_price": 864,
                "currency": "USD",
                "exchange_rate": 7.2,
                "source": "finnhub",
                "fetched_at": "2026-07-24T01:00:01+00:00",
            },
        },
        warnings=["[价格汇总] realtime=2"],
    )
    portfolio = SimpleNamespace(calculate_valuation=Mock(return_value=valuation))
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=portfolio,
        reporting_service=Mock(),
    )
    pool = object()

    result = service.build_valuation_evidence(
        supplemental_codes=["NVDA"],
        price_timeout_seconds=9,
        run_quote_pool=pool,
    )

    assert result["status"] == "complete"
    assert result["holdings"][0]["asset_type"] == "cash"
    assert result["quotes"][0]["exchange_rate_to_cny"] == 7.2
    assert {item["code"] for item in result["quotes"]} == {"USD-CASH", "NVDA"}
    portfolio.calculate_valuation.assert_called_once_with(
        "lx",
        price_timeout_seconds=9,
        run_quote_pool=pool,
        supplemental_codes=["NVDA"],
    )


def test_build_valuation_evidence_marks_missing_supplemental_quote_partial():
    valuation = SimpleNamespace(
        holdings=[],
        price_evidence={},
        warnings=[],
    )
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=SimpleNamespace(calculate_valuation=Mock(return_value=valuation)),
        reporting_service=Mock(),
    )

    result = service.build_valuation_evidence(supplemental_codes=["NVDA"])

    assert result["status"] == "partial"
    assert result["quotes"] == []
    assert result["warnings"] == ["NVDA: supplemental quote missing"]


def test_get_holdings_without_price_keeps_light_storage_read():
    portfolio = SimpleNamespace(calculate_valuation=Mock())
    storage = SimpleNamespace(
        get_holdings=Mock(return_value=[
            Holding(
                asset_id="CNY-CASH",
                asset_name="人民币现金",
                asset_type=AssetType.CASH,
                account="lx",
                broker="富途",
                quantity=10,
                currency="CNY",
            ),
            Holding(
                asset_id="AAPL",
                asset_name="Apple",
                asset_type=AssetType.US_STOCK,
                account="lx",
                broker="富途",
                quantity=1,
                currency="USD",
            ),
        ])
    )
    service = PortfolioReadService(
        account="lx",
        storage=storage,
        portfolio=portfolio,
        reporting_service=Mock(),
    )

    result = service.get_holdings(include_price=False, include_cash=False)

    portfolio.calculate_valuation.assert_not_called()
    storage.get_holdings.assert_called_once_with(account="lx")
    assert result == {
        "success": True,
        "count": 1,
        "holdings": [
            {
                "code": "AAPL",
                "name": "Apple",
                "quantity": 1.0,
                "type": "us_stock",
                "normalized_type": "stock",
                "broker": "富途",
                "currency": "USD",
            }
        ],
    }


def test_get_position_delegates_to_reporting_service_snapshot():
    snapshot = {"valuation": object(), "holdings_data": {}}
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=SimpleNamespace(calculate_valuation=Mock()),
        reporting_service=Mock(),
    )
    service.build_snapshot = Mock(return_value=snapshot)
    service.reporting_service.build_position.return_value = {"success": True, "total_value": 1}

    result = service.get_position()

    assert result == {"success": True, "total_value": 1}
    service.build_snapshot.assert_called_once_with()
    service.reporting_service.build_position.assert_called_once_with(snapshot)


def test_merge_holdings_data_preserves_account_and_sums_total():
    merged = PortfolioReadService.merge_holdings_data([
        {
            "success": True,
            "holdings": [
                {"code": "AAPL", "account": "lx", "quantity": 5},
            ],
            "total_value": 100.0,
        },
        {
            "success": True,
            "holdings": [
                {"code": "AAPL", "account": "alice", "quantity": 5},
            ],
            "total_value": 100.0,
        },
    ])

    assert merged["success"] is True
    assert merged["total_value"] == 200.0
    assert [h["account"] for h in merged["holdings"]] == ["lx", "alice"]


def test_get_asset_distribution_delegates_to_reporting_service():
    holdings_data = {"success": True, "holdings": [{"code": "AAPL"}], "total_value": 1}
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=SimpleNamespace(calculate_valuation=Mock()),
        reporting_service=Mock(),
    )
    service.reporting_service.build_asset_distribution.return_value = {"success": True, "by_asset": []}

    result = service.get_asset_distribution(holdings_data=holdings_data, include_value=False)

    assert result == {"success": True, "by_asset": []}
    service.reporting_service.build_asset_distribution.assert_called_once()
    passed_snapshot = service.reporting_service.build_asset_distribution.call_args[0][0]
    assert passed_snapshot["holdings_data"] is holdings_data
    assert service.reporting_service.build_asset_distribution.call_args[1]["include_value"] is False


def test_get_distribution_accepts_prebuilt_holdings_data_without_refetch():
    holdings_data = {
        "success": True,
        "holdings": [{"code": "AAPL", "normalized_type": "stock", "market_value": 1}],
        "total_value": 1,
    }
    service = PortfolioReadService(
        account="lx",
        storage=Mock(),
        portfolio=SimpleNamespace(calculate_valuation=Mock()),
        reporting_service=Mock(),
    )
    service.build_snapshot = Mock()
    service.reporting_service.build_distribution.return_value = {"success": True, "total_value": 1}

    result = service.get_distribution(holdings_data=holdings_data)

    assert result == {"success": True, "total_value": 1}
    service.build_snapshot.assert_not_called()
    service.reporting_service.build_distribution.assert_called_once_with({"holdings_data": holdings_data})
