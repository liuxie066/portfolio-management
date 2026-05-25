# Feishu Table Repository Migration Pattern

## Pattern

Move one Feishu table at a time from a heavy mixin into a repository:

1. Keep `FeishuStorage` public methods stable.
2. Make the mixin a thin delegation facade.
3. Put table-specific read/write/cache/guard logic in `src/feishu/repositories/*`.
4. Add a boundary test so the mixin does not grow direct Feishu client calls again.
5. Update the dependency graph after the table boundary changes.

## First Application

`nav_history` was migrated first because it is the highest-risk fact ledger for daily NAV recording and duplicate-date write blocking.

`holdings` followed because it feeds valuation and is the write surface for Futu cash/MMF synchronization.

`cash_flow` followed because manual entry reconciliation and aggregate caches directly affect share-change and NAV calculations.

`transactions` and `holdings_snapshot` followed to finish the Feishu core table boundary. The former preserves idempotency/dedup helpers used by trading and cash_flow; the latter preserves daily NAV audit snapshots.

`price_cache` was not moved because the current mixin is already a thin wrapper over `LocalPriceCache` and no longer writes the Feishu table on the main path.
