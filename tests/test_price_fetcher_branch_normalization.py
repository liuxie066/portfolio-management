from unittest.mock import Mock

from src.pricing.cache import price_cache_to_payload
from src.pricing.fixed import get_cash_price


def test_cash_price_branch_uses_normalized_output():
    fetch_exchange_rates = Mock(return_value={'USDCNY': 7.1234567})

    result = get_cash_price('USD-CASH', fetch_exchange_rates)

    assert result['price'] == 1.0
    assert result['cny_price'] == 7.12
    assert result['exchange_rate'] == 7.123457
    assert result['source'] == 'fixed'


def test_price_cache_to_payload_is_normalized_on_fetch_path():
    cached = Mock(
        asset_id='AAPL',
        asset_name='Apple',
        price=123.456,
        currency='USD',
        cny_price=888.8888,
        change=1.235,
        change_pct=1.005,
        exchange_rate=7.1234567,
        data_source='cache',
        expires_at=None,
    )

    result = price_cache_to_payload(cached)

    assert result['price'] == 123.46
    assert result['cny_price'] == 888.89
    assert result['change'] == 1.24
    assert result['change_pct'] == 1.01
    assert result['exchange_rate'] == 7.123457


def test_foreign_mmf_uses_same_fx_conversion_as_cash():
    from src.pricing.fixed import get_mmf_price

    fetch_exchange_rates = Mock(return_value={"HKDCNY": 0.9123456})
    result = get_mmf_price("HKD-MMF", fetch_exchange_rates)

    assert result["price"] == 1.0
    assert result["currency"] == "HKD"
    assert result["cny_price"] == 0.91
    assert result["exchange_rate"] == 0.912346
    assert result["market_type"] == "mmf"
