"""Fixed-price quote helpers for cash-like assets."""
from __future__ import annotations

from typing import Callable, Dict

from .payload import normalize_price_payload


def _currency_from_code(code: str) -> str:
    return code.split("-")[0] if "-" in code else "CNY"


def is_crypto_value_code(code: str) -> bool:
    """Return whether code represents a crypto basket already valued in currency."""
    parts = (code or "").upper().split("-")
    return len(parts) >= 3 and parts[-2] == "CRYPTO" and parts[-1] in {"CNY", "USD", "HKD"}


def _rate_for_currency(currency: str, rates: Dict[str, float]) -> float:
    if currency == "CNY":
        return 1.0

    rate_key = f"{currency}CNY"
    exchange_rate = rates.get(rate_key)
    if exchange_rate is None and currency == "USD":
        exchange_rate = rates.get("USDCNY")
    if exchange_rate is None and currency == "HKD":
        exchange_rate = rates.get("HKDCNY")
    if exchange_rate is None:
        raise KeyError(f"rates missing {rate_key}")
    return exchange_rate


def get_cash_price_with_rates(code: str, rates: Dict[str, float]) -> Dict:
    """Build a fixed cash quote using already-fetched FX rates."""
    currency = _currency_from_code(code)
    exchange_rate = _rate_for_currency(currency, rates)

    return normalize_price_payload(
        {
            "code": code,
            "name": f"{currency}现金",
            "price": 1.0,
            "currency": currency,
            "cny_price": exchange_rate,
            "exchange_rate": exchange_rate,
            "market_type": "cash",
            "source": "fixed",
        }
    )


def get_cash_price(code: str, fetch_exchange_rates: Callable[[], Dict[str, float]] | None = None) -> Dict:
    """Build a fixed cash quote, fetching FX rates only for non-CNY cash."""
    currency = _currency_from_code(code)
    if currency == "CNY":
        rates = {"CNYCNY": 1.0}
    else:
        if fetch_exchange_rates is None:
            raise KeyError(f"rates missing {currency}CNY")
        rates = fetch_exchange_rates()

    return get_cash_price_with_rates(code, rates)


def get_mmf_price_with_rates(code: str, rates: Dict[str, float]) -> Dict:
    """Build a fixed money-market-fund quote using validated FX rates."""
    payload = get_cash_price_with_rates(code, rates)
    payload.update(
        {
            "name": f"{payload['currency']}货币基金",
            "market_type": "mmf",
        }
    )
    return normalize_price_payload(payload)


def get_mmf_price(
    code: str,
    fetch_exchange_rates: Callable[[], Dict[str, float]] | None = None,
) -> Dict:
    """Build a fixed MMF quote, fetching FX only for non-CNY currencies."""
    currency = _currency_from_code(code)
    if currency == "CNY":
        rates = {"CNYCNY": 1.0}
    else:
        if fetch_exchange_rates is None:
            raise KeyError(f"rates missing {currency}CNY")
        rates = fetch_exchange_rates()
    return get_mmf_price_with_rates(code, rates)


def get_crypto_value_price_with_rates(code: str, rates: Dict[str, float]) -> Dict:
    """Build a fixed quote for a crypto basket whose quantity is currency value."""
    currency = code.upper().split("-")[-1]
    exchange_rate = _rate_for_currency(currency, rates)
    return normalize_price_payload(
        {
            "code": code,
            "name": f"{currency}计价虚拟币",
            "price": 1.0,
            "currency": currency,
            "cny_price": exchange_rate,
            "exchange_rate": exchange_rate,
            "market_type": "crypto",
            "source": "fixed",
        }
    )


def get_crypto_value_price(
    code: str,
    fetch_exchange_rates: Callable[[], Dict[str, float]] | None = None,
) -> Dict:
    """Build a fixed quote for an already currency-valued crypto basket."""
    currency = code.upper().split("-")[-1]
    if currency == "CNY":
        rates = {"CNYCNY": 1.0}
    else:
        if fetch_exchange_rates is None:
            raise KeyError(f"rates missing {currency}CNY")
        rates = fetch_exchange_rates()
    return get_crypto_value_price_with_rates(code, rates)
