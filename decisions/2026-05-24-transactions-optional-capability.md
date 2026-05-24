# Transactions Optional Capability

- `transactions` is no longer treated as part of the daily NAV core path.
- Core NAV/product checks are based on `holdings`, `cash_flow`, `nav_history`, and `holdings_snapshot`.
- `transactions` remains available for optional trade ledger workflows, future trade replay, and cost analysis.
- `scripts/migrate_schema.py check-live` now reports `role`, `blocking`, `core_ok`, and `all_ok`; optional tables can be unconfigured without failing the core check.
- Current live `transactions.tx_date` is stored as `YYYY-MM-DD` text. Do not spend migration effort on it until trade ledger maintenance is re-enabled.
