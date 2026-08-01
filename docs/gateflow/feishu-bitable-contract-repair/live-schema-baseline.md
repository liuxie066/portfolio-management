# Live Feishu Schema Baseline

## Gate

- Gate: plan evidence
- Work unit: feishu-bitable-contract-repair
- Source base: origin/main@59625b0d6c666da338c0a520e85a221932846949
- Observed at: 2026-08-01T22:59:33+08:00
- Access: authorized read-only field metadata
- Business records read: no
- Feishu writes: no
- Tokens/table identifiers stored: no
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/live-schema-baseline.md

## Observed Core Tables

### holdings

| Field | Type | UI type | Observed select options |
|---|---:|---|---|
| asset_id | 1 | Text | |
| asset_name | 1 | Text | |
| asset_type | 3 | SingleSelect | a_stock, cash, otc_fund, other, hk_stock, us_stock, exchange_fund, us_fund, mmf, crypto |
| account | 1 | Text | |
| broker | 1 | Text | |
| quantity | 2 | Number | |
| avg_cost | 2 | Number | |
| currency | 1 | Text | |
| asset_class | 3 | SingleSelect | 美国资产, 另类资产, 中国资产, 现金, 港股资产 |
| industry | 3 | SingleSelect | 金融, AI, 中概, 非行业指数, 区块链, 能源, 消费, 房地产, 半导体, 现金, 科技, 其他 |
| tag | 1 | Text | |
| created_at | 1 | Text | |
| updated_at | 1 | Text | |

### cash_flow

| Field | Type | UI type | Observed select options |
|---|---:|---|---|
| flow_date | 5 | DateTime | |
| account | 1 | Text | |
| amount | 2 | Number | |
| currency | 1 | Text | |
| remark | 1 | Text | |
| cny_amount | 2 | Number | |
| exchange_rate | 2 | Number | |
| source | 1 | Text | |
| flow_type | 3 | SingleSelect | DEPOSIT, WITHDRAW |
| dedup_key | 1 | Text | |
| broker | 1 | Text | |

The optional documented updated_at field is absent.

### nav_history

| Field | Type | UI type |
|---|---:|---|
| date | 5 | DateTime |
| stock_value | 2 | Number |
| cash_value | 2 | Number |
| total_value | 2 | Number |
| stock_weight | 2 | Number |
| cash_weight | 2 | Number |
| shares | 2 | Number |
| nav | 2 | Number |
| cash_flow | 2 | Number |
| share_change | 2 | Number |
| mtd_nav_change | 2 | Number |
| ytd_nav_change | 2 | Number |
| pnl | 2 | Number |
| mtd_pnl | 2 | Number |
| ytd_pnl | 2 | Number |
| account | 1 | Text |
| fund_value | 2 | Number |
| cn_stock_value | 2 | Number |
| us_stock_value | 2 | Number |
| hk_stock_value | 2 | Number |
| details | 1 | Text |

The optional documented updated_at field is absent.

### holdings_snapshot

| Field | Type | UI type |
|---|---:|---|
| as_of | 1 | Text |
| account | 1 | Text |
| asset_id | 1 | Text |
| broker | 1 | Text |
| quantity | 2 | Number |
| currency | 1 | Text |
| price | 2 | Number |
| cny_price | 2 | Number |
| market_value_cny | 2 | Number |
| dedup_key | 1 | Text |
| asset_name | 1 | Text |
| avg_cost | 2 | Number |
| source | 1 | Text |
| remark | 1 | Text |

## Observed Optional Tables

### transactions

The table is configured. Its observed fields are:

- Text: tx_date, tx_type, asset_id, asset_name, asset_type, market, account, currency, remark, dedup_key, request_id
- Number: quantity, price, amount, fee

Notable contract differences:

- tx_type and asset_type are Text, not SingleSelect.
- market exists although it is not in the current schema document.
- broker, source, tax, and related_account are absent.

Because transactions is a legacy read-only archive, the repair plan must model this observed Text contract and must not require a live migration.

### compensation_tasks

Not configured.

### schema_version

Not configured.

## Contract Decisions from Evidence

1. holdings_snapshot.as_of remains Text YYYY-MM-DD; no migration is required.
2. cash_flow.flow_date and nav_history.date remain DateTime/Date wire fields.
3. holdings.asset_type, asset_class, industry and cash_flow.flow_type are precise SingleSelect fields.
4. tag is JSON encoded in Text.
5. cash_flow.updated_at and nav_history.updated_at are optional observed fields and cannot be required for cache or correctness.
6. transactions uses its observed Text archive contract and exposes no mutation contract.
7. compensation_tasks and schema_version remain optional/unconfigured.
8. holdings.asset_type and industry have domain-enum/live-option drift:
   - domain AssetType includes values not present in the live SingleSelect options;
   - live industry includes AI, while the current Industry enum does not;
   - the write contract must fail closed for a value outside observed/approved live options;
   - adding or removing live select options is a separate schema-migration authorization.

## Residual Risks

- Select options can drift after this read. The implementation must support repeatable read-only comparison and report drift.
- No business rows were read, so this artifact does not prove which select options are currently used by records.
- Live schema mutation and option migration remain out of scope.

## Next Gate

plan
