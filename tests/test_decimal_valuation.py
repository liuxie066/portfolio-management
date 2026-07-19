from unittest.mock import Mock

from src.portfolio import PortfolioManager
from src.models import Holding, AssetType, AssetClass


def test_calculate_valuation_uses_decimal_quantization_for_market_values():
    storage = Mock()
    fetcher = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=fetcher)

    holdings = [
        Holding(
            asset_id='AAA',
            asset_name='AAA',
            asset_type=AssetType.A_STOCK,
            account='测试账户',
            quantity=3,
            currency='CNY',
            asset_class=AssetClass.CN_ASSET,
        ),
        Holding(
            asset_id='CNY-CASH',
            asset_name='人民币现金',
            asset_type=AssetType.CASH,
            account='测试账户',
            quantity=1.005,
            currency='CNY',
            asset_class=AssetClass.CASH,
        ),
    ]
    storage.get_holdings.return_value = holdings
    storage.get_total_shares.return_value = 10
    fetcher.fetch_batch.return_value = {
        'AAA': {'price': 0.335, 'cny_price': 0.335, 'currency': 'CNY'}
    }

    result = manager.calculate_valuation('测试账户')

    # 3 * 0.335 = 1.005 -> 1.01
    # 现金 1.005 -> 1.01
    assert result.stock_value_cny == 1.01
    assert result.cash_value_cny == 1.01
    assert result.total_value_cny == 2.02
    assert result.cn_asset_value == 1.01
    assert result.nav == 0.202
    stock_holding = [h for h in result.holdings if h.asset_id == 'AAA'][0]
    cash_holding = [h for h in result.holdings if h.asset_id == 'CNY-CASH'][0]
    assert stock_holding.market_value_cny == 1.01
    assert cash_holding.market_value_cny == 1.01
    assert stock_holding.weight == 0.5
    assert cash_holding.weight == 0.5


def test_valuation_excludes_unconvertible_foreign_and_unpriced_cny_security():
    storage = Mock()
    fetcher = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=fetcher)
    storage.get_holdings.return_value = [
        Holding(
            asset_id="AAPL",
            asset_name="Apple",
            asset_type=AssetType.US_STOCK,
            account="测试账户",
            quantity=2,
            currency="USD",
            asset_class=AssetClass.US_ASSET,
        ),
        Holding(
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            account="测试账户",
            quantity=100,
            currency="CNY",
            asset_class=AssetClass.CN_ASSET,
        ),
        Holding(
            asset_id="CNY-MMF",
            asset_name="货币基金",
            asset_type=AssetType.MMF,
            account="测试账户",
            quantity=50.125,
            currency="CNY",
            asset_class=AssetClass.CASH,
        ),
    ]
    storage.get_total_shares.return_value = 100
    fetcher.fetch_batch.return_value = {
        "AAPL": {"price": 200, "currency": "USD"},
    }

    result = manager.calculate_valuation("测试账户")

    assert result.total_value_cny == 50.13
    assert result.cash_value_cny == 50.13
    assert result.stock_value_cny == 0
    assert result.holdings[0].market_value_cny is None
    assert result.holdings[1].market_value_cny is None
    assert result.holdings[2].market_value_cny == 50.13
    assert sum("价格缺失" in warning for warning in result.warnings) == 2


def test_valuation_timeout_makes_one_deadline_bound_fetch_attempt():
    storage = Mock()
    fetcher = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=fetcher)
    storage.get_holdings.return_value = [
        Holding(
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            account="测试账户",
            quantity=1,
            currency="CNY",
            asset_class=AssetClass.CN_ASSET,
        )
    ]
    storage.get_total_shares.return_value = 1
    fetcher.fetch_batch.side_effect = TimeoutError("deadline")

    result = manager.calculate_valuation("测试账户", price_timeout_seconds=0.01)

    fetcher.fetch_batch.assert_called_once()
    assert "deadline" in fetcher.fetch_batch.call_args.kwargs
    assert result.total_value_cny == 0
    assert any("价格获取超时" in warning for warning in result.warnings)
