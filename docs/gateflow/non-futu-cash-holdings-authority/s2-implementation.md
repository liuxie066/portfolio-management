# Gateflow S2 Implementation — Structured NAV Refusal and Receipt

- Work unit: `non-futu-cash-holdings-authority`
- Slice: `S2`
- Gate: `implementation`
- Status: implementation and code review accepted; pending slice commit

## Objective and outcome

Preserved cash-flow dataset refusal facts through the account NAV result and
added receipt rendering that uses structured blocker fields instead of the raw
nested exception string.

## Changed behavior

- `CashFlowDatasetRefusal` carries a stable code, immutable blockers, optional
  diagnostic details, and the existing log-compatible message.
- `AccountNavRecorderService` serializes the typed refusal into `failure` for
  both normal and CLOSED NAV paths while retaining the raw `error`.
- Effect-gate blockers expose broker, currency, and signed amount.
- NAV receipts prefer structured cash-flow facts, distinguish cash-flow
  confirmation from Futu reconciliation, and provide an operator command.
- Unknown cash-flow refusal codes use Run-ID guidance; legacy errors retain the
  generic row path.

## Validation

```text
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_s2_fix python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_summary_service.py tests/test_daily_nav_services.py tests/test_nav_history_receipt_service.py
70 passed in 0.97s

python3.12 -m ruff check src/domain/cash_flow_contracts.py src/app/cash_flow_effect_service.py src/app/account_nav_recorder_service.py src/app/nav_history_receipt_service.py tests/test_cash_flow_summary_service.py tests/test_daily_nav_services.py tests/test_nav_history_receipt_service.py
All checks passed!

git diff --check
pass
```

No NAV, effect, holding, notification, or other live external write was used.

The three findings in `docs/reviews/code-review-20260814-105421.md` were
accepted and fixed; see `s2-review-fix.md`.
