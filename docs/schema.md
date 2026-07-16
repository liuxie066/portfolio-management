# Feishu Bitable Schema (truth source)

Schema version: `0004_manual_editable_fields`

This doc defines the canonical Feishu Bitable field names expected by the code.
Field names must match exactly: they are case-sensitive and underscore-sensitive.

Manual editing policy:
- Manual fields are safe to edit directly in Feishu.
- System fields should be hidden from normal manual-entry views. Leave them blank when manually adding rows.
- System-only tables should not be edited by hand except during explicit repair.
- "Required fields" means the Feishu table should contain these fields. It does not mean every manual row must fill every field.

## Active Tables

### holdings

Role: core

Purpose: current positions. This is the main manual-maintained table.

Business key: `(asset_id, account, broker)`

Manual edit policy:
- Non-Futu stock/fund/other holding rows are maintained manually in the manual view.
- For `broker=富途`, `pm futu sync` treats Futu as the source of truth for cash/MMF balances and STOCK/ETF quantities plus average cost.
- Existing Futu stock/ETF rows update only `quantity`, `avg_cost`, and `updated_at`; names and manual metadata remain unchanged. New rows use Futu name/type/currency metadata.
- `avg_cost` maps only from Futu `average_cost`; `diluted_cost` and deprecated `cost_price` are never used. Closed positions keep the row with `quantity=0` and clear `avg_cost`.

Manual view fields:
- `asset_id`, `asset_name`, `asset_type`, `account`, `broker`, `quantity`, `currency`
- Optional metadata: `avg_cost`, `asset_class`, `industry`, `tag` (`avg_cost` is system-managed for Futu stock/ETF rows)

System fields:
- `created_at`, `updated_at`

Required fields:
- `asset_id` (text) - manual
- `asset_name` (text) - manual
- `asset_type` (text/select) - manual
- `account` (text) - manual
- `broker` (text) - manual
- `quantity` (number) - manual
- `currency` (text) - manual

Optional fields:
- `avg_cost` (number) - manual except system-managed Futu stock/ETF rows
- `asset_class` (text/select) - manual
- `industry` (text/select) - manual
- `tag` (text/json) - manual
- `created_at` (text/datetime) - system
- `updated_at` (text/datetime) - system

Allowed `asset_type` values include: `a_stock`, `hk_stock`, `us_stock`, `exchange_fund`, `otc_fund`, `fund`, `cash`, `mmf`, `bond`, `crypto`, `other`.

### transactions

Role: optional

Purpose: optional trade ledger. It is not part of the daily NAV core path unless you choose to maintain trade history.

Manual edit policy:
- Current positions are maintained in `holdings`; cash movements used by NAV are maintained in `cash_flow`.
- Manual correction of existing transaction rows is acceptable for obvious data fixes.
- Do not treat this table as a required manual workflow while the product is focused on daily NAV and position distribution.
- If you later want trade replay/cost analysis, re-enable this table as a maintained ledger and migrate `tx_date` to a true date field.

Manual view fields:
- `tx_date`, `tx_type`, `asset_id`, `account`, `quantity`, `price`, `currency`
- Optional manual fields: `asset_name`, `asset_type`, `broker`, `fee`, `remark`

System fields:
- `amount`, `request_id`, `dedup_key`, `source`

Required fields:
- `tx_date` (text/date) - manual/system, currently stored as `YYYY-MM-DD` text in live Feishu
- `tx_type` (text/select) - manual
- `asset_id` (text) - manual
- `account` (text) - manual
- `quantity` (number) - manual
- `price` (number) - manual
- `currency` (text) - manual
- `request_id` (text) - system
- `dedup_key` (text) - system

Optional fields:
- `asset_name` (text) - manual/system
- `asset_type` (text/select) - manual/system
- `broker` (text) - manual
- `amount` (number) - system
- `fee` (number) - manual
- `remark` (text) - manual
- `source` (text) - system
- `tax` (number) - reserved
- `related_account` (text) - reserved

Allowed `tx_type` values: `BUY`, `SELL`, `DEPOSIT`, `WITHDRAW`.

### cash_flow

Role: core

Purpose: cash deposits/withdrawals used by NAV calculation. This table must stay easy to maintain manually.

Manual view fields:
- `flow_date`, `account`, `amount`, `currency`, `remark`

