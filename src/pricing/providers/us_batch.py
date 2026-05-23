"""Fast US batch quote provider."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from src import config as _config


def fetch_us_batch(
    fetcher,
    codes: List[str],
    name_map: Dict[str, str],
    expired_cache: Dict[str, Dict],
    max_workers: int = 3,
    _nested: bool = False,
) -> Dict[str, Dict]:
    """Fetch US quotes in batch with short timeouts and stale-cache fallback."""
    results: Dict[str, Dict] = {}
    if not codes:
        return results

    failure_lock = threading.Lock()
    consecutive_failures = [0]
    max_consecutive_failures = 3
    finnhub_key = _config.get("finnhub_api_key")

    def fetch_single_us(code):
        try:
            quote_code = code.replace(".", "-")

            if finnhub_key:
                try:
                    result = fetcher._fetch_us_stock_finnhub(quote_code, finnhub_key)
                    if result:
                        result["code"] = code
                        return code, result
                except Exception:
                    pass

            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_code}?interval=1d&range=2d"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                }
                response = fetcher.session.get(url, headers=headers, timeout=5)

                if response.status_code == 200:
                    data = response.json()
                    chart = data.get("chart", {})
                    if not chart.get("error"):
                        result = chart.get("result", [{}])[0]
                        meta = result.get("meta", {})
                        quotes = result.get("indicators", {}).get("quote", [{}])[0]
                        closes = [c for c in quotes.get("close", []) if c is not None]

                        if closes:
                            current = closes[-1]
                            prev_close = meta.get("previousClose") or meta.get("chartPreviousClose") or current
                            change = current - prev_close
                            change_pct = (change / prev_close * 100) if prev_close else 0
                            usd_cny = fetcher._fetch_exchange_rates()["USDCNY"]

                            return code, fetcher._normalize_price_payload(
                                {
                                    "code": code,
                                    "name": meta.get("shortName") or meta.get("longName") or code,
                                    "price": current,
                                    "prev_close": prev_close,
                                    "change": change,
                                    "change_pct": change_pct,
                                    "currency": meta.get("currency", "USD"),
                                    "cny_price": current * usd_cny,
                                    "exchange_rate": usd_cny,
                                    "market_type": "us",
                                    "source": "yahoo_chart",
                                }
                            )
            except Exception:
                pass

            return code, None
        except Exception:
            return code, None

    def use_expired_cache(code: str) -> None:
        if code in expired_cache:
            results[code] = expired_cache[code]
            results[code]["source"] = "cache_fallback"

    if _nested:
        for code in codes:
            _, result = fetch_single_us(code)
            if result:
                results[code] = result
                consecutive_failures[0] = 0
            else:
                consecutive_failures[0] += 1
                use_expired_cache(code)

            if consecutive_failures[0] >= max_consecutive_failures:
                print(f"[美股价格] 连续 {consecutive_failures[0]} 次获取失败，跳过剩余美股查询")
                for remaining_code in codes:
                    if remaining_code not in results:
                        use_expired_cache(remaining_code)
                break
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_code = {executor.submit(fetch_single_us, code): code for code in codes}

        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                _, result = future.result(timeout=10)
                if result:
                    results[code] = result
                    with failure_lock:
                        consecutive_failures[0] = 0
                else:
                    with failure_lock:
                        consecutive_failures[0] += 1
                    use_expired_cache(code)
            except Exception:
                with failure_lock:
                    consecutive_failures[0] += 1
                use_expired_cache(code)

            with failure_lock:
                should_break = consecutive_failures[0] >= max_consecutive_failures
            if should_break:
                print(f"[美股价格] 连续 {consecutive_failures[0]} 次获取失败，跳过剩余美股查询")
                for _, remaining_code in future_to_code.items():
                    if remaining_code not in results:
                        use_expired_cache(remaining_code)
                break

    return results
