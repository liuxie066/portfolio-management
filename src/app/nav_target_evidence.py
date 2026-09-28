"""Admission check for immutable, target-period daily NAV facts."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.domain.snapshot_contracts import NormalizedValuationSnapshot, digest_payload


_MARKET_ZONES = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}


def _instant(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid number") from exc
    if not result.is_finite():
        raise ValueError("nonfinite value")
    return result


def missing_target_period_evidence(
    *,
    account: str,
    nav_date: date,
    normalized_valuation: NormalizedValuationSnapshot,
    artifact: Mapping[str, Any] | None,
    calendar_info: Mapping[str, Any] | None,
    calendar: Any = None,
) -> list[str]:
    """Return missing or conflicting fact names; an empty list admits final NAV."""
    bundle = (artifact or {}).get("target_period_evidence")
    if not isinstance(bundle, dict):
        return ["target_period_evidence"]
    missing: list[str] = []
    body = {key: value for key, value in bundle.items() if key != "bundle_digest"}
    if bundle.get("bundle_digest") != digest_payload(body):
        missing.append("bundle_digest")
    if bundle.get("account") != account or bundle.get("nav_date") != nav_date.isoformat():
        missing.append("account_nav_date")
    if not str(bundle.get("source_id") or "").strip():
        missing.append("source_id")

    try:
        cutoff = _instant(bundle.get("cutoff_at"))
        sessions = bundle["markets"]
        calendar_markets = (calendar_info or {}).get("markets")
        closes = []
        for market, zone in _MARKET_ZONES.items():
            item = sessions[market]
            if not isinstance(item.get("open"), bool):
                raise ValueError("unknown market session")
            if item["open"] is not calendar_markets[market]:
                raise ValueError("market session conflicts with calendar")
            if item.get("local_date") != nav_date.isoformat():
                raise ValueError("market local date mismatch")
            if item["open"]:
                close = _instant(item.get("close_at"))
                if close.astimezone(ZoneInfo(zone)).date() != nav_date:
                    raise ValueError("market close date mismatch")
                closes.append(close)
        if not closes or cutoff != max(closes):
            raise ValueError("target cutoff mismatch")
    except (KeyError, TypeError, ValueError, AttributeError):
        missing.append("market_cutoff")
        cutoff = None

    valuation = normalized_valuation.canonical_payload()
    holdings = bundle.get("holdings")
    try:
        if not isinstance(holdings, dict) or cutoff is None:
            raise ValueError("holdings or cutoff absent")
        expected_digest = (valuation.get("holdings_provenance") or {}).get("normalized_holdings_digest")
        if not expected_digest or holdings.get("digest") != expected_digest or holdings.get("digest") != (artifact or {}).get("holdings_digest"):
            raise ValueError("holdings digest mismatch")
        if not str(holdings.get("receipt_id") or "").strip():
            raise ValueError("holdings receipt missing")
        if _instant(holdings.get("effective_as_of")) > cutoff or _instant(holdings.get("complete_through")) < cutoff:
            raise ValueError("holdings do not cover cutoff")
        if holdings.get("latest_event_at") and _instant(holdings["latest_event_at"]) > cutoff:
            raise ValueError("later holdings event included")
    except (TypeError, ValueError):
        missing.append("holdings_as_of")

    rows = []
    try:
        rows = [row for row in valuation["rows"] if _number(row["quantity"]) != 0]
        if any(_number(item["value_cny"]) != 0 for item in valuation.get("components") or []):
            raise ValueError("unproved valuation component")
        facts = bundle["price_facts"]
        keys = [(fact["asset_id"], fact["broker"]) for fact in facts]
        row_keys = [(row["asset_id"], row["broker"]) for row in rows]
        if sorted(keys) != sorted(row_keys) or len(keys) != len(set(keys)):
            raise ValueError("price fact set mismatch")
        for row in rows:
            fact = next(f for f in facts if (f["asset_id"], f["broker"]) == (row["asset_id"], row["broker"]))
            if fact.get("currency") != row["currency"] or _number(fact.get("value")) != _number(row["price"]):
                raise ValueError("price fact value mismatch")
            quote = (valuation.get("price_evidence") or {}).get(row["asset_id"])
            if quote and (
                _number(quote.get("price")) != _number(row["price"])
                or _number(quote.get("cny_price")) != _number(row["cny_price"])
                or quote.get("currency") != row["currency"]
            ):
                raise ValueError("valuation quote conflicts with row")
            if not str(fact.get("source") or "").strip():
                raise ValueError("price source missing")
            market_by_type = {"a_stock": "CN", "exchange_fund": "CN", "hk_stock": "HK", "us_stock": "US"}
            asset_type = row["asset_type"]
            if asset_type in market_by_type or asset_type in {"fund", "otc_fund", "cn_fund"}:
                if not isinstance(quote, Mapping) or quote.get("fact_date") != fact.get("fact_date") or quote.get("source") != fact.get("source"):
                    raise ValueError("price fact conflicts with saved quote")
            if asset_type in market_by_type:
                if fact.get("kind") != "market" or fact.get("market") != market_by_type[asset_type]:
                    raise ValueError("price market mismatch")
                market = fact["market"]
                published = _instant(fact.get("published_at"))
                if bundle["markets"][market]["open"]:
                    if fact.get("fact_date") != nav_date.isoformat():
                        raise ValueError("price fact date mismatch")
                    close = _instant(bundle["markets"][market]["close_at"])
                else:
                    fact_date = date.fromisoformat(str(fact.get("fact_date")))
                    if calendar is None or not (nav_date - timedelta(days=31) <= fact_date < nav_date):
                        raise ValueError("prior market session unavailable")
                    close = _instant(fact.get("market_close_at"))
                    if close.astimezone(ZoneInfo(_MARKET_ZONES[market])).date() != fact_date:
                        raise ValueError("prior market close date mismatch")
                    candidate = fact_date
                    while candidate < nav_date:
                        decision = calendar.explain(candidate)
                        if candidate == fact_date and (decision.get("markets") or {}).get(market) is not True:
                            raise ValueError("prior session not confirmed open")
                        if candidate > fact_date and candidate.weekday() < 5 and (decision.get("markets") or {}).get(market) is not False:
                            raise ValueError("intervening session not confirmed closed")
                        candidate += timedelta(days=1)
                captured_at = _instant((artifact or {}).get("captured_at"))
                if published < close or published > captured_at:
                    raise ValueError("market close price not captured")
            elif asset_type in {"cash", "mmf", "crypto"}:
                if fact.get("kind") != "continuous":
                    raise ValueError("continuous price kind mismatch")
                if cutoff is None or not str(fact.get("policy") or "").strip():
                    raise ValueError("continuous price policy missing")
                if _instant(fact.get("effective_at")) > cutoff or _instant(fact.get("valid_through")) < cutoff:
                    raise ValueError("continuous price does not cover cutoff")
            elif asset_type in {"fund", "otc_fund", "cn_fund"}:
                if fact.get("kind") != "fund" or cutoff is None:
                    raise ValueError("fund price kind mismatch")
                if date.fromisoformat(str(fact.get("fact_date"))) > nav_date or _instant(fact.get("published_at")) > cutoff:
                    raise ValueError("fund fact not known by cutoff")
            else:
                raise ValueError("unsupported price kind")
            if row["currency"] == "CNY" and _number(row["price"]) != _number(row["cny_price"]):
                raise ValueError("CNY conversion mismatch")
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError, StopIteration):
        missing.append("price_facts")

    try:
        fx = bundle["fx_facts"]
        needed = {row["currency"] for row in rows if row["currency"] != "CNY"}
        if {fact["currency"] for fact in fx} != needed or len(fx) != len(needed):
            raise ValueError("FX fact set mismatch")
        for fact in fx:
            if cutoff is None or not str(fact.get("source") or "").strip() or not str(fact.get("policy") or "").strip():
                raise ValueError("FX source or cutoff missing")
            if _instant(fact.get("effective_at")) > cutoff or _instant(fact.get("valid_through")) < cutoff:
                raise ValueError("FX does not cover cutoff")
            rate = _number(fact["rate"])
            if rate <= 0:
                raise ValueError("FX rate invalid")
            for row in rows:
                if row["currency"] == fact["currency"] and abs(_number(row["price"]) * rate - _number(row["cny_price"])) > Decimal("0.0001"):
                    raise ValueError("FX conversion mismatch")
    except (KeyError, TypeError, ValueError):
        missing.append("fx_facts")
    return missing
