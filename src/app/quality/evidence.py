from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _requires_market_quote(holding: Any) -> bool:
    asset_type = _value(holding, "asset_type", "")
    asset_type = getattr(asset_type, "value", asset_type)
    currency = str(_value(holding, "currency", "") or "").upper()
    return not (str(asset_type).upper() in {"CASH", "MMF"} and currency == "CNY")


def valuation_quality_evidence(valuation: Any) -> dict[str, Any]:
    quotes = []
    raw_evidence = dict(getattr(valuation, "price_evidence", None) or {})
    for code, payload in sorted(raw_evidence.items()):
        if not isinstance(payload, dict):
            continue
        currency = str(payload.get("currency") or "").upper()
        observed_at = payload.get("fetched_at") or payload.get("observed_at")
        quotes.append({
            "code": str(code),
            "currency": currency,
            "source": payload.get("source") or payload.get("data_source"),
            "observed_at_utc": observed_at,
            "price_present": payload.get("price") is not None,
            "cny_price_present": payload.get("cny_price") is not None,
            "fx_present": currency == "CNY" or payload.get("exchange_rate") is not None,
            "is_stale": bool(payload.get("is_stale")) or payload.get("source") == "cache_fallback",
            "is_fallback": bool(payload.get("is_from_cache")) or payload.get("source") == "cache_fallback",
        })
    priced_codes = {item["code"] for item in quotes if item["price_present"] and item["cny_price_present"]}
    required_codes = {
        str(_value(item, "asset_id"))
        for item in (getattr(valuation, "holdings", None) or [])
        if _value(item, "quantity", 0) and _requires_market_quote(item)
    }
    missing_prices = sorted(required_codes - priced_codes)
    stale_codes = sorted(item["code"] for item in quotes if item["is_stale"])
    missing_fx = sorted(
        item["code"]
        for item in quotes
        if item["currency"] != "CNY" and not item["fx_present"]
    )
    missing_fx_fact_time = sorted(
        item["code"]
        for item in quotes
        if item["currency"] != "CNY"
        and item["fx_present"]
        and not item["observed_at_utc"]
    )
    observed_times = sorted(
        str(item["observed_at_utc"])
        for item in quotes
        if item.get("observed_at_utc")
    )
    return {
        "schema_version": "pm.valuation_quality.v1",
        "observed_at_utc": observed_times[-1] if observed_times else datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prices": {
            "status": "trusted" if not missing_prices and not stale_codes else "partial",
            "quote_count": len(quotes),
            "missing_codes": missing_prices,
            "stale_codes": stale_codes,
            "quotes": quotes,
        },
        "fx": {
            "status": "trusted" if not missing_fx and not missing_fx_fact_time else "unavailable",
            "missing_codes": missing_fx,
            "missing_fact_time_codes": missing_fx_fact_time,
            "fact_times": sorted(set(observed_times)),
        },
    }
