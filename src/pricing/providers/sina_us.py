"""Sina US stock quote helpers shared by US single and batch providers.

Replaces the former Yahoo Chart path: Yahoo blocks the deployment host
(HTTP 403), while hq.sinajs.cn is reachable and supports batch queries.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from ..payload import normalize_price_payload, remaining_timeout

SINA_US_HEADERS = {"Referer": "https://finance.sina.com.cn"}
SINA_US_URL = "https://hq.sinajs.cn/list="

# Field indices inside the hq_str_gb_<code> payload, verified against live
# responses (FUTU/BABA/GOOGL/PDD/TCOM/TIGR/SPY, 2026-07-22).
_IDX_NAME = 0
_IDX_PRICE = 1
_IDX_CHANGE_PCT = 2
_IDX_CHANGE = 4
_IDX_OPEN = 5
_IDX_HIGH = 6
_IDX_LOW = 7
_IDX_VOLUME = 10
_IDX_PREV_CLOSE = 26
_MIN_FIELDS = 27


def _float_or_none(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sina_query_code(code: str) -> str:
    """Map a US ticker to the Sina query symbol (class-share dots become $)."""
    return code.lower().replace(".", "$")


def parse_sina_us_quotes(
    text: str,
    codes: list[str],
    *,
    usd_cny: float | None = None,
    rate_lookup: Callable[[], dict[str, float]] | None = None,
) -> dict[str, dict]:
    """Parse hq.sinajs.cn response text into normalized quote payloads.

    Unmatched codes (empty quote strings) are omitted. The FX lookup is only
    invoked when at least one usable quote was found.
    """
    wanted = {_sina_query_code(code): code for code in codes}
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("var hq_str_gb_"):
            continue
        var, _, value = line.partition("=")
        query = var.strip()[len("var hq_str_gb_"):]
        original = wanted.get(query)
        if original is None:
            continue
        fields = value.strip().rstrip(";").strip('"').split(",")
        if len(fields) < _MIN_FIELDS or not fields[_IDX_PRICE]:
            continue
        found[original] = fields

    if not found:
        return {}

    if usd_cny is None:
        if rate_lookup is None:
            raise ValueError("usd_cny or rate_lookup is required")
        usd_cny = rate_lookup()["USDCNY"]

    results: dict[str, dict] = {}
    for code, fields in found.items():
        price = _float_or_none(fields[_IDX_PRICE])
        if price is None or price <= 0:
            continue
        prev_close = _float_or_none(fields[_IDX_PREV_CLOSE]) or price
        change = _float_or_none(fields[_IDX_CHANGE])
        if change is None:
            change = price - prev_close
        change_pct = _float_or_none(fields[_IDX_CHANGE_PCT])
        if change_pct is None:
            change_pct = (change / prev_close * 100) if prev_close else 0
        volume = _float_or_none(fields[_IDX_VOLUME]) or 0
        results[code] = normalize_price_payload(
            {
                "code": code,
                "name": fields[_IDX_NAME] or code,
                "price": price,
                "prev_close": prev_close,
                "open": _float_or_none(fields[_IDX_OPEN]) or price,
                "high": _float_or_none(fields[_IDX_HIGH]) or price,
                "low": _float_or_none(fields[_IDX_LOW]) or price,
                "change": change,
                "change_pct": change_pct,
                "volume": int(volume),
                "currency": "USD",
                "cny_price": price * usd_cny,
                "exchange_rate": usd_cny,
                "market_type": "us",
                "source": "sina_us",
            }
        )
    return results


def fetch_sina_us_quotes(
    fetcher,
    codes: list[str],
    *,
    timeout: float = 8,
    deadline: float | None = None,
) -> dict[str, dict]:
    """Fetch Sina US quotes for all codes in one request and normalize them."""
    if not codes:
        return {}
    query = ",".join("gb_" + _sina_query_code(code) for code in codes)
    response = fetcher.session.get(
        SINA_US_URL + query,
        headers=SINA_US_HEADERS,
        timeout=remaining_timeout(deadline, timeout),
    )
    response.raise_for_status()
    rate_lookup = (
        fetcher._fetch_exchange_rates
        if deadline is None
        else lambda: fetcher._fetch_exchange_rates(deadline=deadline)
    )
    text = response.content.decode("gbk", errors="replace")
    return parse_sina_us_quotes(text, codes, rate_lookup=rate_lookup)
