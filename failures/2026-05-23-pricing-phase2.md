# Pricing Phase 2

- Removing `MarketTimeUtil` from `src.price_fetcher` broke existing imports in `tests/test_price_fetcher.py`; keep it re-exported until callers move to `src.market_time`.
- Leaving method/source wording as `Yahoo API` after removing yfinance caused avoidable ambiguity; use `Yahoo Chart` consistently.
- Fixed CNY cash quotes should not require a legacy fetcher or FX callback; resolve fixed quotes before the legacy-fetcher requirement in `PriceService.fetch_quote()`.
- Deleting implementation from `PriceFetcher` is not enough if tests still import its private helpers; move tests to canonical `src/pricing/*` helpers so private wrappers can disappear later.
