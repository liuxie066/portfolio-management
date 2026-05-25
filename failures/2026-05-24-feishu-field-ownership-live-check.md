# Feishu field ownership live-check failure

Do not ask operators to maintain generated fields such as `dedup_key`,
`source`, `flow_type`, `cny_amount`, `exchange_rate`, NAV metrics, or holdings
snapshot valuation columns.

That increases manual-entry friction and risks inconsistent NAV calculations.
