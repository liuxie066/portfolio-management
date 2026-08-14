# Gateflow S2 Fix Artifact — Code Review

- Work unit: `non-futu-cash-holdings-authority`
- Slice: `S2`
- Gate: `fix`
- Review artifact: `docs/reviews/code-review-20260814-105421.md`
- Re-review artifact: `docs/reviews/code-review-20260814-105724.md`
- Status: accepted; no unresolved findings

## Finding decisions and fixes

### DR-S2-01 — accepted — fixed

The receipt formatter now claims ownership only when `failure.code` starts with
`CASH_FLOW_`. All generic daily-job failure dictionaries return to the existing
status/stage/error renderer. Added a generic structured failure regression.

### DR-S2-02 — accepted — fixed

Dataset blockers whose reason begins with `EFFECT_` and lacks nested effect rows
now render effect-gate Run-ID guidance. Only row/field blockers instruct the
operator to edit the Cash Flow table. Added an `EFFECT_GATE_FAILED` regression.

### DR-S2-03 — accepted — fixed

Effect gate projection now reuses `_cash_flow_operations()` and emits
account-specific safe operations. Scalar broker/currency/amount fields are set
only for one matching operation; multi-operation effects retain the complete
safe operation list. Receipt rendering consumes the account-scoped operation
and counts additional operations. Added A/B correction projection coverage.

## Validation after fixes

```text
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_s2_fix python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_summary_service.py tests/test_daily_nav_services.py tests/test_nav_history_receipt_service.py
70 passed in 0.97s

python3.12 -m ruff check src/domain/cash_flow_contracts.py src/app/cash_flow_effect_service.py src/app/account_nav_recorder_service.py src/app/nav_history_receipt_service.py tests/test_cash_flow_summary_service.py tests/test_daily_nav_services.py tests/test_nav_history_receipt_service.py
All checks passed!

git diff --check
pass
```

No live external state was read or changed.
