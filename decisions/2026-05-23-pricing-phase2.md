# Pricing Phase 2

- Price source order is Finnhub then Yahoo Chart for US assets; yfinance is removed.
- AKShare is removed from the core quote path; CN/HK/exchange funds use Tencent, open-end funds use Tencent fund NAV then East Money.
- `PriceService` is the structured quote and batch entry. `PriceFetcher` remains the compatibility facade and returns legacy dict payloads for existing callers.
- Cache and stale fallback policy live in `src/pricing/cache.py`; FX rate caching and fallback live in `src/pricing/fx.py`.
- Optimized batch planning lives in `src/pricing/batch.py` and reuses `PriceCachePolicy`.
- Provider-specific batch source implementations live in `src/pricing/providers/tencent_batch.py` and `src/pricing/providers/us_batch.py`; `PriceFetcher` keeps compatibility adapters only.
- Non-US concurrent batch orchestration also lives in `BatchPricePlanner.fetch_non_us`; `PriceFetcher._fetch_concurrent()` is compatibility-only.
- Cash/MMF fixed quotes live in `src/pricing/fixed.py`; `PriceFetcher._get_cash_price*()` and `_get_mmf_price()` are compatibility wrappers only.
- Price cache record-to-payload normalization lives in `src/pricing/cache.py::price_cache_to_payload()` and is shared by `PriceCachePolicy` plus legacy wrappers.
- The unused `LegacyRoutingProvider` migration adapter was deleted; provider routing now uses explicit provider classes wired by `PriceService.for_legacy_fetcher()`.
