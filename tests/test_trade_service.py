from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.app.compensation_service import PartialWriteError
from src.app.trade_service import TradeService
from src.models import AssetType, Holding
from src.portfolio import PortfolioManager


def _manager(storage):
    manager = PortfolioManager(storage=storage, price_fetcher=Mock())
    manager._get_asset_name = Mock(side_effect=lambda asset_id, fallback: fallback)
    return manager


def _holding(asset_id="000001", quantity=100, *, asset_type=AssetType.A_STOCK, broker=""):
    return Holding(
        record_id=f"holding-{asset_id}",
        asset_id=asset_id,
        asset_name=asset_id,
        asset_type=asset_type,
        account="a",
        broker=broker,
        quantity=quantity,
        currency="CNY",
    )


def test_trade_service_buy_records_transaction_and_absolute_holding_target():
    storage = Mock()
    storage.get_holding.return_value = None
    storage.add_transaction.side_effect = lambda tx: tx
    storage.replace_holding.side_effect = lambda holding: holding
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

    target = storage.replace_holding.call_args[0][0]
    assert tx.quantity == 1.005
    assert tx.price == 1.01
    assert tx.amount == 1.02
    assert target.quantity == 1.005


def test_trade_service_buy_raises_partial_and_records_complete_targets_on_cash_failure():
    stock = None
    cash = _holding("CNY-CASH", 100, asset_type=AssetType.CASH)
    mmf = _holding("CNY-MMF", 0, asset_type=AssetType.MMF)
    storage = Mock()
    storage.get_holding.side_effect = lambda asset_id, account, broker=None: {
        "000001": stock,
        "CNY-CASH": cash,
        "CNY-MMF": mmf,
    }.get(asset_id)
    storage.add_transaction.side_effect = lambda tx: tx

    def replace(holding):
        if holding.asset_id == "CNY-CASH":
            raise RuntimeError("cash write failed")
        return holding

    storage.replace_holding.side_effect = replace
    manager = _manager(storage)
    manager._record_compensation = Mock(return_value=SimpleNamespace(task_id="repair-1"))
    service = TradeService(manager=manager, storage=storage)

    with pytest.raises(PartialWriteError) as captured:
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

    error = captured.value
    assert error.compensation_persisted is True
    assert error.task_id == "repair-1"
    assert error.completed_steps == ["transaction_created", "target[0]/HOLDING_TARGET_SET"]
    payload = manager._record_compensation.call_args.kwargs["payload"]
    assert [target["type"] for target in payload["targets"]] == ["HOLDING_TARGET_SET", "CASH_TARGET_SET"]


def test_trade_service_reports_compensation_durability_failure():
    storage = Mock()
    storage.get_holding.return_value = None
    storage.add_transaction.side_effect = lambda tx: tx
    storage.replace_holding.side_effect = RuntimeError("holding write failed")
    manager = _manager(storage)
    manager._record_compensation = Mock(side_effect=OSError("disk full"))
    service = TradeService(manager=manager, storage=storage)

    with pytest.raises(PartialWriteError) as captured:
        service.buy(
            tx_date=date(2025, 3, 14),
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            account="a",
            quantity=1,
            price=10,
            currency="CNY",
            auto_deduct_cash=False,
        )

    assert captured.value.compensation_persisted is False
    assert captured.value.task_id is None
    assert "disk full" in captured.value.original_error


def test_trade_service_sell_applies_holding_then_cash_targets():
    stock = _holding(quantity=100)
    storage = Mock()
    storage.get_holding.side_effect = lambda asset_id, account, broker=None: stock if asset_id == "000001" else None
    storage.add_transaction.side_effect = lambda tx: tx
    storage.replace_holding.side_effect = lambda holding: holding
    manager = _manager(storage)
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
    targets = [call.args[0] for call in storage.replace_holding.call_args_list]
    assert [(target.asset_id, target.quantity) for target in targets] == [("000001", 99.0), ("CNY-CASH", 9.0)]


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


def test_trade_service_replay_skips_buy_side_effects():
    storage = Mock()
    storage.get_holding.return_value = None

    def replay(tx):
        tx.record_id = "tx-existing"
        tx.mark_replayed()
        return tx

    storage.add_transaction.side_effect = replay
    manager = _manager(storage)
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
        auto_deduct_cash=False,
        request_id="same-request",
    )

    assert tx.was_replayed is True
    storage.replace_holding.assert_not_called()


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


def test_trade_service_rejects_oversell_before_transaction_write():
    storage = Mock()
    storage.get_holding.return_value = _holding(quantity=1)
    manager = _manager(storage)
    service = TradeService(manager=manager, storage=storage)

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
    storage.replace_holding.assert_not_called()
