# Calculation Cleanup

- Changing industry distribution to use `calculate_valuation()` exposed old tests that mocked holdings and prices but not `get_total_shares()`. Facade tests need to mock every dependency used by the canonical calculation path.
- A report path that silently continued after `record_nav` failure could make operators believe NAV was recorded when the write had actually failed.
- Summing raw cash-flow amounts ignores currency normalization and can understate multi-currency totals when `cny_amount` is available.
- Treating normalized provider payload keys as failures creates false missing-price diagnostics for lowercase or alternate input symbols.
