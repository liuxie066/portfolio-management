# Feishu Manual Field Ownership

- `docs/schema.md` is the canonical operator-facing Feishu field dictionary and now includes field ownership: manual, system, or system-only.
- `cash_flow` is defined as a manual-minimal table: users maintain `flow_date`, `account`, `amount`, `currency`, and optional `remark`; system/reconcile logic owns `flow_type`, `cny_amount`, `exchange_rate`, `dedup_key`, and `source`.
- `holdings` remains the main manual-maintained current-state table.
- `transactions` is correction-tolerant but should usually be written through CLI/service because it has cross-table side effects.
- `nav_history`, `holdings_snapshot`, `compensation_tasks`, and `schema_version` are system-only except explicit repair flows.
- `price_cache` is retired from active Feishu schema because runtime price cache uses local storage.
