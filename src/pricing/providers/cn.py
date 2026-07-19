"""China A-share quote provider."""
from __future__ import annotations

import time
from typing import Optional

import requests

from ..classifier import is_otc_fund
from ..payload import normalize_price_payload, remaining_timeout
from ..types import PriceRequest, ProviderResult


class CNStockProvider:
    name = "cn-stock"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def supports(self, request: PriceRequest) -> bool:
        code = request.normalized_code or request.code
        asset_type = request.asset_type.value if hasattr(request.asset_type, "value") else request.asset_type
        if asset_type in ("otc_fund", "hk_stock", "hk_fund", "us_stock", "us_fund"):
            return False
        if not (
            code.startswith(("SH", "SZ"))
            or (code.isdigit() and len(code) == 6 and code.startswith(("6", "0", "3", "1", "2")))
        ):
            return False
        if asset_type == "a_stock":
            return True
        hints = request.hints or {}
        is_likely_fund = hints.get("is_fund", False) or is_otc_fund(code)
        return not (is_likely_fund and not hints.get("is_stock", False))

    def fetch_one(self, request: PriceRequest) -> ProviderResult:
        started = time.time()
        code = request.normalized_code or request.code
        deadline = (request.hints or {}).get("_deadline")
        try:
            payload = self.fetch_a_stock(code, deadline=deadline) if deadline is not None else self.fetch_a_stock(code)
            return ProviderResult(payload, self.name, latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            return ProviderResult(None, self.name, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000))

    def fetch_a_stock(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        try:
            result = self.fetch_from_tencent(code, deadline=deadline)
            if result:
                return result
        except requests.Timeout:
            print(f"[超时] 腾讯API获取A股价格 {code}")
        except Exception as exc:
            print(f"[腾讯API失败] 获取A股价格 {code}: {exc}")
        return None

    def fetch_from_tencent(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        if code.startswith(("SH", "SZ")):
            query_code = code.lower()
        elif code.isdigit():
            query_code = f"sh{code}" if code.startswith("6") else f"sz{code}"
        else:
            query_code = code

        from src.tencent_batch import fetch_batch as tencent_fetch_batch

        parts_map, _meta = tencent_fetch_batch(
            self.fetcher.session,
            [query_code],
            timeout=remaining_timeout(deadline, 5),
            chunk_size=1,
        )
        data = parts_map.get(query_code)
        if data and len(data) > 45:
            return normalize_price_payload(
                {
                    "code": code,
                    "name": data[1],
                    "price": float(data[3]),
                    "prev_close": float(data[4]),
                    "open": float(data[5]),
                    "high": float(data[33]),
                    "low": float(data[34]),
                    "change": float(data[31]),
                    "change_pct": float(data[32]),
                    "volume": float(data[36]) * 100 if data[36] else 0,
                    "time": data[30],
                    "currency": "CNY",
                    "cny_price": float(data[3]),
                    "market_type": "cn",
                    "source": "tencent",
                }
            )
        return None
