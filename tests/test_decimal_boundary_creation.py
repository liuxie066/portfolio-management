from datetime import date
from unittest.mock import Mock

import pytest

from src.portfolio import PortfolioManager


def test_normalize_payload_helpers():
    manager = PortfolioManager(storage=Mock(), price_fetcher=Mock())

    tx = manager._normalize_transaction_payload(quantity=1, price=1.005, fee=0.005)
    assert tx['price'] == 1.01
    assert tx['fee'] == 0.01
    assert tx['amount'] == 1.01

    cf = manager._normalize_cash_flow_payload(amount=1.005, cny_amount=1.005, exchange_rate=7.1234)
    assert cf['amount'] == 1.01
    assert cf['cny_amount'] == 1.01
    assert cf['exchange_rate'] == 7.1234

    holding_cash = manager._normalize_holding_payload(quantity=1.005, cash_like=True)
    holding_stock = manager._normalize_holding_payload(quantity=1.005, cash_like=False)
    assert holding_cash['quantity'] == 1.01
    assert holding_stock['quantity'] == 1.005


def test_deposit_legacy_entry_is_disabled_without_any_write():
    storage = Mock()
    fetcher = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=fetcher)
    storage.add_cash_flow.side_effect = lambda cf: cf
    storage.get_holding.return_value = None
    storage.replace_holding.side_effect = lambda holding: holding

    with pytest.raises(RuntimeError, match="cash_flow_entry_disabled"):
        manager.deposit(
            flow_date=date(2025, 3, 14),
            account='测试账户',
            amount=1.005,
            currency='CNY',
            cny_amount=1.005,
        )
    storage.add_cash_flow.assert_not_called()
    storage.replace_holding.assert_not_called()


def test_foreign_deposit_legacy_entry_is_disabled_before_validation():
    manager = PortfolioManager(storage=Mock(), price_fetcher=Mock())

    with pytest.raises(RuntimeError, match="cash_flow_entry_disabled"):
        manager.deposit(
            flow_date=date(2025, 3, 14),
            account='测试账户',
            amount=1000,
            currency='USD',
        )
