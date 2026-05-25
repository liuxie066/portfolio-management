# Feishu field ownership live check

Decision: keep the live Feishu schema ownership split as:

- `holdings`: manual source of current positions.
- `cash_flow`: manually enter only `flow_date`, `account`, `amount`, `currency`, and optional `remark`; generated fields are filled by reconcile.
- `nav_history` and `holdings_snapshot`: system-only output tables.
- `transactions`: optional capability table, not required for daily NAV.

The live core schema is compatible with code expectations. Optional
`compensation_tasks` and `schema_version` remain unconfigured and non-blocking.
