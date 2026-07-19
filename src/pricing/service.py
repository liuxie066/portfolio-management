"""Provider-based pricing service."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, List, Optional

from src.asset_utils import detect_asset_type

from .batch import BatchPricePlanner
from .cache import PriceCachePolicy
from .classifier import canonicalize_pricing_code
from .fixed import get_cash_price, get_crypto_value_price, get_mmf_price, is_crypto_value_code
from .provider import PriceProvider
from .payload import normalize_price_payload
from .result import BatchPriceResult, PriceFailure, PriceQuote
from .types import PriceRequest


class PriceService:
    """Coordinate providers, cache policy, and quote diagnostics."""

    def __init__(self, providers: Iterable[PriceProvider], *, fetcher_context: Any = None):
        self.providers: List[PriceProvider] = list(providers)
        self.fetcher_context = fetcher_context
        self.last_diagnostics: list[dict] = []

    @classmethod
    def for_price_fetcher(cls, fetcher) -> "PriceService":
        from .providers import CNStockProvider, ETFProvider, FundProvider, HKStockProvider, USStockProvider

        return cls(
            [
                ETFProvider(fetcher),
                FundProvider(fetcher),
                CNStockProvider(fetcher),
                HKStockProvider(fetcher),
                USStockProvider(fetcher),
            ],
            fetcher_context=fetcher,
        )

    def fetch_realtime(self, request: PriceRequest, *, deadline: float | None = None) -> Optional[dict]:
        self.last_diagnostics = []
        if deadline is not None:
            request = replace(request, hints={**(request.hints or {}), "_deadline": deadline})
        for provider in self.providers:
            if not provider.supports(request):
                continue

            result = provider.fetch_one(request)
            diagnostic = {
                "provider": result.provider,
                "ok": result.ok,
                "error": result.error,
                "latency_ms": result.latency_ms,
            }
            if result.ok:
                try:
                    payload = normalize_price_payload(result.payload or {})
                except (TypeError, ValueError) as exc:
                    diagnostic["ok"] = False
                    diagnostic["error"] = f"invalid quote: {exc}"
                    self.last_diagnostics.append(diagnostic)
                    continue
                payload.setdefault("provider", result.provider)
                payload.setdefault("source_chain", [d["provider"] for d in self.last_diagnostics] + [result.provider])
                self.last_diagnostics.append(diagnostic)
                return payload
            self.last_diagnostics.append(diagnostic)

        return None

    def _fetch_rates(self, deadline: float | None) -> dict[str, float]:
        fetch = getattr(self.fetcher_context, "_fetch_exchange_rates", None)
        if fetch is None:
            raise KeyError("FX rate fetcher unavailable")
        if deadline is None:
            return fetch()
        return fetch(deadline=deadline)

    def fetch_quote(
        self,
        code: str,
        asset_name: str = "",
        force_refresh: bool = False,
        *,
        asset_type_map: dict[str, Any] | None = None,
        market_closed_ttl_multiplier: float = 1.0,
        accept_stale_when_closed: bool = False,
        max_stale_after_expiry_sec: int = 0,
        use_cache_only: bool = False,
        deadline: float | None = None,
    ) -> PriceQuote | PriceFailure:
        """Fetch one quote with cache policy and structured diagnostics."""
        self.last_diagnostics = []

        original_code = (code or "").upper().strip()
        canonical_code = canonicalize_pricing_code(original_code)
        asset_name = (asset_name or "").strip()
        cache_policy = PriceCachePolicy(
            getattr(self.fetcher_context, "storage", None),
            enabled=bool(getattr(self.fetcher_context, "use_cache", False)),
        )
        stale_quote: PriceQuote | None = None

        if not force_refresh:
            try:
                cached_quote = cache_policy.get(
                    canonical_code,
                    accept_stale=accept_stale_when_closed,
                    max_stale_after_expiry_sec=max_stale_after_expiry_sec,
                )
            except (TypeError, ValueError):
                cached_quote = None
            if cached_quote is not None:
                payload = cached_quote.to_payload()
                payload["code"] = original_code
                cached_quote = PriceQuote.from_payload(
                    payload,
                    code=original_code,
                    cache_status=cached_quote.cache_status,
                    stale=cached_quote.stale,
                    source_chain=cached_quote.source_chain,
                )
                if not cached_quote.stale:
                    return cached_quote
                stale_quote = cached_quote

        if use_cache_only:
            return stale_quote or PriceFailure(
                code=original_code,
                error_type="cache_miss",
                message="no usable cached quote",
                source_chain=["cache"],
            )

        fixed_source = None
        fixed_factory = None
        if is_crypto_value_code(canonical_code):
            fixed_source, fixed_factory = "crypto_value", get_crypto_value_price
        elif canonical_code == "CASH" or canonical_code.endswith("-CASH"):
            fixed_source, fixed_factory = "cash", get_cash_price
        elif canonical_code.endswith("-MMF"):
            fixed_source, fixed_factory = "mmf", get_mmf_price

        if fixed_factory is not None:
            try:
                payload = fixed_factory(canonical_code, lambda: self._fetch_rates(deadline))
                payload["code"] = original_code
                return PriceQuote.from_payload(
                    payload,
                    code=original_code,
                    cache_status="realtime",
                    source_chain=[fixed_source],
                )
            except Exception as exc:
                return PriceFailure(
                    code=original_code,
                    error_type="fx_rate_unavailable",
                    message=f"{fixed_source} quote unavailable: {exc}",
                    source_chain=[fixed_source],
                )

        if self.fetcher_context is None:
            return PriceFailure(
                code=original_code,
                error_type="unsupported",
                message="PriceService.fetch_quote requires fetcher context until providers own cache context",
            )

        asset_type = None
        if asset_type_map is not None:
            asset_type = asset_type_map.get(original_code)
            if asset_type is None:
                asset_type = asset_type_map.get(canonical_code)
            if asset_type is None:
                asset_type = asset_type_map.get(code)
        if asset_type is None and original_code.endswith((".US", ".HK", ".SH", ".SZ")):
            asset_type = detect_asset_type(original_code)[0]

        try:
            if asset_type is not None and deadline is not None:
                payload = self.fetcher_context._fetch_realtime(
                    canonical_code, asset_name, asset_type, deadline=deadline
                )
            elif asset_type is not None:
                payload = self.fetcher_context._fetch_realtime(canonical_code, asset_name, asset_type)
            elif deadline is not None:
                payload = self.fetcher_context._fetch_realtime(canonical_code, asset_name, deadline=deadline)
            else:
                payload = self.fetcher_context._fetch_realtime(canonical_code, asset_name)
        except Exception as exc:
            payload = None
            self.last_diagnostics.append(
                {
                    "provider": "realtime",
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": None,
                }
            )

        if payload:
            try:
                payload = normalize_price_payload(payload)
            except (TypeError, ValueError) as exc:
                self.last_diagnostics.append(
                    {"provider": "validation", "ok": False, "error": str(exc), "latency_ms": None}
                )
            else:
                payload["code"] = original_code
                quote = PriceQuote.from_payload(
                    payload,
                    code=original_code,
                    cache_status="realtime",
                    source_chain=payload.get("source_chain"),
                )
                market_type = cache_policy.save(
                    canonical_code,
                    quote,
                    asset_type=asset_type,
                    market_closed_ttl_multiplier=market_closed_ttl_multiplier,
                )
                if market_type:
                    out = quote.to_payload()
                    out["market_type"] = market_type
                    quote = PriceQuote.from_payload(
                        out,
                        code=original_code,
                        cache_status=quote.cache_status,
                        source_chain=quote.source_chain,
                    )
                return quote

        if stale_quote is not None:
            return stale_quote

        error_type = "deadline_exceeded" if any(
            "deadline" in str(d.get("error", "")).lower() for d in self.last_diagnostics
        ) else "quote_unavailable"
        return PriceFailure(
            code=original_code,
            error_type=error_type,
            message="realtime quote unavailable and no stale cache fallback",
            source_chain=[d.get("provider", "") for d in self.last_diagnostics if d.get("provider")],
        )

    def fetch(
        self,
        code: str,
        asset_name: str = "",
        force_refresh: bool = False,
        **kwargs,
    ) -> Optional[dict]:
        """Return a dict payload for callers that do not need structured results."""
        result = self.fetch_quote(code, asset_name, force_refresh, **kwargs)
        if isinstance(result, PriceQuote):
            return result.to_payload()
        if result.fallback_quote is not None:
            return result.fallback_quote.to_payload()
        return None

    def fetch_batch(
        self,
        codes: list[str],
        *,
        name_map: dict[str, str] | None = None,
        asset_type_map: dict[str, Any] | None = None,
        market_closed_ttl_multiplier: float = 1.0,
        accept_stale_when_closed: bool = False,
        max_stale_after_expiry_sec: int = 0,
        force_refresh: bool = False,
        use_concurrent: bool = True,
        skip_us: bool = False,
        use_cache_only: bool = False,
        deadline: float | None = None,
    ) -> BatchPriceResult:
        """Fetch a structured batch result."""
        name_map = name_map or {}
        result = BatchPriceResult()

        if self.fetcher_context is not None:
            payloads = BatchPricePlanner(self.fetcher_context).fetch_batch(
                codes,
                name_map=name_map,
                asset_type_map=asset_type_map,
                market_closed_ttl_multiplier=market_closed_ttl_multiplier,
                accept_stale_when_closed=accept_stale_when_closed,
                max_stale_after_expiry_sec=max_stale_after_expiry_sec,
                force_refresh=force_refresh,
                use_concurrent=use_concurrent,
                skip_us=skip_us,
                use_cache_only=use_cache_only,
                deadline=deadline,
            )
            for result_code, payload in payloads.items():
                try:
                    normalized = normalize_price_payload(payload)
                except (TypeError, ValueError) as exc:
                    result.failures[result_code] = PriceFailure(
                        code=result_code,
                        error_type="invalid_quote",
                        message=str(exc),
                    )
                    continue
                normalized["code"] = result_code
                result.quotes[result_code] = PriceQuote.from_payload(
                    normalized,
                    code=result_code,
                    cache_status=normalized.get("cache_status", "realtime"),
                    source_chain=normalized.get("source_chain"),
                )

            quote_norms = {canonicalize_pricing_code(result_code) for result_code in result.quotes}
            failure_norms = {canonicalize_pricing_code(result_code) for result_code in result.failures}
            for raw_code in codes or []:
                norm = canonicalize_pricing_code(raw_code)
                if norm and norm not in quote_norms and norm not in failure_norms:
                    result.failures[raw_code] = PriceFailure(
                        code=raw_code,
                        error_type="quote_unavailable",
                        message="batch quote unavailable",
                    )

            result.diagnostics = list(getattr(self.fetcher_context, "_last_price_service_diagnostics", []))
            return result

        diagnostics: list[dict] = []
        seen: set[str] = set()
        for raw_code in codes or []:
            norm = canonicalize_pricing_code(raw_code)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            quote = self.fetch_quote(
                raw_code,
                name_map.get(raw_code) or name_map.get(norm) or "",
                force_refresh=force_refresh,
                asset_type_map=asset_type_map,
                market_closed_ttl_multiplier=market_closed_ttl_multiplier,
                accept_stale_when_closed=accept_stale_when_closed,
                max_stale_after_expiry_sec=max_stale_after_expiry_sec,
                use_cache_only=use_cache_only,
                deadline=deadline,
            )
            if isinstance(quote, PriceQuote):
                result.quotes[raw_code] = quote
            else:
                result.failures[raw_code] = quote
            diagnostics.extend(self.last_diagnostics)

        result.diagnostics = diagnostics
        return result