Manual rule:
- `amount` is positive for deposit and negative for withdrawal.
- Manual users do not fill exchange-rate, CNY, flow-type, dedup, or source fields.
- After manual insertion or edit, run `pm cash-flow reconcile` to preview generated fields, then `pm cash-flow reconcile --apply --confirm` to write them.

System fields:
- `flow_type` - derived from amount sign (`DEPOSIT` / `WITHDRAW`)
- `exchange_rate` - derived when `currency != CNY`
- `cny_amount` - derived from `amount * exchange_rate`
- `dedup_key` - generated for duplicate protection
- `source` - `manual`, `system`, `broker_sync`, or repair source

Required fields:
- `flow_date` (date) - manual
- `account` (text) - manual
- `amount` (number) - manual
- `currency` (text) - manual
- `flow_type` (text/select) - system
- `cny_amount` (number) - system
- `dedup_key` (text) - system

Optional fields:
- `exchange_rate` (number) - system
- `source` (text) - system
- `remark` (text) - manual
- `updated_at` (text/datetime) - system

Allowed `flow_type` values: `DEPOSIT`, `WITHDRAW`.

### nav_history

Role: core

Purpose: daily NAV facts. Do not use this as a normal manual-entry table.

Manual edit policy:
- Normal writes must go through `pm daily-job --write --confirm` or an explicit
  nav repair command.
- Manual editing is only for explicit repair and should be followed by an audit/reconcile pass.
- Duplicate `(account, date)` rows are considered data corruption. Run `pm nav duplicates --json`; normal NAV writes block until duplicates are repaired.

System-only fields:
- all fields below are generated or repaired by the system.

Required fields:
- `date` (date) - system
- `account` (text) - system
- `total_value` (number) - system
- `shares` (number) - system
- `nav` (number) - system

Optional fields:
- `cash_value` (number) - system
- `stock_value` (number) - system
- `fund_value` (number) - system
- `cn_stock_value` (number) - system
- `us_stock_value` (number) - system
- `hk_stock_value` (number) - system
- `stock_weight` (number) - system
- `cash_weight` (number) - system
- `cash_flow` (number) - system
- `share_change` (number) - system
- `mtd_nav_change` (number) - system
- `ytd_nav_change` (number) - system
- `pnl` (number) - system
- `mtd_pnl` (number) - system
- `ytd_pnl` (number) - system
- `details` (text/json) - system
- `updated_at` (text/datetime) - system

### holdings_snapshot

Role: core

Purpose: per-NAV-date holdings snapshot for audit/replay. This is a system-only table.

Business key: `(as_of, account, asset_id, broker)`

Manual edit policy:
- Do not manually edit during normal operation.
- If a snapshot is wrong, repair the source data and regenerate/rewrite the snapshot.

Required fields:
- `as_of` (date/text) - system
- `account` (text) - system
- `asset_id` (text) - system
- `broker` (text) - system
- `quantity` (number) - system
- `currency` (text) - system
- `price` (number) - system
- `cny_price` (number) - system
- `market_value_cny` (number) - system
- `dedup_key` (text) - system

Optional fields:
- `asset_name` (text) - system
- `avg_cost` (number) - system
- `source` (text) - system
- `remark` (text) - system

### compensation_tasks

Role: optional

Purpose: repair queue for partial multi-table write failures. This is a system-only table.

Required fields:
- `task_id` (text) - system
- `operation_type` (text/select) - system
- `account` (text) - system
- `status` (text/select) - system
- `payload` (text/json) - system
- `error` (text) - system
- `related_record_id` (text) - system
- `retry_count` (number) - system
- `created_at` (text/datetime) - system
- `updated_at` (text/datetime) - system

Optional fields:
- `resolved_at` (text/datetime) - system
- `resolution` (text) - system

### schema_version

Role: optional

Purpose: track Feishu schema migration status. This is a system-only table.

Required fields:
- `migration_id` (text) - system
- `description` (text) - system
- `applied_at` (text/datetime) - system
- `status` (text/select) - system

Optional fields:
- `notes` (text) - system/manual

## Retired Tables

- `price_cache` is no longer an active Feishu table. Price cache operations use local cache storage. Do not create or maintain `price_cache` in Feishu for new setups.
