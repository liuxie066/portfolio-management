from unittest.mock import Mock

from src.app.valuation_service import ValuationService
from src.models import AssetClass, AssetType, Holding
from src.portfolio import PortfolioManager


def _manager(storage, fetcher):
    return PortfolioManager(storage=storage, price_fetcher=fetcher)


def test_valuation_service_empty_holdings():
    storage = Mock()
    fetcher = Mock()
    storage.get_holdings.return_value = []
    manager = _manager(storage, fetcher)
    service = ValuationService(manager=manager, storage=storage, price_fetcher=fetcher)

    result = service.calculate_valuation("a")

    assert result.total_value_cny == 0
    assert result.holdings == []


def test_valuation_service_values_holdings_with_prices():
    storage = Mock()
    fetcher = Mock()
    storage.get_holdings.return_value = [
        Holding(
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            account="a",
            quantity=1000,
            currency="CNY",
            asset_class=AssetClass.CN_ASSET,
        ),
        Holding(
            asset_id="CNY-CASH",
            asset_name="人民币现金",
            asset_type=AssetType.CASH,
            account="a",
            quantity=50000,
            currency="CNY",
            asset_class=AssetClass.CASH,
        ),
    ]
    fetcher.fetch_batch.return_value = {
        "000001": {"price": 10.5, "cny_price": 10.5, "currency": "CNY"},
    }
    storage.get_total_shares.return_value = 1000000
    manager = _manager(storage, fetcher)
    service = ValuationService(manager=manager, storage=storage, price_fetcher=fetcher)

    result = service.calculate_valuation("a")

    assert result.total_value_cny == 60500.0
    assert result.stock_value_cny == 10500.0
    assert result.cash_value_cny == 50000.0
    assert result.cn_asset_value == 10500.0
    assert result.nav == 0.0605
    assert result.holdings[0].weight == 0.173554


def test_valuation_service_matches_price_keys_case_insensitively():
    storage = Mock()
    fetcher = Mock()
    storage.get_holdings.return_value = [
        Holding(
            asset_id="baba",
            asset_name="阿里巴巴",
            asset_type=AssetType.US_STOCK,
            account="a",
            quantity=10,
            currency="USD",
            asset_class=AssetClass.US_ASSET,
        ),
    ]
    fetcher.fetch_batch.return_value = {
        "BABA": {"price": 80.0, "cny_price": 580.0, "currency": "USD"},
    }
    storage.get_total_shares.return_value = 1000
    manager = _manager(storage, fetcher)
    service = ValuationService(manager=manager, storage=storage, price_fetcher=fetcher)

    result = service.calculate_valuation("a")

    assert result.total_value_cny == 5800.0
    assert result.us_asset_value == 5800.0
    assert result.holdings[0].current_price == 80.0
    fetcher.fetch_batch.assert_called_once()
    kwargs = fetcher.fetch_batch.call_args.kwargs
    assert kwargs["asset_type_map"]["baba"] == AssetType.US_STOCK
    assert kwargs["asset_type_map"]["BABA"] == AssetType.US_STOCK


def test_valuation_service_warns_for_missing_foreign_cash_fx():
    storage = Mock()
    fetcher = Mock()
    storage.get_holdings.return_value = [
        Holding(
            asset_id="USD-CASH",
            asset_name="美元现金",
            asset_type=AssetType.CASH,
            account="a",
            quantity=100,
            currency="USD",
            asset_class=AssetClass.CASH,
        ),
    ]
    fetcher.fetch_batch.return_value = {}
    storage.get_total_shares.return_value = 1000
    manager = _manager(storage, fetcher)
    service = ValuationService(manager=manager, storage=storage, price_fetcher=fetcher)

    result = service.calculate_valuation("a")

    assert result.total_value_cny == 0.0
    assert any("无法获取汇率" in warning for warning in result.warnings)
