"""Yahoo Chart quote helpers shared by US single and batch providers."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from ..payload import normalize_price_payload


YAHOO_CHART_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def parse_yahoo_chart_payload(
    data: dict[str, Any],
    *,
    code: str,
    usd_cny: float | None = None,
    rate_lookup: Callable[[], dict[str, float]] | None = None,
) -> Optional[dict]:
    """Convert a Yahoo Chart JSON response into the normalized quote payload."""
    chart = data.get("chart", {})
    if chart.get("error"):
        raise Exception(chart["error"].get("description", "Unknown error"))

    result = chart.get("result", [{}])[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp", [])
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    if not timestamps or not quotes.get("close"):
        return None

    closes = quotes["close"]
    opens = quotes.get("open", [])
    highs = quotes.get("high", [])
    lows = quotes.get("low", [])
    volumes = quotes.get("volume", [])
    valid_closes = [c for c in closes if c is not None]
    if not valid_closes:
        return None

    current = valid_closes[-1]
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    if prev_close is None and len(valid_closes) >= 2:
        prev_close = valid_closes[-2]
    elif prev_close is None and opens:
        prev_close = opens[0]
    elif prev_close is None:
        prev_close = current

    valid_opens = [o for o in opens if o is not None]
    valid_highs = [h for h in highs if h is not None]
    valid_lows = [l for l in lows if l is not None]
    valid_volumes = [v for v in volumes if v is not None]
    change = current - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0
    if usd_cny is None:
        if rate_lookup is None:
            raise ValueError("usd_cny or rate_lookup is required")
        usd_cny = rate_lookup()["USDCNY"]

    return normalize_price_payload(
        {
            "code": code,
            "name": meta.get("shortName") or meta.get("longName") or meta.get("symbol") or code,
            "price": current,
            "prev_close": prev_close,
            "open": valid_opens[-1] if valid_opens else current,
            "high": valid_highs[-1] if valid_highs else current,
            "low": valid_lows[-1] if valid_lows else current,
            "change": change,
            "change_pct": change_pct,
            "volume": int(valid_volumes[-1]) if valid_volumes else 0,
            "currency": meta.get("currency", "USD"),
            "cny_price": current * usd_cny,
            "exchange_rate": usd_cny,
            "market_type": "us",
            "source": "yahoo_chart",
        }
    )


def fetch_yahoo_chart_quote(fetcher: Any, quote_code: str, *, code: str | None = None, timeout: int = 15) -> Optional[dict]:
    """Fetch and normalize a Yahoo Chart quote."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_code}?interval=1d&range=2d"
    response = fetcher.session.get(url, headers=YAHOO_CHART_HEADERS, timeout=timeout)
    if response.status_code == 429:
        raise Exception("Rate limited")
    response.raise_for_status()
    return parse_yahoo_chart_payload(
        response.json(),
        code=code or quote_code,
        rate_lookup=fetcher._fetch_exchange_rates,
    )
