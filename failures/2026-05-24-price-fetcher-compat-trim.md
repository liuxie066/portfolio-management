# PriceFetcher Compatibility Trim

- No test failure occurred in this trim.
- The main risk is false-positive deletion from name-based scans. Example: `_price_cache_to_dict` still exists in `src/feishu/_price_mixin.py`, but it is not the removed `PriceFetcher` method.
- Another risk is deleting provider adapters still used by batch internals. `_fetch_us_stock_finnhub` remains because `src/pricing/providers/us_batch.py` still calls it.
