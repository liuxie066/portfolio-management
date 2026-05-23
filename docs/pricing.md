# Pricing & Caching

## Sources (priority)

1) **Tencent batch** for CN/HK/exchange-traded funds (`asset_type=exchange_fund`): `qt.gtimg.cn` (fast, low dependency)
2) **Tencent jj** for open-end fund NAV (`asset_type=otc_fund`) (fast, low dependency)
3) US: Finnhub (if API key) → Yahoo Chart fallback
4) Open-end funds: Tencent fund NAV → East Money fallback

## Cache policy

- If cache is valid (not expired): MUST use cache (unless `force_refresh=True`).
- If cache expired: try realtime.
  - If realtime succeeds: update cache.
  - If realtime fails: may fallback to stale cache, but MUST set `is_stale=true`.

Runtime ownership is now split as:

- `src/pricing/service.py`: public structured quote entry (`fetch_quote`, `fetch_batch`) and diagnostics.
- `src/pricing/batch.py`: optimized batch planner used by `PriceService.fetch_batch()` and the legacy facade.
- `src/pricing/providers/tencent_batch.py`: Tencent batch implementation for CN/HK/exchange funds/open-end fund NAV.
- `src/pricing/providers/us_batch.py`: fast US batch implementation with Finnhub/Yahoo Chart and stale fallback.
- `src/pricing/cache.py`: cache hit, stale fallback, TTL, and cache writes.
- `src/pricing/fixed.py`: fixed-price cash/MMF quote construction.
- `src/pricing/fx.py`: USD/HKD exchange-rate cache and multi-source fallback.
- `src/price_fetcher.py`: compatibility facade for existing callers.

## TTL

TTL is computed by `src/market_time.py::MarketTimeUtil.get_cache_ttl()`.

- Market open → 30 minutes
- Market closed → until next open (or fund next update)

## Observability

- `PortfolioManager.calculate_valuation()` appends a warning line:
  - `[价格汇总] realtime=..., cache=..., stale_fallback=..., missing=...`
  - plus Tencent batch meta when available.

- Use `scripts/diagnose_pricing.py` to inspect per-asset states.
