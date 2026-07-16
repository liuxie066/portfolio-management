from unittest.mock import Mock

from src.app.reporting_service import ReportingService
from src.models import AssetType, Holding, Industry, PortfolioValuation
from src.portfolio import PortfolioManager
from src.reporting_utils import normalize_asset_type


def test_reporting_service_asset_distribution_uses_manager_valuation():
    storage = Mock()
    manager = Mock()
    manager.calculate_valuation.return_value = PortfolioValuation(
        account="a",
        total_value_cny=200.0,
        cash_value_cny=50.0,
        stock_value_cny=100.0,
        fund_value_cny=50.0,
        cn_asset_value=100.0,
        us_asset_value=75.0,
        hk_asset_value=25.0,
    )
    service = ReportingService(manager=manager, storage=storage)

    result = service.get_asset_distribution("a")

    assert result == {
        "现金": 0.25,
        "股票": 0.5,
        "基金": 0.25,
        "中国资产": 0.5,
        "美国资产": 0.375,
        "港股资产": 0.125,
    }


def test_reporting_service_asset_distribution_returns_empty_for_zero_value():
    storage = Mock()
    manager = Mock()
    manager.calculate_valuation.return_value = PortfolioValuation(account="a", total_value_cny=0.0)
    service = ReportingService(manager=manager, storage=storage)

    assert service.get_asset_distribution("a") == {}


def test_normalize_asset_type_treats_split_funds_as_fund():
    assert normalize_asset_type(AssetType.EXCHANGE_FUND, "510300") == "fund"
    assert normalize_asset_type(AssetType.OTC_FUND, "110022") == "fund"


def test_normalize_asset_type_keeps_crypto_separate_from_cash_and_stock():
    assert normalize_asset_type(AssetType.CRYPTO, "TRADING-CRYPTO-USD") == "crypto"


def test_reporting_service_industry_distribution_with_price_and_cny_fallback():
    storage = Mock()
    manager = Mock()
    manager.price_fetcher = Mock()
    holdings = [
        Holding(
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            account="a",
            quantity=100,
            currency="CNY",
            industry=Industry.FINANCE,
            market_value_cny=1000.0,
        ),
        Holding(
            asset_id="CNY-CASH",
            asset_name="人民币现金",
            asset_type=AssetType.CASH,
            account="a",
            quantity=50,
            currency="CNY",
            industry=None,
            market_value_cny=50.0,
        ),
    ]
    manager.calculate_valuation.return_value = PortfolioValuation(account="a", total_value_cny=1050.0, holdings=holdings)
    service = ReportingService(manager=manager, storage=storage)

    result = service.get_industry_distribution("a")

    assert result["金融"] == 1000.0 / 1050.0
    assert result["其他"] == 50.0 / 1050.0
    manager.calculate_valuation.assert_called_once_with("a")
    manager.price_fetcher.fetch_batch.assert_not_called()
    storage.get_holdings.assert_not_called()


def test_reporting_service_industry_distribution_returns_empty_without_value():
    storage = Mock()
    manager = Mock()
    manager.price_fetcher = None
    manager.calculate_valuation.return_value = PortfolioValuation(account="a", total_value_cny=0.0, holdings=[
        Holding(
            asset_id="AAPL",
            asset_name="Apple",
            asset_type=AssetType.US_STOCK,
            account="a",
            quantity=1,
            currency="USD",
            industry=Industry.TECH,
        )
    ])
    service = ReportingService(manager=manager, storage=storage)

    assert service.get_industry_distribution("a") == {}
    storage.get_holdings.assert_not_called()


def test_portfolio_distribution_methods_delegate_to_reporting_service():
    storage = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=Mock())
    manager.reporting_service = Mock()
    manager.reporting_service.get_asset_distribution.return_value = {"现金": 1.0}
    manager.reporting_service.get_industry_distribution.return_value = {"其他": 1.0}

    assert manager.get_asset_distribution("a") == {"现金": 1.0}
    assert manager.get_industry_distribution("a") == {"其他": 1.0}
    manager.reporting_service.get_asset_distribution.assert_called_once_with("a")
    manager.reporting_service.get_industry_distribution.assert_called_once_with("a")


def test_reporting_service_build_position_uses_valuation():
    storage = Mock()
    manager = Mock()
    service = ReportingService(manager=manager, storage=storage)
    valuation = PortfolioValuation(
        account="a",
        total_value_cny=200.0,
        cash_value_cny=50.0,
        stock_value_cny=100.0,
        fund_value_cny=50.0,
    )

    result = service.build_position({"valuation": valuation})

    assert result == {
        "success": True,
        "total_value": 200.0,
        "stock_value": 100.0,
        "fund_value": 50.0,
        "cash_value": 50.0,
        "stock_ratio": 0.5,
        "fund_ratio": 0.25,
        "cash_ratio": 0.25,
    }


