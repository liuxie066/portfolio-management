"""US stock quote provider."""
from __future__ import annotations

import time
from typing import Optional

from src import config as _config

from ..payload import normalize_price_payload, remaining_timeout
from ..types import PriceRequest, ProviderResult
from .sina_us import fetch_sina_us_quotes


SINA_REQUEST_BUDGET_SEC = 5.0
FINNHUB_REQUEST_BUDGET_SEC = 4.0


class USStockProvider:
    name = "us-stock"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def supports(self, request: PriceRequest) -> bool:
        return True

    def fetch_one(self, request: PriceRequest) -> ProviderResult:
        started = time.time()
        code = request.normalized_code or request.code
        deadline = (request.hints or {}).get("_deadline")
        try:
            payload = self.fetch_us_stock(code, deadline=deadline) if deadline is not None else self.fetch_us_stock(code)
            return ProviderResult(payload, self.name, latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            return ProviderResult(
                None,
                self.name,
                f"error_type={type(exc).__name__}",
                int((time.time() - started) * 1000),
            )

    def fetch_us_stock(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        quote_code = code.replace(".", "-")
        errors = []

        sina_deadline = time.monotonic() + SINA_REQUEST_BUDGET_SEC
        if deadline is not None:
            sina_deadline = min(sina_deadline, deadline)
        try:
            result = self.fetcher._retry_with_backoff(
                lambda: self.fetch_sina(code, deadline=sina_deadline),
                max_retries=1,
                base_delay=1.0,
                deadline=sina_deadline,
            )
            if result:
                return result
        except Exception as exc:
            errors.append(
                f"provider=sina_us symbol={code} "
                f"error_type={type(exc).__name__}"
            )

        finnhub_key = _config.get("finnhub_api_key")
        if finnhub_key:
            finnhub_deadline = time.monotonic() + FINNHUB_REQUEST_BUDGET_SEC
            if deadline is not None:
                finnhub_deadline = min(finnhub_deadline, deadline)
            try:
                result = self.fetch_finnhub(
                    quote_code,
                    finnhub_key,
                    deadline=finnhub_deadline,
                )
                if result:
                    return result
            except Exception as exc:
                errors.append(
                    f"provider=finnhub symbol={code} "
                    f"error_type={type(exc).__name__}"
                )

        print(f"获取美股价格失败 {code}: {'; '.join(errors)}")
        return None

    def fetch_finnhub(self, code: str, api_key: str, *, deadline: float | None = None) -> Optional[dict]:
        response = self.fetcher.session.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": code, "token": api_key},
            timeout=remaining_timeout(deadline, 10),
        )
        response.raise_for_status()
        data = response.json()

        current = data.get("c")
        prev_close = data.get("pc")
        if current is None:
            return None

        change = data.get("d", current - prev_close if prev_close else 0)
        change_pct = data.get("dp", (change / prev_close * 100) if prev_close else 0)
        rates = (
            self.fetcher._fetch_exchange_rates()
            if deadline is None
            else self.fetcher._fetch_exchange_rates(deadline=deadline)
        )
        usd_cny = rates["USDCNY"]

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

    def fetch_sina(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        return fetch_sina_us_quotes(self.fetcher, [code], timeout=15, deadline=deadline).get(code)
