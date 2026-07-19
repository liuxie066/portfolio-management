"""Optimized batch quote planner used by the PriceFetcher facade."""
from __future__ import annotations

from typing import Any, Dict, List

from src.asset_utils import detect_asset_type, detect_market_type

from .cache import PriceCachePolicy, market_type_from_asset_type
from .classifier import canonicalize_pricing_code
from .fixed import get_cash_price, get_crypto_value_price, get_mmf_price, is_crypto_value_code
from .payload import normalize_price_payload, remaining_timeout
from .providers.tencent_batch import fetch_tencent_quotes_batch
from .providers.us_batch import fetch_us_batch


class BatchPricePlanner:
    """Keep deadline-bound batch orchestration out of the fetcher facade."""

    def __init__(self, fetcher: Any):
        self.fetcher = fetcher

    @staticmethod
    def _mapped_value(mapping: Dict[str, Any] | None, original: str, canonical: str, default=None):
        if not mapping:
            return default
        for key in (original, original.upper(), canonical):
            if key in mapping:
                return mapping[key]
        return default

    def _fetch_rates(self, deadline: float | None) -> dict[str, float]:
        if deadline is None:
            return self.fetcher._fetch_exchange_rates()
        return self.fetcher._fetch_exchange_rates(deadline=deadline)

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
        deadline: float | None = None,
    ) -> Dict[str, Dict]:
        """Fetch quotes without allowing work to outlive the caller deadline."""
        del use_concurrent  # compatibility flag; deadline safety requires synchronous ownership
        name_map = name_map or {}
        original_by_canonical: Dict[str, List[str]] = {}
        primary_by_canonical: Dict[str, str] = {}
        for raw_code in codes or []:
            original = (raw_code or "").strip()
            canonical = canonicalize_pricing_code(original)
            if not canonical:
                continue
            original_by_canonical.setdefault(canonical, []).append(original)
            primary_by_canonical.setdefault(canonical, original)

        results: Dict[str, Dict] = {}
        stale_cache: Dict[str, Dict] = {}
        to_fetch: List[str] = []
        cache_policy = PriceCachePolicy(self.fetcher.storage, enabled=bool(self.fetcher.use_cache))

        for canonical, originals in original_by_canonical.items():
            primary = primary_by_canonical[canonical]
            if not force_refresh:
                try:
                    cached_quote = cache_policy.get(
                        canonical,
                        accept_stale=accept_stale_when_closed,
                        max_stale_after_expiry_sec=max_stale_after_expiry_sec,
                    )
                except (TypeError, ValueError):
                    cached_quote = None
                if cached_quote is not None:
                    payload = cached_quote.to_payload()
                    payload["code"] = primary
                    if cached_quote.stale:
                        stale_cache[canonical] = payload
                    else:
                        results[canonical] = payload
                        continue

            if use_cache_only:
                if canonical in stale_cache:
                    results[canonical] = stale_cache[canonical]
                continue

            fixed_factory = None
            if is_crypto_value_code(canonical):
                fixed_factory = get_crypto_value_price
            elif canonical == "CASH" or canonical.endswith("-CASH"):
                fixed_factory = get_cash_price
            elif canonical.endswith("-MMF"):
                fixed_factory = get_mmf_price
            if fixed_factory is not None:
                try:
                    payload = fixed_factory(canonical, lambda: self._fetch_rates(deadline))
                    payload["code"] = primary
                    results[canonical] = payload
                except Exception:
                    if canonical in stale_cache:
                        results[canonical] = stale_cache[canonical]
                continue

            to_fetch.append(canonical)

        if to_fetch:
            other_codes: List[str] = []
            us_codes: List[str] = []
            canonical_asset_types: Dict[str, Any] = {}
            canonical_names: Dict[str, str] = {}
            for canonical in to_fetch:
                primary = primary_by_canonical[canonical]
                asset_type = self._mapped_value(asset_type_map, primary, canonical)
                if asset_type is None and primary.upper().endswith((".US", ".HK", ".SH", ".SZ")):
                    asset_type = detect_asset_type(primary)[0]
                if asset_type is not None:
                    canonical_asset_types[canonical] = asset_type
                canonical_names[canonical] = self._mapped_value(name_map, primary, canonical, "")
                market_type = market_type_from_asset_type(
                    canonical,
                    asset_type,
                    detect_market_type(primary),
                )
                if market_type == "us":
                    if not skip_us:
                        us_codes.append(canonical)
                else:
                    other_codes.append(canonical)

            try:
                if other_codes:
                    results.update(
                        self.fetch_non_us(
                            other_codes,
                            canonical_names,
                            asset_type_map=canonical_asset_types,
                            deadline=deadline,
                        )
                    )
                if us_codes:
                    results.update(
                        fetch_us_batch(
                            self.fetcher,
                            us_codes,
                            canonical_names,
                            stale_cache,
                            deadline=deadline,
                        )
                    )
            except TimeoutError:
                pass

            for canonical in other_codes + us_codes:
                if canonical not in results and canonical in stale_cache:
                    results[canonical] = stale_cache[canonical]

        return self._map_results_to_originals(results, original_by_canonical)

    def fetch_non_us(
        self,
        codes: List[str],
        name_map: Dict[str, str],
        max_workers: int = 5,
        _nested: bool = False,
        asset_type_map: Dict[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Dict[str, Dict]:
        """Fetch non-US quotes via bounded Tencent batch, then sequential fallback."""
        del max_workers, _nested
        results: Dict[str, Dict] = {}
        errors: List[str] = []

        remaining_timeout(deadline, 8)
        try:
            batch_results, leftover = fetch_tencent_quotes_batch(
                self.fetcher,
                codes,
                name_map=name_map,
                asset_type_map=asset_type_map,
                deadline=deadline,
            )
            results.update(batch_results)
            codes = leftover
        except TimeoutError:
            raise
        except Exception as exc:
            errors.append(f"tencent_batch_failed: {exc}")

        for code in codes:
            remaining_timeout(deadline, 15)
            try:
                asset_name = name_map.get(code)
                result = self.fetcher.fetch(
                    code,
                    asset_name,
                    force_refresh=False,
                    asset_type_map=asset_type_map,
                    deadline=deadline,
                )
                if result and "error" not in result:
                    results[code] = normalize_price_payload(result)
                elif result and "error" in result:
                    errors.append(f"{code}: {result['error']}")
            except TimeoutError:
                raise
            except Exception as exc:
                errors.append(f"{code}: {exc}")

        if errors and len(errors) <= 3:
            print(f"部分资产查询失败: {'; '.join(errors[:3])}")
        return results

    @staticmethod
    def _map_results_to_originals(
        results: Dict[str, Dict], original_by_canonical: Dict[str, List[str]]
    ) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        for canonical, payload in results.items():
            originals = original_by_canonical.get(canonical, [canonical])
            for original in originals:
                mapped = dict(payload)
                mapped["code"] = original
                out[original] = mapped
        return out
