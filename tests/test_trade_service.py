from datetime import date
from unittest.mock import Mock

import pytest

from src.app.trade_service import TradeService
from src.models import AssetType, Holding
from src.portfolio import PortfolioManager


def _manager(storage):
    manager = PortfolioManager(storage=storage, price_fetcher=Mock())
    manager._get_asset_name = Mock(side_effect=lambda asset_id, fallback: fallback)
    return manager


def test_trade_service_buy_records_transaction_and_holding():
    storage = Mock()
    storage.add_transaction.side_effect = lambda tx: tx
    storage.upsert_holding.side_effect = lambda holding: holding
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    tx = service.buy(
        tx_date=date(2025, 3, 14),
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        quantity=1.005,
        price=1.005,
        currency="CNY",
        fee=0.005,
        auto_deduct_cash=False,
    )

    holding = storage.upsert_holding.call_args[0][0]
    assert tx.quantity == 1.005
    assert tx.price == 1.01
    assert tx.amount == 1.02
    assert holding.quantity == 1.005


def test_trade_service_buy_records_compensation_when_cash_deduct_fails():
    storage = Mock()
    storage.add_transaction.side_effect = lambda tx: tx
    storage.upsert_holding.side_effect = lambda holding: holding
    manager = _manager(storage)
    manager.cash_service.has_sufficient_cash = Mock(return_value=True)
    manager.cash_service.deduct_cash = Mock(return_value=False)
    manager._record_compensation = Mock()
    service = TradeService(manager=manager, storage=storage)

    service.buy(
        tx_date=date(2025, 3, 14),
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        quantity=1,
        price=10,
        currency="CNY",
        auto_deduct_cash=True,
    )

    manager._record_compensation.assert_called()
    assert manager._record_compensation.call_args.kwargs["operation_type"] == "BUY_CASH_DEDUCT_FAILED"


def test_trade_service_sell_uses_cash_service_add_cash():
    storage = Mock()
    storage.get_holding.return_value = Holding(
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        quantity=100,
        currency="CNY",
    )
    storage.add_transaction.side_effect = lambda tx: tx
    manager = _manager(storage)
    manager.cash_service.add_cash = Mock()
    service = TradeService(manager=manager, storage=storage)

    tx = service.sell(
        tx_date=date(2025, 3, 14),
        asset_id="000001",
        account="a",
        quantity=1,
        price=10,
        currency="CNY",
        fee=1,
        auto_add_cash=True,
    )

    assert tx.quantity == -1.0
    manager.cash_service.add_cash.assert_called_once_with("a", 9.0)


def test_trade_service_deposit_uses_cash_service_update():
    storage = Mock()
    storage.add_cash_flow.side_effect = lambda cf: cf
    manager = _manager(storage)
    manager.cash_service.update_cash_holding = Mock()
    service = TradeService(manager=manager, storage=storage)

    cf = service.deposit(
        flow_date=date(2025, 3, 14),
        account="a",
        amount=1.005,
        currency="CNY",
        cny_amount=1.005,
    )

    assert cf.amount == 1.01
    manager.cash_service.update_cash_holding.assert_called_once_with("a", 1.01, "CNY", 1.01)


def test_trade_service_replay_skips_buy_side_effects():
    storage = Mock()

    def replay(tx):
        tx.record_id = "tx-existing"
        tx.mark_replayed()
        return tx

    storage.add_transaction.side_effect = replay
    manager = _manager(storage)
    manager.cash_service.has_sufficient_cash = Mock(return_value=True)
    manager.cash_service.deduct_cash = Mock()
    service = TradeService(manager=manager, storage=storage)

    tx = service.buy(
        tx_date=date(2025, 3, 14),
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        quantity=1,
        price=10,
        currency="CNY",
        auto_deduct_cash=True,
        request_id="same-request",
    )

    assert tx.was_replayed is True
    storage.upsert_holding.assert_not_called()
    manager.cash_service.deduct_cash.assert_not_called()


def test_trade_service_replay_skips_deposit_cash_side_effect():
    storage = Mock()

    def replay(cf):
        cf.record_id = "cf-existing"
        cf.mark_replayed()
        return cf

    storage.add_cash_flow.side_effect = replay
    manager = _manager(storage)
    manager.cash_service.update_cash_holding = Mock()
    service = TradeService(manager=manager, storage=storage)

    cf = service.deposit(
        flow_date=date(2025, 3, 14),
        account="a",
        amount=100,
        currency="CNY",
    )

    assert cf.was_replayed is True
    manager.cash_service.update_cash_holding.assert_not_called()


def test_trade_service_rejects_oversell_before_transaction_write():
    storage = Mock()
    storage.get_holding.return_value = Holding(
        record_id="holding-1",
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        quantity=1,
        currency="CNY",
    )
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    import pytest
    with pytest.raises(ValueError, match="持仓不足"):
        service.sell(
            tx_date=date(2025, 3, 14),
            asset_id="000001",
            account="a",
            quantity=2,
            price=10,
            currency="CNY",
        )

    storage.add_transaction.assert_not_called()


def test_trade_service_rejects_broker_mismatch_before_transaction_write():
    storage = Mock()
    storage.get_holding.return_value = None
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

    with pytest.raises(ValueError, match="未找到持仓"):
        service.sell(
            tx_date=date(2025, 3, 14),
            asset_id="000001",
            account="a",
            broker="FUTU",
            quantity=1,
            price=10,
            currency="CNY",
        )

    storage.get_holding.assert_called_once_with("000001", "a", "FUTU")
    storage.add_transaction.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("quantity", 0),
    ("quantity", float("nan")),
    ("price", float("inf")),
    ("fee", -1),
])
def test_trade_service_rejects_invalid_buy_before_any_write(field, value):
    storage = Mock()
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)
    kwargs = {"quantity": 1, "price": 10, "fee": 0}
    kwargs[field] = value

    with pytest.raises(ValueError, match="invalid financial write input"):
        service.buy(
            tx_date=date(2025, 3, 14),
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            account="a",
            currency="CNY",
            **kwargs,
        )

    storage.add_transaction.assert_not_called()
    storage.upsert_holding.assert_not_called()
