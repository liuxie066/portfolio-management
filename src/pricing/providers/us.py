"""US stock quote provider."""
from __future__ import annotations

import time
from typing import Optional

from src import config as _config

from ..payload import normalize_price_payload
from ..types import PriceRequest, ProviderResult
from .yahoo_chart import fetch_yahoo_chart_quote


class USStockProvider:
    name = "us-stock"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def supports(self, request: PriceRequest) -> bool:
        return True

    def fetch_one(self, request: PriceRequest) -> ProviderResult:
        started = time.time()
        code = request.normalized_code or request.code
        try:
            return ProviderResult(self.fetch_us_stock(code), self.name, latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            return ProviderResult(None, self.name, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000))

    def fetch_us_stock(self, code: str) -> Optional[dict]:
        quote_code = code.replace(".", "-")
        errors = []

        finnhub_key = _config.get("finnhub_api_key")
        if finnhub_key:
            try:
                result = self.fetch_finnhub(quote_code, finnhub_key)
                if result:
                    return result
            except Exception as exc:
                errors.append(f"Finnhub: {exc}")

        try:
            result = self.fetcher._retry_with_backoff(
                lambda: self.fetch_yahoo_chart(quote_code),
                max_retries=2,
                base_delay=1.0,
            )
            if result:
                return result
        except Exception as exc:
            errors.append(f"Yahoo Chart: {exc}")

        print(f"获取美股价格失败 {code}: {'; '.join(errors)}")
        return None

    def fetch_finnhub(self, code: str, api_key: str) -> Optional[dict]:
        response = self.fetcher.session.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": code, "token": api_key},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        current = data.get("c")
        prev_close = data.get("pc")
        if not current:
            return None

        change = data.get("d", current - prev_close if prev_close else 0)
        change_pct = data.get("dp", (change / prev_close * 100) if prev_close else 0)
        usd_cny = self.fetcher._fetch_exchange_rates()["USDCNY"]

        return normalize_price_payload(
            {
                "code": code,
                "name": code,
                "price": current,
                "prev_close": prev_close if prev_close else current,
                "open": data.get("o", current),
                "high": data.get("h", current),
                "low": data.get("l", current),
                "change": change,
                "change_pct": change_pct,
                "currency": "USD",
                "cny_price": current * usd_cny,
                "exchange_rate": usd_cny,
                "market_type": "us",
                "source": "finnhub",
            }
        )

    def fetch_yahoo_chart(self, code: str) -> Optional[dict]:
        return fetch_yahoo_chart_quote(self.fetcher, code, timeout=15)
