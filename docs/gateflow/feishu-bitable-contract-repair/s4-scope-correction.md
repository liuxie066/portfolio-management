# Gateflow S4 Scope Correction — Complete Cash-flow Test Fixtures

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S4
- Base: `8ef9c8c`
- Status: accepted test-only scope correction
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s4-scope-correction.md`

## Correction

S4 makes `CompletedCashFlowFacts` the only aggregation and direct-write input.
The full suite identified two existing tests outside the original S4 list that
directly instantiate those affected public boundaries with partial `CashFlow`
objects. Their fixtures previously omitted broker, exchange rate, dedup key,
and source while treating the rows as aggregation-safe facts.

The following test-only files are added to the S4 verification scope:

- `tests/test_nav_cashflow_perf_minimal.py`: construct registry-complete rows
  for aggregate preload and pass `CompletedCashFlowFacts` to direct add.
- `tests/test_portfolio.py`: make NAV-summary fixtures represent validated,
  completed cash-flow rows instead of relying on model defaults.

## Boundary

These updates change no production behavior and do not broaden S4 into NAV
orchestration. They preserve the original scenarios while making their test
facts obey the same cash-flow contract as production. No live Feishu/Futu
request, schema mutation, merge, release, or deployment is authorized.
