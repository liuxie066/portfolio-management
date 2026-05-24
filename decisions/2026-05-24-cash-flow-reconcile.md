# Cash Flow Reconcile

- Manual `cash_flow` rows only require `flow_date`, `account`, `amount`, `currency`, and optional `remark`.
- System-owned fields are filled by `pm cash-flow reconcile`: `flow_type`, `exchange_rate`, `cny_amount`, `dedup_key`, and `source`.
- The command is dry-run by default. Writing back to Feishu requires `--apply --confirm`.
- Reconcile re-derives generated fields from manual fields on each run. For foreign currency it preserves an existing `exchange_rate` and recalculates `cny_amount` from that rate, so manual amount edits are reflected without replacing the historical FX choice.
- After apply, cash-flow aggregate caches for affected accounts are invalidated so NAV calculation reads fresh Feishu data.
