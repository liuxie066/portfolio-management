from datetime import date
from unittest.mock import Mock

import pytest

from src.app.trade_service import TradeService
from src.portfolio import PortfolioManager


def _manager(storage):
    return PortfolioManager(storage=storage, price_fetcher=Mock())


def test_manual_trade_entrypoints_are_retired():
    assert not hasattr(TradeService, "buy")
    assert not hasattr(TradeService, "sell")
    assert not hasattr(PortfolioManager, "buy")
    assert not hasattr(PortfolioManager, "sell")


def test_trade_service_deposit_is_stably_disabled():
    storage = Mock()
    storage.get_holding.return_value = None
    storage.add_cash_flow.side_effect = lambda cf: cf
    storage.replace_holding.side_effect = lambda holding: holding
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    with pytest.raises(RuntimeError, match="cash_flow_entry_disabled"):
        service.deposit(
            flow_date=date(2025, 3, 14),
            account="a",
            amount=1.005,
            currency="CNY",
            cny_amount=1.005,
        )
    storage.add_cash_flow.assert_not_called()
    storage.replace_holding.assert_not_called()


def test_trade_service_disabled_entry_never_reaches_replay_logic():
    storage = Mock()
    storage.get_holding.return_value = None

    def replay(cf):
        cf.record_id = "cf-existing"
        cf.mark_replayed()
        return cf

    storage.add_cash_flow.side_effect = replay
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    with pytest.raises(RuntimeError, match="cash_flow_entry_disabled"):
        service.deposit(
            flow_date=date(2025, 3, 14),
            account="a",
            amount=100,
            currency="CNY",
        )
    storage.add_cash_flow.assert_not_called()
    storage.replace_holding.assert_not_called()
