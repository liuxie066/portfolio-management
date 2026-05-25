# Feishu Core Table Repository Boundary

## Decision

`nav_history` storage behavior now lives in `src/feishu/repositories/NavHistoryRepository`.
`holdings` storage behavior now lives in `src/feishu/repositories/HoldingsRepository`.
`cash_flow` storage behavior now lives in `src/feishu/repositories/CashFlowRepository`.
`transactions` storage behavior now lives in `src/feishu/repositories/TransactionsRepository`.
`holdings_snapshot` storage behavior now lives in `src/feishu/repositories/SnapshotsRepository`.

`FeishuStorage` keeps the existing public methods through thin mixin facades. The NAV repository owns duplicate audit, NAV index preload/cache, date/account lookup, full record write/upsert, derived-field patch, and delete behavior. The holdings repository owns holdings index preload/cache, lookup, upsert, replace, bulk upsert, quantity updates, and delete behavior. The cash_flow repository owns deduped writes, manual-row reconciliation, flow reads, total CNY aggregation, and aggregate cache behavior. The transactions repository owns transaction idempotency, dedup lookup, reads, and deletes. The snapshots repository owns holdings_snapshot batch upsert.

## Reason

`nav_history` is the core daily NAV fact ledger, `holdings` is the source for valuation inputs plus Futu cash/MMF sync, `cash_flow` is the manual/system reconciliation source for share changes, `transactions` is the optional trade ledger, and `holdings_snapshot` makes NAV records auditable. Keeping their table logic inside large mixins made the storage boundary harder to reason about and encouraged more behavior to accumulate inside `FeishuStorage`.

## Consequence

Existing callers still use `FeishuStorage.write_nav_record()`, `write_nav_records()`, `get_nav_history()`, `patch_nav_derived_fields()`, `get_holdings()`, `get_holding()`, `upsert_holding()`, `replace_holding()`, `add_cash_flow()`, `get_cash_flows()`, `reconcile_cash_flows()`, `get_cash_flow_aggs()`, `add_transaction()`, `get_transactions()`, and `batch_upsert_holding_snapshots()`. New table-specific storage behavior should go into repositories instead of growing the mixin.
