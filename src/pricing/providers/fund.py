"""Fund quote provider."""
from __future__ import annotations

import re
import time
from typing import Optional

from ..classifier import is_otc_fund
from ..payload import normalize_price_payload, remaining_timeout
from ..types import PriceRequest, ProviderResult


class FundProvider:
    name = "fund"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def supports(self, request: PriceRequest) -> bool:
        code = request.normalized_code or request.code
        hints = request.hints or {}
        asset_type = request.asset_type.value if hasattr(request.asset_type, "value") else request.asset_type
        if asset_type in ("a_stock", "hk_stock", "us_stock"):
            return False
        if asset_type in ("otc_fund", "fund"):
            return True
        if asset_type == "exchange_fund":
            return False
        return bool(hints.get("is_fund", False) or is_otc_fund(code))

    def fetch_one(self, request: PriceRequest) -> ProviderResult:
        started = time.time()
        code = request.normalized_code or request.code
        deadline = (request.hints or {}).get("_deadline")
        try:
            payload = self.fetch_fund(code, deadline=deadline) if deadline is not None else self.fetch_fund(code)
            return ProviderResult(payload, self.name, latency_ms=int((time.time() - started) * 1000))
        except Exception as exc:
            return ProviderResult(None, self.name, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000))

    def fetch_fund(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        try:
            result = self.fetch_from_tencent(code, deadline=deadline)
            if result:
                result["market_type"] = "fund"
                return result
        except Exception:
            pass

        try:
            result = self.fetch_from_eastmoney(code, deadline=deadline)
            if result:
                result["market_type"] = "fund"
                return result
        except Exception:
            pass
        return None

    def fetch_from_tencent(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        code = (code or "").strip().upper()
        if code.startswith(("SH", "SZ")):
            code = code[2:]
        if not (code.isdigit() and len(code) == 6):
            return None

        query_code = f"jj{code}"
        url = f"https://qt.gtimg.cn/q={query_code}"
        response = self.fetcher.session.get(url, timeout=remaining_timeout(deadline, 5))
        response.encoding = "gb2312"
        text = response.text

        match = re.search(rf'v_{query_code}="([^"]+)"', text)
        if not match:
            return None

        parts = match.group(1).split("~")
        if len(parts) < 9:
            return None

        try:
            nav = float(parts[5])
        except Exception:
            return None
        if not nav or nav <= 0:
            return None

        return normalize_price_payload(
            {
                "code": code,
                "name": parts[1],
                "price": nav,
                "nav_date": parts[8] if len(parts) > 8 else None,
                "currency": "CNY",
                "cny_price": nav,
                "source": "tencent_jj",
            }
        )

    def fetch_from_eastmoney(self, code: str, *, deadline: float | None = None) -> Optional[dict]:
        try:
            url = f"https://fund.eastmoney.com/{code}.html"
            response = self.fetcher.session.get(url, timeout=remaining_timeout(deadline, 10))
            response.encoding = "utf-8"
            text = response.text

            name_match = re.search(r"<h1[^>]*>([^<]+)</h1>", text)
            name = name_match.group(1).strip() if name_match else None
            nav_match = re.search(r'class="dataNums"[^>]*>\s*<span[^>]*>([\d.]+)</span>', text)
            if not nav_match:
                return None

            nav = float(nav_match.group(1))
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            change_match = re.search(r'class="(?:(?:ui-color-red)|(?:ui-color-green))"[^>]*>([+-]?[\d.]+)%', text)
            return normalize_price_payload(
                {
                    "code": code,
                    "name": name,
                    "price": nav,
                    "nav_date": date_match.group(1) if date_match else None,
                    "change_pct": float(change_match.group(1)) if change_match else None,
                    "currency": "CNY",
                    "cny_price": nav,
                    "source": "eastmoney",
                }
            )
        except Exception as exc:
            print(f"从东方财富获取基金价格失败 {code}: {exc}")
            return None