def test_reporting_service_build_distribution_uses_snapshot_holdings():
    storage = Mock()
    manager = Mock()
    service = ReportingService(manager=manager, storage=storage)
    valuation = PortfolioValuation(account="a", total_value_cny=300.0)
    snapshot = {
        "valuation": valuation,
        "holdings_data": {
            "holdings": [
                {"code": "AAPL", "normalized_type": "stock", "broker": "富途", "currency": "USD", "market_value": 100.0},
                {"code": "CNY-MMF", "normalized_type": "cash", "broker": "富途", "currency": "CNY", "market_value": 50.0},
                {"code": "110022", "normalized_type": "fund", "broker": "平安", "currency": "CNY", "market_value": 150.0},
            ],
        },
    }

    result = service.build_distribution(snapshot)

    assert result["success"] is True
    assert result["total_value"] == 300.0
    assert result["by_type"] == [
        {"type": "fund", "value": 150.0, "ratio": 0.5},
        {"type": "stock", "value": 100.0, "ratio": 1 / 3},
        {"type": "cash", "value": 50.0, "ratio": 1 / 6},
    ]
    assert result["by_market"] == [
        {"broker": "富途", "value": 150.0, "ratio": 0.5},
        {"broker": "平安", "value": 150.0, "ratio": 0.5},
    ]
    assert result["by_currency"] == [
        {"currency": "CNY", "value": 200.0, "ratio": 2 / 3},
        {"currency": "USD", "value": 100.0, "ratio": 1 / 3},
    ]


def test_reporting_service_build_asset_distribution_merges_accounts_and_includes_value():
    storage = Mock()
    manager = Mock()
    service = ReportingService(manager=manager, storage=storage)
    valuation = PortfolioValuation(account="a", total_value_cny=300.0)
    snapshot = {
        "valuation": valuation,
        "holdings_data": {
            "holdings": [
                {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "broker": "富途", "currency": "USD", "account": "lx", "quantity": 5, "market_value": 100.0},
                {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "broker": "老虎", "currency": "USD", "account": "alice", "quantity": 5, "market_value": 100.0},
                {"code": "CNY-MMF", "name": "MMF", "normalized_type": "cash", "broker": "富途", "currency": "CNY", "account": "lx", "quantity": 50, "market_value": 50.0},
            ],
        },
    }

    result = service.build_asset_distribution(snapshot, include_value=True)

    assert result["success"] is True
    assert result["total_value"] == 300.0
    assert len(result["by_asset"]) == 2
    aapl = result["by_asset"][0]
    assert aapl["code"] == "AAPL"
    assert aapl["quantity"] == 10.0
    assert aapl["value"] == 200.0
    assert aapl["ratio"] == 2 / 3
    assert aapl["accounts"] == {"lx": 5.0, "alice": 5.0}
    assert aapl["breakdown"] == [
        {"account": "lx", "broker": "富途", "quantity": 5, "value": 100.0},
        {"account": "alice", "broker": "老虎", "quantity": 5, "value": 100.0},
    ]


def test_reporting_service_build_asset_distribution_groups_cash_equivalents():
    service = ReportingService(manager=Mock(), storage=Mock())
    snapshot = {
        "holdings_data": {
            "total_value": 350.0,
            "holdings": [
                {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "broker": "富途", "currency": "USD", "account": "lx", "quantity": 1, "market_value": 100.0},
                {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "broker": "富途", "currency": "USD", "account": "sy", "quantity": 1, "market_value": 100.0},
                {"code": "CNY-CASH", "name": "人民币现金", "type": "cash", "broker": "富途", "currency": "CNY", "account": "lx", "quantity": 100, "market_value": 100.0},
                {"code": "CNY-MMF", "name": "货币基金", "type": "mmf", "broker": "富途", "currency": "CNY", "account": "sy", "quantity": 50, "market_value": 50.0},
            ],
        },
    }

    result = service.build_asset_distribution(snapshot, group_cash=True)

    assert [row["code"] for row in result["by_asset"]] == ["AAPL", "CASH+MMF"]
    assert result["by_asset"][0]["quantity"] == 2.0
    cash = result["by_asset"][1]
    assert cash["name"] == "现金及等价物"
    assert cash["quantity"] == 150.0
    assert cash["value"] == 150.0
    assert cash["ratio"] == 150 / 350
    assert cash["accounts"] == {"lx": 100.0, "sy": 50.0}


def test_reporting_service_group_cash_keeps_crypto_separate():
    service = ReportingService(manager=Mock(), storage=Mock())
    snapshot = {
        "holdings_data": {
            "total_value": 150.0,
            "holdings": [
                {"code": "CNY-CASH", "name": "人民币现金", "type": "cash", "broker": "富途", "currency": "CNY", "account": "lx", "quantity": 100, "market_value": 100.0},
                {"code": "TRADING-CRYPTO-USD", "name": "币安交易账户", "type": "crypto", "broker": "币安", "currency": "USD", "account": "lx", "quantity": 7.0, "market_value": 50.0},
            ],
        },
    }

    result = service.build_asset_distribution(snapshot, group_cash=True)

    assert [row["code"] for row in result["by_asset"]] == ["CASH+MMF", "TRADING-CRYPTO-USD"]
    assert result["by_asset"][1]["normalized_type"] == "crypto"


def test_reporting_service_build_asset_distribution_hides_value_when_requested():
    storage = Mock()
    manager = Mock()
    service = ReportingService(manager=manager, storage=storage)
    snapshot = {
        "holdings_data": {
            "holdings": [
                {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "broker": "富途", "currency": "USD", "account": "lx", "quantity": 5, "market_value": 100.0},
                {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "broker": "老虎", "currency": "USD", "account": "alice", "quantity": 5, "market_value": 100.0},
            ],
        },
    }

    result = service.build_asset_distribution(snapshot, include_value=False)

    assert result["success"] is True
    assert "total_value" not in result
    assert result["total_quantity"] == 10.0
    aapl = result["by_asset"][0]
    assert "value" not in aapl
    assert "ratio" not in aapl
    assert aapl["quantity"] == 10.0
    assert aapl["quantity_ratio"] == 1.0
    assert "value" not in aapl["breakdown"][0]
