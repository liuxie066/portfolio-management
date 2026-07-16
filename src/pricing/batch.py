"""Optimized batch quote planner used by the PriceFetcher facade."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from src.asset_utils import detect_market_type

from .cache import PriceCachePolicy, market_type_from_asset_type
from .fixed import (
    get_cash_price,
    get_cash_price_with_rates,
    get_crypto_value_price,
    get_crypto_value_price_with_rates,
    get_mmf_price,
    is_crypto_value_code,
)
from .providers.tencent_batch import fetch_tencent_quotes_batch
from .providers.us_batch import fetch_us_batch


class BatchPricePlanner:
    """Keep batch orchestration out of the fetcher facade."""

    def __init__(self, fetcher: Any):
        self.fetcher = fetcher

    def fetch_batch(
        self,
        codes: List[str],
        name_map: Dict[str, str] | None = None,
        asset_type_map: Dict[str, Any] | None = None,
        market_closed_ttl_multiplier: float = 1.0,
        accept_stale_when_closed: bool = False,
        max_stale_after_expiry_sec: int = 0,
        force_refresh: bool = False,
        use_concurrent: bool = True,
        skip_us: bool = False,
        use_cache_only: bool = False,
    ) -> Dict[str, Dict]:
        """Batch quote path with cache scan, grouping, and stale fallback."""
        fetcher = self.fetcher
        name_map = name_map or {}
        results: Dict[str, Dict] = {}

        original_codes = list(codes or [])
        norm_to_codes: Dict[str, List[str]] = {}
        unique_codes: List[str] = []
        norm_to_primary: Dict[str, str] = {}
        for code in original_codes:
            norm = (code or "").strip().upper()
            if not norm:
                continue
            if norm not in norm_to_primary:
                norm_to_primary[norm] = code
                unique_codes.append(code)
            norm_to_codes.setdefault(norm, []).append(code)

        codes = unique_codes

        to_fetch: List[str] = []
        expired_cache: Dict[str, Dict] = {}
        cache_policy = PriceCachePolicy(fetcher.storage, enabled=bool(fetcher.use_cache))

        try:
            batch_rates = fetcher._fetch_exchange_rates()
        except Exception:
            batch_rates = None

        for code in codes:
            normalized_code = (code or "").upper().strip()

            if is_crypto_value_code(normalized_code):
                try:
                    if batch_rates:
                        results[code] = get_crypto_value_price_with_rates(normalized_code, batch_rates)
                    else:
                        results[code] = get_crypto_value_price(
                            normalized_code,
                            getattr(fetcher, "_fetch_exchange_rates", None),
                        )
                    continue
                except Exception:
                    pass

            if normalized_code == "CASH" or normalized_code.endswith("-CASH"):
                try:
                    if batch_rates:
                        results[code] = get_cash_price_with_rates(normalized_code, batch_rates)
                    else:
                        results[code] = get_cash_price(
                            normalized_code,
                            getattr(fetcher, "_fetch_exchange_rates", None),
                        )
                    continue
                except Exception:
                    pass

            if normalized_code.endswith("-MMF"):
                results[code] = get_mmf_price(normalized_code)
                continue

            if fetcher.use_cache:
                cached_quote = cache_policy.get(
                    code,
                    accept_stale=accept_stale_when_closed,
                    max_stale_after_expiry_sec=max_stale_after_expiry_sec,
                )
                if cached_quote is not None:
                    payload = cached_quote.to_payload()
                    if not cached_quote.stale:
                        results[code] = payload
                        continue
                    expired_cache[code] = payload

            if not use_cache_only:
                to_fetch.append(code)
            elif code in expired_cache:
                results[code] = expired_cache[code]

        if not to_fetch:
            return self._copy_duplicate_results(results, norm_to_codes, norm_to_primary)

        us_codes = []
        other_codes = []
        for code in to_fetch:
            market_type = detect_market_type(code)
            if asset_type_map is not None and code in asset_type_map:
                market_type = market_type_from_asset_type(code, asset_type_map.get(code), market_type)
            if market_type == "us" and not skip_us:
                us_codes.append(code)
            elif market_type != "us":
                other_codes.append(code)
            elif skip_us and code in expired_cache:
                results[code] = expired_cache[code]

        if use_concurrent and (other_codes or us_codes):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = []

                if other_codes:
                    futures.append(
                        executor.submit(self.fetch_non_us, other_codes, name_map, 5, True, asset_type_map)
                    )

                if us_codes:
                    futures.append(
                        executor.submit(fetch_us_batch, fetcher, us_codes, name_map, expired_cache, 3, True)
                    )

                for future in as_completed(futures, timeout=25):
                    try:
                        batch_results = future.result()
                        results.update(batch_results)
                    except Exception as e:
                        print(f"[警告] 批量查询失败: {e}")

            for code, payload in list(results.items()):
                if isinstance(payload, dict) and payload.get("is_from_cache") and code in expired_cache:
                    payload.setdefault("is_stale", True)

            for code in other_codes + us_codes:
                if code not in results and code in expired_cache:
                    results[code] = expired_cache[code]
        else:
            for code in other_codes:
                asset_name = name_map.get(code)
                result = fetcher.fetch(code, asset_name, force_refresh, asset_type_map=asset_type_map)
                if result and "error" not in result:
                    results[code] = result
                elif code in expired_cache:
                    results[code] = expired_cache[code]

            if us_codes:
                results.update(fetch_us_batch(fetcher, us_codes, name_map, expired_cache))

        return self._copy_duplicate_results(results, norm_to_codes, norm_to_primary)

    def fetch_non_us(
        self,
        codes: List[str],
        name_map: Dict[str, str],
        max_workers: int = 5,
        _nested: bool = False,
        asset_type_map: Dict[str, Any] | None = None,
    ) -> Dict[str, Dict]:
        """Fetch non-US quotes, preferring Tencent batch before single quote fallback."""
        fetcher = self.fetcher
        results: Dict[str, Dict] = {}
        errors: List[str] = []

        try:
            batch_results, leftover = fetch_tencent_quotes_batch(
                fetcher,
                codes,
                name_map=name_map,
                asset_type_map=asset_type_map,
            )
            results.update(batch_results)
            codes = leftover
        except Exception as e:
            errors.append(f"tencent_batch_failed: {e}")

        def fetch_single(code: str):
            try:
                asset_name = name_map.get(code)
                return code, fetcher.fetch(code, asset_name, force_refresh=False, asset_type_map=asset_type_map)
            except Exception as e:
                return code, {"error": str(e)}

        if not codes:
            return results

        if _nested:
            for code in codes:
                fetched_code, result = fetch_single(code)
                if result and "error" not in result:
                    results[fetched_code] = result
                elif result and "error" in result:
                    errors.append(f"{fetched_code}: {result['error']}")
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_code = {executor.submit(fetch_single, code): code for code in codes}
                for future in as_completed(future_to_code):
                    code = future_to_code[future]
                    try:
                        _, result = future.result(timeout=15)
                        if result and "error" not in result:
                            results[code] = result
                        elif result and "error" in result:
                            errors.append(f"{code}: {result['error']}")
                    except Exception as e:
                        errors.append(f"{code}: 并发查询异常 {e}")

        if errors and len(errors) <= 3:
            print(f"部分资产查询失败: {'; '.join(errors[:3])}")

        return results

    @staticmethod
    def _copy_duplicate_results(
        results: Dict[str, Dict],
        norm_to_codes: Dict[str, List[str]],
        norm_to_primary: Dict[str, str],
    ) -> Dict[str, Dict]:
        if not norm_to_codes:
            return results

        out = dict(results)
        for norm, codes_list in norm_to_codes.items():
            if len(codes_list) <= 1:
                continue
            primary = norm_to_primary.get(norm)
            if primary and primary in results:
                for dup in codes_list:
                    out.setdefault(dup, results[primary])
        return out
