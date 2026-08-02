# Gateflow S6 Scope Correction — Public Contract Test Migration

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S6
- Base: `cca91c6`
- Review: `docs/reviews/code-review-20260802-041937.md`
- Status: accepted test-only scope correction
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s6-scope-correction.md`

## Correction

S6 changes two public boundaries: cash-flow queries now consume fresh raw rows,
and every official `persist=True` NAV call requires an explicit run-scoped
dataset. The full-suite review found six pre-existing integration test files
outside the original S6 allowlist that directly exercise those boundaries but
still mock the retired model/cache path or provide a fake portfolio without the
new builder.

The following test-only files are added to S6 fix and verification scope:

- `tests/test_decimal_audit_behavior.py`
- `tests/test_holdings_nav_preflight_service.py`
- `tests/test_nav_cashflow_perf_minimal.py`
- `tests/test_portfolio.py`
- `tests/test_reconcile_audit.py`
- `tests/test_snapshot_and_audit.py`

## Required Migration

- Replace `get_cash_flows`-only stubs with complete `RawCashFlowRecord`
  fresh-reader fixtures.
- Build and pass a matching `CashFlowDatasetSnapshot` in direct official NAV
  tests.
- Add `build_cash_flow_dataset()` to fake top-level portfolios and assert the
  same object reaches `record_nav()`.
- Keep all unit tests fully isolated from external Feishu networking.

## Boundary

This correction changes no production behavior and does not move S8 NAV
invariants or S9/S10 snapshot implementation into S6. In particular, product
code must not regain cache/model fallback or implicit official reads merely to
preserve old fixtures. No live Feishu/Futu request, schema mutation, merge,
release, or deployment is authorized.
