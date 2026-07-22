"""Deadline-bound US batch quote provider."""
from __future__ import annotations

from typing import Dict, List

from src import config as _config

from ..payload import remaining_timeout
from .sina_us import fetch_sina_us_quotes
from .us import USStockProvider

# Seconds reserved for the Sina batch fallback so slow/hung Finnhub attempts
# cannot consume the whole caller deadline before the fallback runs.
SINA_DEADLINE_RESERVE_SEC = 6.0


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
    leftover: List[str] = []

    def use_expired_cache(code: str) -> None:
        if code in expired_cache:
            payload = dict(expired_cache[code])
            payload["source"] = "cache_fallback"
            results[code] = payload

    skipped: List[str] = []
    if not finnhub_key:
        leftover.extend(codes)
    finnhub_deadline = deadline - SINA_DEADLINE_RESERVE_SEC if deadline is not None else None
    for index, code in enumerate(codes if finnhub_key else ()):
        try:
            remaining_timeout(finnhub_deadline, 10)
        except TimeoutError:
            # Finnhub budget exhausted: stop Finnhub attempts and let the Sina
            # batch below use the reserved time for all leftover codes.
            skipped = codes[index:]
            leftover.extend(skipped)
            print(f"[美股价格] Finnhub 时限预算耗尽，剩余 {len(skipped)} 只并入新浪批量查询")
            break
        quote_code = code.replace(".", "-")
        result = None
        finnhub_error = None
        try:
            result = us_provider.fetch_finnhub(quote_code, finnhub_key, deadline=finnhub_deadline)
        except Exception as exc:
            finnhub_error = exc
            result = None

        if result:
            result["code"] = code
            results[code] = result
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            leftover.append(code)
            if finnhub_error is not None:
                print(f"[美股价格] {code}: Finnhub 获取失败: {finnhub_error}")

        if consecutive_failures >= 3:
            skipped = codes[index + 1 :]
            print(f"[美股价格] 连续 {consecutive_failures} 次获取失败，剩余 {len(skipped)} 只并入新浪批量查询")
            leftover.extend(skipped)
            break

    if leftover:
        try:
            sina_results = fetch_sina_us_quotes(fetcher, leftover, timeout=5, deadline=deadline)
        except Exception as exc:
            sina_results = {}
            print(f"[美股价格] 新浪批量查询失败: {exc}")
        for code in leftover:
            payload = sina_results.get(code)
            if payload:
                payload["code"] = code
                results[code] = payload
            else:
                print(f"[美股价格] {code}: 新浪未命中，尝试过期缓存")
                use_expired_cache(code)

    return results
