# Gateflow Implementation S1

- Gate: `implementation`
- Work unit: `cash-observation-only`
- Slice: `S1 — synchronization boundary and receipts`
- Branch: `fix/cash-observation-only`
- Base: `origin/main@02ce7f8b549f`
- Plan: `docs/gateflow/cash-observation-only/implementation-plan.md`
- Review: `docs/reviews/code-review-20260801-105243.md`
- Status: `implemented; focused validation and DeepReview pass`

## Implemented contract

- Replaced `pm.securities_cash` readback with `pm.cash_aggregate`.
- Aggregate CASH validates only the local `CNY-CASH` identity, type, currency,
  broker/account, and finite quantity; it never consumes Futu cash amounts.
- Aggregate-only failure does not trigger the 30-second readback retry and does
  not turn an otherwise successful Futu write into a business failure.
- Futu CNY/USD/HKD fields remain validated and persisted as observation evidence.
- Removed automatic Futu cash-effect bridging from both service sync and
  sync-first NAV paths.
- Futu receipts state the aggregate boundary and ignore legacy `cash_effects`
  payloads.

## Validation

```text
93 passed in 1.12s
```

Covered suites:

- `tests/test_futu_sync_reconciler.py`
- `tests/test_futu_balance_sync_service.py`
- `tests/test_futu_sync_evidence.py`
- `tests/test_futu_sync_receipt_service.py`
- `tests/test_service_application.py`
- `tests/test_holdings_nav_preflight_service.py`

`python3.12 -m compileall -q src` also passed with an isolated pycache root.

## Review conclusion

DeepReview found no material issues. Its only residual risk is the intentional
temporary dataset-identity skew before S2, so this branch must not be delivered
between slices.

## Completion state

- Current gate: `S1 pass`.
- Next gate: `implementation S2`.
