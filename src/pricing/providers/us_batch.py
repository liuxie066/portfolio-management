"""Deadline-bound US batch quote provider."""
from __future__ import annotations

from typing import Dict, List

from src import config as _config

from ..payload import remaining_timeout
from .us import USStockProvider
from .yahoo_chart import fetch_yahoo_chart_quote


def fetch_us_batch(
    fetcher,
    codes: List[str],
    name_map: Dict[str, str],
    expired_cache: Dict[str, Dict],
    max_workers: int = 3,
    _nested: bool = False,
    deadline: float | None = None,
) -> Dict[str, Dict]:
    """Fetch US quotes sequentially so no worker can outlive the deadline."""
    del name_map, max_workers, _nested
    results: Dict[str, Dict] = {}
    consecutive_failures = 0
    finnhub_key = _config.get("finnhub_api_key")
    us_provider = USStockProvider(fetcher)

    def use_expired_cache(code: str) -> None:
        if code in expired_cache:
            payload = dict(expired_cache[code])
            payload["source"] = "cache_fallback"
            results[code] = payload

    for index, code in enumerate(codes):
        remaining_timeout(deadline, 10)
        quote_code = code.replace(".", "-")
        result = None
        if finnhub_key:
            try:
                result = us_provider.fetch_finnhub(quote_code, finnhub_key, deadline=deadline)
            except Exception:
                result = None
        if result is None:
            try:
                result = fetch_yahoo_chart_quote(
                    fetcher,
                    quote_code,
                    code=code,
                    timeout=5,
                    deadline=deadline,
                )
            except Exception:
                result = None

        if result:
            result["code"] = code
            results[code] = result
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            use_expired_cache(code)

        if consecutive_failures >= 3:
            print(f"[美股价格] 连续 {consecutive_failures} 次获取失败，跳过剩余美股查询")
            for remaining_code in codes[index + 1 :]:
                use_expired_cache(remaining_code)
            break

    return results
