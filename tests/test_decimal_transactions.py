from datetime import date
from unittest.mock import Mock, patch

from src.portfolio import PortfolioManager
from src.models import Transaction, TransactionType, Holding, AssetType


@patch.object(PortfolioManager, '_get_asset_name', return_value='平安银行')
def test_buy_uses_decimal_quantized_total_cost(mock_get_name):
    storage = Mock()
    fetcher = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=fetcher)

    storage.add_transaction.return_value = Transaction(
        tx_date=date(2025, 3, 14),
        tx_type=TransactionType.BUY,
        asset_id='000001',
        asset_name='平安银行',
        account='测试账户',
        quantity=1,
        price=1.005,
        currency='CNY',
    )
    storage.get_holding.return_value = None
    storage.replace_holding.side_effect = lambda holding: holding
    manager.cash_service.plan_deduct_cash_targets = Mock(return_value=[])

    manager.buy(
        tx_date=date(2025, 3, 14),
        asset_id='000001',
        asset_name='平安银行',
        asset_type=AssetType.A_STOCK,
        account='测试账户',
        quantity=1,
        price=1.005,
        currency='CNY',
        fee=0.005,
        auto_deduct_cash=True,
    )

    manager.cash_service.plan_deduct_cash_targets.assert_called_once_with('测试账户', 1.02)


def test_sell_uses_decimal_quantized_proceeds():
    storage = Mock()
    fetcher = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=fetcher)
    stock_holding = Holding(
        record_id='holding-1',
        asset_id='000001',
        asset_name='平安银行',
        asset_type=AssetType.A_STOCK,
        account='测试账户',
        quantity=1000,
        currency='CNY',
    )
    storage.get_holding.side_effect = (
        lambda asset_id, account, broker=None: stock_holding if asset_id == '000001' else None
    )
    storage.add_transaction.side_effect = lambda tx: tx
    storage.replace_holding.side_effect = lambda holding: holding
    cash_target = Holding(
        asset_id='CNY-CASH', asset_name='人民币现金', asset_type=AssetType.CASH,
        account='测试账户', quantity=1.0, currency='CNY'
    )
    manager.cash_service.plan_add_cash_target = Mock(return_value=(None, cash_target))

    manager.sell(
        tx_date=date(2025, 3, 14),
        asset_id='000001',
        account='测试账户',
        quantity=1,
        price=1.005,
        currency='CNY',
        fee=0.005,
        auto_add_cash=True,
    )

    manager.cash_service.plan_add_cash_target.assert_called_once_with('测试账户', 1.0)
