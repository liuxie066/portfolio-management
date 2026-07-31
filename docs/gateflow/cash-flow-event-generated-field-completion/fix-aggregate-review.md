# Gateflow Aggregate Review Fix

- Gate: `fix`
- Work unit: `cash-flow-event-generated-field-completion`
- Review artifact: `docs/reviews/code-review-20260731-235839.md`
- Status: `fixed; pending aggregate re-review`

## Finding decision and fix

### DR-AGG-CF-01 — accepted — 已修复

The shared FX-confirmation authority now compares the confirmation's stored
`record_id` with the fresh cash-flow row's `record_id` before checking source
hash, date, rate, CNY amount, and evidence authority. Cross-record evidence is
classified as `fx_confirmation_stale` with `mismatch_field=record_id`.

The frozen FX identity used by semantic attention receipts now also retains the
confirmation record ID. This keeps operator evidence explicit without adding
event IDs, timestamps, or generated-field noise to receipt identity.

## Regression coverage

- otherwise identical evidence from another record is rejected;
- valid exact-record provider/manual-supplement evidence remains accepted;
- event completion and NAV preflight retain their shared-validator behavior;
- complete repository suite remains green.

## Validation

- Focused pytest: `45 passed in 0.85s`
- Full pytest: `993 passed in 19.17s`
- Changed-file Ruff: `All checks passed!`
- `git diff --check`: passed

## Residual risks

- Live Feishu behavior remains separately authorized canary work.
- Unrelated full-repository Ruff baseline remains out of scope.

## Next entry point

`aggregate re-review`
