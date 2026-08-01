# Feishu Bitable Schema and Operating Policy

Schema version: `0005_cash_flow_holding_effects`

The generated block below is a projection of the canonical typed registry in
`src/feishu/contracts`. Field names, types, UI types, encodings, select options,
ownership, clearability, business keys, and write requirements must be changed
there first. The remaining prose defines human operating policy.

Manual editing policy:
- Manual fields are safe to edit directly in Feishu.
- System fields should be hidden from normal manual-entry views. Leave them blank when manually adding rows.
- System-only tables should not be edited by hand except during explicit repair.
- Registry schema presence and per-operation row requirements are separate contracts.

<!-- BEGIN GENERATED FEISHU CONTRACTS -->
## Generated Field Contracts

Generated from `src.feishu.contracts.TABLE_CONTRACTS`; do not edit this block by hand.

### `holdings` contract

- Role: `core`
- Business key: `asset_id`, `account`, `broker`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `asset_id` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `asset_name` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `asset_type` | 3 | `SingleSelect` | `single_select` | `required` | `manual` | `no` | `a_stock`, `cash`, `otc_fund`, `other`, `hk_stock`, `us_stock`, `exchange_fund`, `us_fund`, `mmf`, `crypto` |
| `account` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `broker` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `quantity` | 2 | `Number` | `number` | `required` | `manual` | `no` |  |
| `avg_cost` | 2 | `Number` | `number` | `optional` | `mixed` | `yes` |  |
| `currency` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `asset_class` | 3 | `SingleSelect` | `single_select` | `optional` | `manual` | `yes` | `美国资产`, `另类资产`, `中国资产`, `现金`, `港股资产` |
| `industry` | 3 | `SingleSelect` | `single_select` | `optional` | `manual` | `yes` | `金融`, `AI`, `中概`, `非行业指数`, `区块链`, `能源`, `消费`, `房地产`, `半导体`, `现金`, `科技`, `其他` |
| `tag` | 1 | `Text` | `json_text` | `optional` | `manual` | `no` |  |
| `created_at` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |
| `updated_at` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |

Write contracts:

- `create` row-required fields: `account`, `asset_id`, `asset_name`, `asset_type`, `broker`, `currency`, `quantity`
- `update` row-required fields: none
- `delete` row-required fields: none

### `cash_flow` contract

- Role: `core`
- Business key: `dedup_key`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `flow_date` | 5 | `DateTime` | `datetime` | `required` | `manual` | `no` |  |
| `account` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `broker` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `amount` | 2 | `Number` | `number` | `required` | `manual` | `no` |  |
| `currency` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `flow_type` | 3 | `SingleSelect` | `single_select` | `required` | `system` | `no` | `DEPOSIT`, `WITHDRAW` |
| `cny_amount` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `dedup_key` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `exchange_rate` | 2 | `Number` | `number` | `optional` | `system` | `yes` |  |
| `source` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |
| `remark` | 1 | `Text` | `text` | `optional` | `manual` | `yes` |  |
| `updated_at` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |

Write contracts:

- `create` row-required fields: `account`, `amount`, `currency`, `flow_date`
- `update` row-required fields: none
- `delete` row-required fields: none

Forbidden fields: `exchange_rate_date`, `exchange_rate_evidence_type`, `exchange_rate_source`.

### `nav_history` contract

- Role: `core`
- Business key: `account`, `date`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `date` | 5 | `DateTime` | `datetime` | `required` | `system` | `no` |  |
| `account` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `total_value` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `shares` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `nav` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `cash_value` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `stock_value` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `fund_value` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `cn_stock_value` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `us_stock_value` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `hk_stock_value` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `stock_weight` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `cash_weight` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `cash_flow` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `share_change` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `mtd_nav_change` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `ytd_nav_change` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `pnl` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `mtd_pnl` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `ytd_pnl` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `details` | 1 | `Text` | `json_text` | `optional` | `system` | `no` |  |
| `updated_at` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |

Write contracts:

- `create` row-required fields: `account`, `date`, `nav`, `shares`, `total_value`
- `update` row-required fields: none
- `delete` row-required fields: none

### `holdings_snapshot` contract

- Role: `core`
- Business key: `as_of`, `account`, `asset_id`, `broker`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `as_of` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `account` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `asset_id` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `broker` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `quantity` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `currency` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `price` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `cny_price` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `market_value_cny` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `dedup_key` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `asset_name` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |
| `avg_cost` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `source` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |
| `remark` | 1 | `Text` | `text` | `optional` | `system` | `yes` |  |

