from datetime import date
from unittest.mock import Mock

from src.app.trade_service import TradeService
from src.portfolio import PortfolioManager


def _manager(storage):
    return PortfolioManager(storage=storage, price_fetcher=Mock())


def test_manual_trade_entrypoints_are_retired():
    assert not hasattr(TradeService, "buy")
    assert not hasattr(TradeService, "sell")
    assert not hasattr(PortfolioManager, "buy")
    assert not hasattr(PortfolioManager, "sell")


def test_trade_service_deposit_applies_absolute_cash_target():
    storage = Mock()
    storage.get_holding.return_value = None
    storage.add_cash_flow.side_effect = lambda cf: cf
    storage.replace_holding.side_effect = lambda holding: holding
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    cf = service.deposit(
        flow_date=date(2025, 3, 14),
        account="a",
        amount=1.005,
        currency="CNY",
        cny_amount=1.005,
    )

    assert cf.amount == 1.01
    target = storage.replace_holding.call_args[0][0]
    assert target.asset_id == "CNY-CASH"
    assert target.quantity == 1.01


def test_trade_service_replay_skips_deposit_cash_side_effect():
    storage = Mock()
    storage.get_holding.return_value = None

    def replay(cf):
        cf.record_id = "cf-existing"
        cf.mark_replayed()
        return cf

    storage.add_cash_flow.side_effect = replay
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    cf = service.deposit(
        flow_date=date(2025, 3, 14),
        account="a",
        amount=100,
        currency="CNY",
    )

    assert cf.was_replayed is True
    storage.replace_holding.assert_not_called()
