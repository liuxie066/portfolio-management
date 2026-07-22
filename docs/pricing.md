# Pricing & Caching

## Sources (priority)

1) **Tencent batch** for CN/HK/exchange-traded funds (`asset_type=exchange_fund`): `qt.gtimg.cn` (fast, low dependency)
2) **Tencent jj** for open-end fund NAV (`asset_type=otc_fund`) (fast, low dependency)
3) US: Finnhub (if API key) → Sina US fallback (`hq.sinajs.cn/list=gb_*`)
4) Open-end funds: Tencent fund NAV → East Money fallback

## Cache policy

- If cache is valid (not expired): MUST use cache (unless `force_refresh=True`).
- If cache expired: try realtime.
  - If realtime succeeds: update cache.
  - If realtime fails: may fallback to stale cache, but MUST set `is_stale=true`.
- Stale acceptance has two contracts (`PriceCachePolicy.get`):
  - explicit window: caller passes `max_stale_after_expiry_sec > 0`, accepted within the window;
  - semantic (default when caller only sets `accept_stale_when_closed=True`): accepted only when
    the quote's market has NOT traded since expiry (`MarketTimeUtil.has_market_session_between`).
    A closed market cannot move the price, so the expired quote is still correct; if the market
    has traded since, the stale quote is rejected (fail closed). fund/unknown markets always reject.

Runtime ownership is now split as:

- `src/pricing/service.py`: public structured quote entry (`fetch_quote`, `fetch_batch`) and diagnostics.
- `src/pricing/batch.py`: optimized batch planner used by `PriceService.fetch_batch()` and the `PriceFetcher` facade.
- `src/pricing/providers/tencent_batch.py`: Tencent batch implementation for CN/HK/exchange funds/open-end fund NAV.
- `src/pricing/providers/us_batch.py`: fast US batch implementation with Finnhub/Sina US and stale fallback.
- `src/pricing/cache.py`: cache hit, stale fallback, TTL, and cache writes.
- `src/pricing/fixed.py`: fixed-price cash/MMF quote construction.
- `src/pricing/fx.py`: USD/HKD exchange-rate cache and multi-source fallback.
- `src/price_fetcher.py`: facade for existing `fetch`/`fetch_batch` callers; provider/classifier adapters live under `src/pricing/`.

## TTL

TTL is computed by `src/market_time.py::MarketTimeUtil.get_cache_ttl()`.

- Market open → 30 minutes
- Market closed → until next open (or fund next update)

## Observability

- `PortfolioManager.calculate_valuation()` appends a warning line:
  - `[价格汇总] realtime=..., cache=..., stale_fallback=..., missing=...`
  - plus Tencent batch meta when available.

- Use `scripts/diagnose_pricing.py` to inspect per-asset states.
