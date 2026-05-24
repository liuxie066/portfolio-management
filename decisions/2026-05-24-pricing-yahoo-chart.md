# Yahoo Chart Pricing Cleanup

- Yahoo Chart request and response parsing now live in `src/pricing/providers/yahoo_chart.py`.
- `USStockProvider.fetch_yahoo_chart()` and `fetch_us_batch()` share the same normalized payload construction.
- The batch path still owns batch concurrency, Finnhub-first behavior, consecutive-failure handling, and stale-cache fallback.
- The shared helper delays FX lookup until after a usable Yahoo quote is found, so empty quote payloads still return `None` instead of turning into FX failures.
