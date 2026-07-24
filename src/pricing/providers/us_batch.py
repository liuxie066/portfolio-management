"""Deadline-bound US batch quote provider."""
from __future__ import annotations

import time
from typing import Dict, List

from src import config as _config

from ..payload import remaining_timeout
from .sina_us import fetch_sina_us_quotes
from .us import FINNHUB_REQUEST_BUDGET_SEC, USStockProvider


def fetch_us_batch(
    fetcher,
    codes: List[str],
    name_map: Dict[str, str],
    expired_cache: Dict[str, Dict],
    max_workers: int = 3,
    _nested: bool = False,
    deadline: float | None = None,
) -> Dict[str, Dict]:
    """Fetch US quotes Sina-first, with one bounded Finnhub supplement window."""
    del name_map, max_workers, _nested
    results: Dict[str, Dict] = {}
    finnhub_key = _config.get("finnhub_api_key")
    us_provider = USStockProvider(fetcher)

    def use_expired_cache(code: str) -> None:
        if code in expired_cache:
            payload = dict(expired_cache[code])
            payload["source"] = "cache_fallback"
            results[code] = payload

    try:
        results.update(
            fetch_sina_us_quotes(
                fetcher,
                codes,
                timeout=5,
                deadline=deadline,
            )
        )
    except Exception as exc:
        print(
            "[美股价格] provider=sina_us "
            f"error_type={type(exc).__name__}"
        )

    leftover = [code for code in codes if code not in results]
    finnhub_deadline = time.monotonic() + FINNHUB_REQUEST_BUDGET_SEC
    if deadline is not None:
        finnhub_deadline = min(finnhub_deadline, deadline)

    for index, code in enumerate(leftover if finnhub_key else ()):
        try:
            remaining_timeout(finnhub_deadline, FINNHUB_REQUEST_BUDGET_SEC)
        except TimeoutError:
            remaining_count = len(leftover) - index
            print(
                "[美股价格] provider=finnhub "
                f"error_type=DeadlineExceeded remaining={remaining_count}"
            )
            break
        quote_code = code.replace(".", "-")
        try:
            result = us_provider.fetch_finnhub(quote_code, finnhub_key, deadline=finnhub_deadline)
        except Exception as exc:
            print(
                "[美股价格] provider=finnhub "
                f"symbol={code} error_type={type(exc).__name__}"
            )
            # One request-level provider failure is enough evidence that this
            # upstream should not be retried for every remaining symbol.
            break
        if result is not None:
            result["code"] = code
            results[code] = result

    for code in codes:
        if code not in results:
            print(f"[美股价格] symbol={code} status=missing fallback=expired_cache")
            use_expired_cache(code)

    return results