Write contracts:

- `create` row-required fields: `account`, `as_of`, `asset_id`, `broker`, `cny_price`, `currency`, `dedup_key`, `market_value_cny`, `price`, `quantity`
- `update` row-required fields: none
- `delete` row-required fields: none

### `transactions` contract

- Role: `optional`
- Business key: `request_id`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `tx_date` | 1 | `Text` | `text` | `required` | `mixed` | `no` |  |
| `tx_type` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `asset_id` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `account` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `quantity` | 2 | `Number` | `number` | `required` | `manual` | `no` |  |
| `price` | 2 | `Number` | `number` | `required` | `manual` | `no` |  |
| `currency` | 1 | `Text` | `text` | `required` | `manual` | `no` |  |
| `request_id` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `dedup_key` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `asset_name` | 1 | `Text` | `text` | `optional` | `mixed` | `no` |  |
| `asset_type` | 1 | `Text` | `text` | `optional` | `mixed` | `no` |  |
| `market` | 1 | `Text` | `text` | `optional` | `mixed` | `no` |  |
| `amount` | 2 | `Number` | `number` | `optional` | `system` | `no` |  |
| `fee` | 2 | `Number` | `number` | `optional` | `manual` | `no` |  |
| `remark` | 1 | `Text` | `text` | `optional` | `manual` | `no` |  |

Write contracts:

- none (read-only table)

### `compensation_tasks` contract

- Role: `optional`
- Business key: `task_id`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `task_id` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `operation_type` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `account` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `status` | 3 | `SingleSelect` | `single_select` | `required` | `system` | `no` | `PENDING`, `RUNNING`, `FAILED`, `RESOLVED` |
| `payload` | 1 | `Text` | `json_text` | `required` | `system` | `no` |  |
| `error` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `related_record_id` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `retry_count` | 2 | `Number` | `number` | `required` | `system` | `no` |  |
| `created_at` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `updated_at` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `resolved_at` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |
| `resolution` | 1 | `Text` | `text` | `optional` | `system` | `no` |  |

Write contracts:

- `create` row-required fields: `account`, `created_at`, `error`, `operation_type`, `payload`, `retry_count`, `status`, `task_id`, `updated_at`
- `update` row-required fields: none
- `delete` row-required fields: none

### `schema_version` contract

- Role: `optional`
- Business key: `migration_id`

| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |
|---|---:|---|---|---|---|---|---|
| `migration_id` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `description` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `applied_at` | 1 | `Text` | `text` | `required` | `system` | `no` |  |
| `status` | 3 | `SingleSelect` | `single_select` | `required` | `system` | `no` | `APPLIED`, `FAILED` |
| `notes` | 1 | `Text` | `text` | `optional` | `mixed` | `no` |  |

Write contracts:

- `create` row-required fields: `applied_at`, `description`, `migration_id`, `status`
- `update` row-required fields: none
- `delete` row-required fields: none

`price_cache` is retired as a remote table; its storage is local-only.
<!-- END GENERATED FEISHU CONTRACTS -->

## Active Tables

### holdings

Purpose: current positions. This is the main manual-maintained table.

Manual edit policy:
- Non-Futu stock/fund/other holding rows are maintained manually in the manual view.
- CASH rows are not directly maintained by scheduled sync. PM stores one
  CNY-denominated aggregate `CNY-CASH` row; Futu original-currency observations
  are neither compared with it nor split into per-currency holdings. External
  cash-flow effects remain a separate explicit preview/confirmation workflow.
- For `broker=富途`, `pm futu sync` observes per-currency CASH, synchronizes MMF,
  and treats Futu as the source of truth for STOCK/ETF quantity and average cost.
- Existing Futu stock/ETF rows update only `quantity`, `avg_cost`, and `updated_at`; names and manual metadata remain unchanged. New rows use Futu name/type/currency metadata.
- `avg_cost` maps only from Futu `average_cost`; `diluted_cost` and deprecated `cost_price` are never used. Closed positions keep the row with `quantity=0` and clear `avg_cost`.

Manual view fields:
- `asset_id`, `asset_name`, `asset_type`, `account`, `broker`, `quantity`, `currency`
- Optional metadata: `avg_cost`, `asset_class`, `industry`, `tag` (`avg_cost` is system-managed for Futu stock/ETF rows)

