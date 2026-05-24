# PriceFetcher Compatibility Trim

- Removed unused compatibility wrappers from `src/price_fetcher.py` after an explicit caller scan.
- Kept core facade methods that are still part of the valuation/pricing path: `fetch`, `fetch_batch`, `_fetch_realtime`, `_fetch_exchange_rates`, `_get_cash_price`, `_fetch_a_stock`, `_fetch_hk_stock`, `_fetch_us_stock`, and `_fetch_us_stock_finnhub`.
- Did not remove `src/feishu/_price_mixin.py::_price_cache_to_dict`; it is a different storage-side helper and was not part of the `PriceFetcher` compatibility surface.
- The pricing authority remains `src/pricing/service.py`, `src/pricing/batch.py`, and `src/pricing/providers/*`; `PriceFetcher` should stay a small compatibility facade.
