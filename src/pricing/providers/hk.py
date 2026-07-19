"""Hong Kong stock quote provider."""
from __future__ import annotations

import time
from typing import Optional

import requests

from ..payload import normalize_price_payload, remaining_timeout
from ..types import PriceRequest, ProviderResult


class HKStockProvider:
    name = "hk-stock"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def supports(self, request: PriceRequest) -> bool:
        code = request.normalized_code or request.code
        asset_type = request.asset_type.value if hasattr(request.asset_type, "value") else request.asset_type
        if asset_type in ("a_stock", "cn_fund", "exchange_fund", "otc_fund", "us_stock", "us_fund"):
            return False
        if asset_type in ("hk_stock", "hk_fund"):
            return True
        return code.startswith("HK") or (code.isdigit() and 4 <= len(code) <= 5)

    def fetch_one(self, request: PriceRequest) -> ProviderResult:
        started = time.time()
        code = request.normalized_code or request.code
        deadline = (request.hints or {}).get("_deadline")
        try:
            payload = self.fetch_hk_stock(code, deadline=deadline) if deadline is not None else self.fetch_hk_stock(code)
            return ProviderResult(payload, self.name, latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            return ProviderResult(None, self.name, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000))

    def fetch_hk_stock(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        try:
            result = self.fetch_from_tencent(code, deadline=deadline)
            if result:
                return result
        except requests.Timeout:
            print(f"[超时] 腾讯API获取港股价格 {code}")
        except Exception as exc:
            print(f"[腾讯API失败] 获取港股价格 {code}: {exc}")
        return None

    def fetch_from_tencent(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        numeric_part = code[2:].zfill(5) if code.startswith("HK") else code.zfill(5)
        query_code = f"hk{numeric_part}"

        from src.tencent_batch import fetch_batch as tencent_fetch_batch

        parts_map, _meta = tencent_fetch_batch(
            self.fetcher.session,
            [query_code],
            timeout=remaining_timeout(deadline, 5),
            chunk_size=1,
        )
        data = parts_map.get(query_code)
        if data and len(data) > 45:
            price = float(data[3])
            rates = (
                self.fetcher._fetch_exchange_rates()
                if deadline is None
                else self.fetcher._fetch_exchange_rates(deadline=deadline)
            )
            hkd_cny = rates["HKDCNY"]
            return normalize_price_payload(
                {
                    "code": code,
                    "name": data[1],
                    "price": price,
                    "prev_close": float(data[4]),
                    "open": float(data[5]),
                    "high": float(data[33]),
                    "low": float(data[34]),
                    "change": float(data[31]),
                    "change_pct": float(data[32]),
                    "volume": float(data[36]) * 100 if data[36] else 0,
                    "time": data[30],
                    "currency": "HKD",
                    "cny_price": price * hkd_cny,
                    "exchange_rate": hkd_cny,
                    "market_type": "hk",
                    "source": "tencent",
                }
            )
        return None
