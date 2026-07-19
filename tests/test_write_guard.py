import math

import pytest

from src.write_guard import (
    validate_and_normalize_cash_flow_input,
    validate_and_normalize_trade_input,
)


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), float("-inf")])
def test_trade_guard_rejects_non_positive_or_non_finite_quantity(value):
    result = validate_and_normalize_trade_input(tx_type="BUY", quantity=value, price=1, fee=0)
    assert result["ok"] is False


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), float("-inf")])
def test_trade_guard_rejects_non_positive_or_non_finite_price(value):
    result = validate_and_normalize_trade_input(tx_type="BUY", quantity=1, price=value, fee=0)
    assert result["ok"] is False


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), float("-inf")])
def test_trade_guard_rejects_negative_or_non_finite_fee(value):
    result = validate_and_normalize_trade_input(tx_type="BUY", quantity=1, price=1, fee=value)
    assert result["ok"] is False


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), float("-inf")])
def test_cash_flow_guard_rejects_non_positive_or_non_finite_amount(value):
    result = validate_and_normalize_cash_flow_input(amount=value)
    assert result["ok"] is False


def test_cash_flow_guard_accepts_positive_finite_optional_fx_values():
    result = validate_and_normalize_cash_flow_input(amount=1.25, cny_amount=9.0, exchange_rate=7.2)
    assert result == {
        "ok": True,
        "errors": [],
        "normalized": {"amount": 1.25, "cny_amount": 9.0, "exchange_rate": 7.2},
    }
