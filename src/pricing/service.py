"""Provider-based pricing service."""
from __future__ import annotations

from typing import Any, Iterable, List, Optional

from .batch import BatchPricePlanner
from .cache import PriceCachePolicy
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

    def fetch_realtime(self, request: PriceRequest) -> Optional[dict]:
        self.last_diagnostics = []
        for provider in self.providers:
            if not provider.supports(request):
                continue

            result = provider.fetch_one(request)
            self.last_diagnostics.append(
                {
                    "provider": result.provider,
                    "ok": result.ok,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                }
            )
            if result.ok:
                payload = dict(result.payload or {})
                payload.setdefault("provider", result.provider)
                payload.setdefault("source_chain", [d["provider"] for d in self.last_diagnostics])
                return payload

        return None

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
    ) -> PriceQuote | PriceFailure:
        """Fetch one quote with cache policy and structured diagnostics."""
        self.last_diagnostics = []

        original_code = code
        code = (code or "").upper().strip()
        asset_name = (asset_name or "").strip()

        if is_crypto_value_code(code):
            try:
                return PriceQuote.from_payload(
                    get_crypto_value_price(code, getattr(self.fetcher_context, "_fetch_exchange_rates", None)),
                    code=code,
                    cache_status="realtime",
                    source_chain=["crypto_value"],
                )
            except Exception as exc:
                return PriceFailure(
                    code=code,
                    error_type="fx_rate_unavailable",
                    message=f"crypto value quote unavailable: {exc}",
                    source_chain=["crypto_value"],
                )

        if code == "CASH" or code.endswith("-CASH"):
            try:
                return PriceQuote.from_payload(
                    get_cash_price(code, getattr(self.fetcher_context, "_fetch_exchange_rates", None)),
                    code=code,
                    cache_status="realtime",
                    source_chain=["cash"],
                )
            except Exception as exc:
                return PriceFailure(
                    code=code,
                    error_type="fx_rate_unavailable",
                    message=f"cash quote unavailable: {exc}",
                    source_chain=["cash"],
                )

        if code.endswith("-MMF"):
            return PriceQuote.from_payload(
                get_mmf_price(code),
                code=code,
                cache_status="realtime",
                source_chain=["mmf"],
            )

        if self.fetcher_context is None:
            failure = PriceFailure(
                code=code,
                error_type="unsupported",
                message="PriceService.fetch_quote requires fetcher context until providers own cache context",
            )
            return failure

        cache_policy = PriceCachePolicy(
            getattr(self.fetcher_context, "storage", None),
            enabled=bool(getattr(self.fetcher_context, "use_cache", False)),
        )
        stale_quote: PriceQuote | None = None

        if not force_refresh:
            cached_quote = cache_policy.get(
                code,
                accept_stale=accept_stale_when_closed,
                max_stale_after_expiry_sec=max_stale_after_expiry_sec,
            )
            if cached_quote is not None:
                if not cached_quote.stale:
                    return cached_quote
                stale_quote = cached_quote

        if use_cache_only:
            if stale_quote is not None:
                return stale_quote
            return PriceFailure(
                code=code,
                error_type="cache_miss",
                message="no usable cached quote",
                source_chain=["cache"],
            )

        asset_type = None
        if asset_type_map is not None:
            asset_type = asset_type_map.get(code)
            if asset_type is None:
                asset_type = asset_type_map.get(original_code)

        try:
            if asset_type is not None:
                payload = self.fetcher_context._fetch_realtime(code, asset_name, asset_type)
            else:
                payload = self.fetcher_context._fetch_realtime(code, asset_name)
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
            payload = normalize_price_payload(payload)
            quote = PriceQuote.from_payload(
                payload,
                code=code,
                cache_status="realtime",
                source_chain=payload.get("source_chain"),
            )
            market_type = cache_policy.save(
                code,
                quote,
                asset_type=asset_type,
                market_closed_ttl_multiplier=market_closed_ttl_multiplier,
            )
            out = quote.to_payload()
            if market_type:
                out["market_type"] = market_type
                quote = PriceQuote.from_payload(
                    out,
                    code=code,
                    cache_status=quote.cache_status,
                    source_chain=quote.source_chain,
                )
            return quote

        if stale_quote is not None:
            return stale_quote

        return PriceFailure(
            code=code,
            error_type="quote_unavailable",
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
    ) -> BatchPriceResult:
        """Fetch a structured batch result.

        With a fetcher context, this uses the optimized batch planner and wraps
        payload dicts into structured quote results.
        """
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
            )
            for code, payload in payloads.items():
                result.quotes[code] = PriceQuote.from_payload(
                    payload,
                    code=code,
                    cache_status=payload.get("cache_status", "realtime"),
                    source_chain=payload.get("source_chain"),
                )

            quote_norms = {str(code).strip().upper() for code in result.quotes}
            for raw_code in codes or []:
                norm = (raw_code or "").strip().upper()
                if norm and raw_code not in result.quotes and norm not in quote_norms:
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
            norm = (raw_code or "").strip().upper()
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
            )
            if isinstance(quote, PriceQuote):
                result.quotes[raw_code] = quote
            else:
                result.failures[raw_code] = quote
            diagnostics.extend(self.last_diagnostics)

        result.diagnostics = diagnostics
        return result