System fields:
- `created_at`, `updated_at` are Text dates written as `YYYY/MM/DD`. Reads also
  accept the predecessor `YYYY-MM-DD HH:MM:SS` representation during the
  compatibility transition; new writes never emit that predecessor form.

`asset_class` describes the geography of the underlying assets or economic
exposure, not fund domicile, distribution channel, trading currency, or listing
venue. Fund and cross-market security rows therefore retain explicit manual
values unless exact instrument-level exposure evidence is available. Only
instrument types that prove the classification (currently A-shares, CASH, and
MMF) are eligible for deterministic `asset_class` completion or conflict
detection.

### transactions

Status: legacy read-only archive

Purpose: historical trade ledger. The product has no active `transactions` writer;
Futu synchronization updates current `holdings` quantity and average cost directly.

Manual edit policy:
- Current positions are maintained in `holdings`; cash movements used by NAV are maintained in `cash_flow`.
- Manual correction of existing transaction rows is acceptable for obvious data fixes.
- Do not treat this table as a required manual workflow or a source for current positions.
- If you later want trade replay/cost analysis, re-enable this table as a maintained ledger and migrate `tx_date` to a true date field.

Manual view fields:
- `tx_date`, `tx_type`, `asset_id`, `account`, `quantity`, `price`, `currency`
- Optional archive fields: `asset_name`, `asset_type`, `market`, `fee`, `remark`

System fields:
- `amount`, `request_id`, `dedup_key`

### cash_flow

Purpose: cash deposits/withdrawals used by NAV calculation. This table must stay easy to maintain manually.

Manual view fields:
- `flow_date`, `account`, `broker`, `amount`, `currency`, `remark`

Manual rule:
- `amount` is positive for deposit and negative for withdrawal.
- `broker` is mandatory and explicitly routes the same-currency CASH identity.
- Manual users do not fill exchange-rate, CNY, flow-type, dedup, or source fields.
- When the combined Feishu event listener is activated, a fresh exact-record
  worker automatically completes deterministic generated fields for valid CNY
  rows. Foreign rows are completed automatically only when their existing local
  FX confirmation still matches exactly; otherwise a durable receipt requires
  operator confirmation. `pm cash-flow reconcile --record-id ... --apply
  --confirm` remains the recovery and FX-confirmation workflow.

System fields:
- `flow_type` - derived from amount sign (`DEPOSIT` / `WITHDRAW`)
- `exchange_rate` - derived when `currency != CNY`
- `cny_amount` - derived from `amount * exchange_rate`
- `dedup_key` - generated for duplicate protection
- `source` - `manual`, `system`, `broker_sync`, or repair source

Historical FX evidence is technical workflow state stored in local SQLite. It
must never be added to or queried from the Feishu `cash_flow` table.

### nav_history

Purpose: daily NAV facts. Do not use this as a normal manual-entry table.

Manual edit policy:
- Normal writes must go through `pm daily-job --write --confirm` or an explicit
  nav repair command.
- Manual editing is only for explicit repair and should be followed by an audit/reconcile pass.
- Duplicate `(account, date)` rows are considered data corruption. Run `pm nav duplicates --json`; normal NAV writes block until duplicates are repaired.

System-only fields:
- all registered fields are generated or repaired by the system.

### holdings_snapshot

Purpose: per-NAV-date holdings snapshot for audit/replay. This is a system-only table.

Manual edit policy:
- Do not manually edit during normal operation.
- If a snapshot is wrong, repair the source data and regenerate/rewrite the snapshot.

### compensation_tasks

Purpose: optional Feishu mirror for partial multi-table write failures. The same-host source of truth is the append-only `${PM_DATA_DIR}/compensation_tasks.jsonl` event log; every append is process-locked, flushed, and fsync'd before the mirror is attempted. Events fold by `task_id` through `PENDING`, `RUNNING`, `FAILED`, and `RESOLVED`.

Automatic retry only accepts absolute compare-and-set targets of type `HOLDING_TARGET_SET`, `HOLDING_ZERO_DELETE`, `CASH_TARGET_SET`, or `HOLDINGS_SNAPSHOT_TARGET_SET`. Legacy delta payloads remain inspectable with `supported=false` but must not be replayed automatically.

### schema_version

Purpose: track Feishu schema migration status. This is a system-only table.

## Retired Tables

- `price_cache` is no longer an active Feishu table. Price cache operations use local cache storage. Do not create or maintain `price_cache` in Feishu for new setups.
