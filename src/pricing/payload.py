"""Shared price payload normalization helpers."""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping

from src.time_utils import bj_now_naive

MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.000001")
PCT_QUANT = Decimal("0.01")


def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def positive_finite_decimal(value, field: str) -> Decimal:
    """Return a positive finite Decimal or reject the quote boundary."""
    try:
        result = to_decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive finite number") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return result


def remaining_timeout(deadline: float | None, default: float) -> float:
    """Bound one blocking operation by the remaining monotonic deadline."""
    if deadline is None:
        return float(default)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("pricing deadline exceeded")
    return max(0.001, min(float(default), remaining))


def sleep_with_deadline(delay: float, deadline: float | None) -> None:
    """Sleep only when the requested backoff fits inside the deadline."""
    if delay <= 0:
        return
    if deadline is not None and delay >= deadline - time.monotonic():
        raise TimeoutError("pricing deadline exceeded during retry backoff")
    time.sleep(delay)


def quantize_money(value) -> float:
    return float(to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))


def quantize_rate(value) -> float:
    return float(to_decimal(value).quantize(RATE_QUANT, rounding=ROUND_HALF_UP))


def quantize_pct(value) -> float:
    return float(to_decimal(value).quantize(PCT_QUANT, rounding=ROUND_HALF_UP))


def normalize_price_payload(payload: Mapping) -> dict:
    """Normalize and validate provider price payloads."""
    result = dict(payload)
    if not result.get("is_from_cache"):
        result.setdefault("fetched_at", bj_now_naive().isoformat())

    currency = str(result.get("currency") or "CNY").strip().upper()
    if result.get("price") is None:
        raise ValueError("price must be a positive finite number")
    positive_finite_decimal(result["price"], "price")
    if currency != "CNY" and result.get("price") is not None and result.get("cny_price") is None:
        raise ValueError("foreign-currency quote requires cny_price")
    for key in ("cny_price", "exchange_rate"):
        if result.get(key) is not None:
            positive_finite_decimal(result[key], key)

    for key in ("price", "prev_close", "open", "high", "low", "change", "cny_price"):
        if key in result and result[key] is not None:
            result[key] = quantize_money(result[key])
    if "change_pct" in result and result["change_pct"] is not None:
        result["change_pct"] = quantize_pct(result["change_pct"] )
    if "exchange_rate" in result and result["exchange_rate"] is not None:
        result["exchange_rate"] = quantize_rate(result["exchange_rate"] )

    for key in ("price", "cny_price", "exchange_rate"):
        if result.get(key) is not None:
            positive_finite_decimal(result[key], key)
    return result
