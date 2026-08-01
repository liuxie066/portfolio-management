# Gateflow Implementation Plan

- Gate: `plan`
- Work unit: `cash-observation-only`
- Branch: `fix/cash-observation-only`
- Base: `origin/main@02ce7f8b549f`
- Goal artifact: `docs/gateflow/cash-observation-only/goal-confirmation.md`
- Artifact path: `docs/gateflow/cash-observation-only/implementation-plan.md`
- Status: `plan review pass-with-risks`
- Plan review: `docs/reviews/plan-review-20260801-104826.md`

## Goal

Remove the invalid Futu-per-currency-to-PM-holdings comparison while preserving
complete Futu observation evidence, one fail-closed aggregate PM CASH quality
fact, and the separately authorized cash-flow-ledger-to-aggregate-CASH workflow.

## Public and internal contract changes

### Provider observation contract

Keep `cash_by_currency`, `cash_source_fields`, `cash_present_by_currency`, and
`source_metadata.cash.mode=per_currency`. The `securities_cash` stage continues
to mean that authoritative source cash fields were read and captured. It never
means those values were written to or matched against holdings.

### Reconciliation contract

Replace `pm.securities_cash` with `pm.cash_aggregate` in full and balance-only
readback receipts.

`pm.cash_aggregate` evaluates only `CNY-CASH` for the requested account and
broker:

- exact holding exists;
- `asset_type == AssetType.CASH`;
- `currency == "CNY"`;
- quantity converts to a finite decimal.

The trusted reason is `AGGREGATE_CASH_STRUCTURALLY_VALID`; invalid/missing rows
use `AGGREGATE_CASH_INVALID`; repository exceptions use
`REPOSITORY_READ_FAILED`. No provider amount participates in this verdict.

Only provider-to-replica datasets (`pm.holdings_quantity`, `pm.cost_basis`, and
`pm.fund_mmf`) trigger the 30-second read-only retry. `pm.cash_aggregate` is
re-evaluated after a retry triggered by another dataset, but never triggers one
itself. An aggregate failure sets reconciliation and `quality_status`
untrusted while preserving the current cash-only non-business-failure behavior.

### Quality and consumer contract

- `NAV_REQUIRED_DATASETS` requires `pm.cash_aggregate` instead of
  `pm.securities_cash`.
- The durable Futu receipt must contain trusted verdicts for holdings quantity,
  cost basis, aggregate cash structure, and MMF before `pm.futu_sync` is trusted.
- `pm.cash_like_assets` combines `pm.cash_aggregate` and `pm.fund_mmf`.
- Official NAV and public freshness endpoints use the same dataset identity.
- Check `PM-CASH-001` now means aggregate PM CASH structural validity.
  `PM-CASH-002` remains source per-currency field completeness and belongs to
  Futu observation evidence, not holdings equality.

Old receipts that only contain `pm.securities_cash` fail closed as missing new
evidence; no compatibility alias may silently reinterpret the old equality
verdict as aggregate structural evidence.

### Effects and receipt contract

Remove both automatic `observe_futu_cash_result()` call sites. Do not delete the
cash-flow effect subsystem: confirmed cash-flow ledger facts may still update
the aggregate CASH row and remain NAV-gated.

Futu receipt CASH text becomes explicit:

```text
CASH / MMF｜富途原币余额仅观测；PM 使用 CNY-CASH 人民币汇总，不做金额对账；MMF 新增 N，更新 N
```

The renderer ignores any legacy `cash_effects` payload and never emits `CASH
Effects` counters or the `pm cash-flow review` action from a Futu receipt.

## Slice S1 — Synchronization boundary and receipts

### Scope

- Replace per-currency cash comparison with aggregate local-structure verdict.
- Restrict retry triggers to provider-replica datasets.
- Preserve Futu per-currency source observation and cash stage evidence.
- Remove both automatic Futu-cash-to-effect bridges.
- Update receipt wording and omit CASH Effects.

### Primary files

- `src/app/futu_sync_reconciler.py`
- `src/app/futu_balance_sync_service.py`
- `src/service/application.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/futu_sync_receipt_service.py`
- corresponding focused tests

### Tests

- Valid `CNY-CASH` trusts `pm.cash_aggregate` even when Futu observations differ.
- `USD-CASH` and `HKD-CASH` reads fail the test if attempted.
- Missing/invalid aggregate CASH is untrusted without a cash-only retry.
- Stock/MMF mismatch still retries and can recover.
- Full and balance-only durable receipts contain `pm.cash_aggregate` and preserve
  per-currency source metadata.
- Real application and sync-first NAV results have no `cash_effects` bridge.
- Futu receipt states the aggregate boundary and never displays CASH Effects or
  its review command, including when a legacy payload contains those fields.

### Validation

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_futu_sync_reconciler.py \
  tests/test_futu_balance_sync_service.py \
  tests/test_futu_sync_evidence.py \
  tests/test_futu_sync_receipt_service.py \
  tests/test_service_application.py \
  tests/test_holdings_nav_preflight_service.py
python3.12 -X pycache_prefix=/tmp/pm_cash_observation_s1 -m compileall -q src
```

## Slice S2 — Quality, public freshness, and documentation

### Scope

- Move quality, NAV policy, and API freshness consumers to
  `pm.cash_aggregate`.
- Update check ownership/wording and current design/operator documentation.
- Retain raw per-currency source evidence and cash-flow-ledger behavior.

### Primary files

- `src/app/quality/policy.py`
- `src/app/quality/service.py`
- `src/service/http.py`
- `tests/test_pm_quality.py`
- `tests/test_service_http.py`
- `docs/quality-monitoring/pm-check-implementation.md`
- `docs/service.md`
- `docs/schema.md`
- `docs/cash-flow-holding-effects.md`
- `docs/cash-flow-effects-runbook.md`
- `README.md`

### Tests

- Quality artifact validates with `pm.cash_aggregate` and no
  `pm.securities_cash`.
- Missing new evidence in an old receipt fails closed.
- Cost basis remains non-NAV-blocking; aggregate CASH and MMF remain blocking.
- `pm.cash_like_assets` combines aggregate CASH with MMF correctly.
- Stale receipt propagation and official NAV write gate use the new dataset.
- `/api/v1` overview, holdings, cash, distribution, and report freshness use
  `pm.cash_aggregate` consistently.

### Validation

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_pm_quality.py \
  tests/test_service_http.py
python3.12 -X pycache_prefix=/tmp/pm_cash_observation_s2 -m compileall -q src
```

## Aggregate validation and review sequence

1. Adversarially review this plan and repair accepted findings before code.
2. Implement S1, run its focused tests and compile check, then DeepReview the
   slice and repair accepted findings.
3. Implement S2, run its focused tests and compile check, then DeepReview the
   slice and repair accepted findings.
4. Run repository-wide reference checks proving no active
   `pm.securities_cash`, `SECURITIES_CASH_MISMATCH`, or Futu cash-effect bridge
   remains outside historical artifacts.
5. Run the full test suite and compile checks, then aggregate DeepReview against
   `origin/main` and repair accepted findings.
6. Stop after local verified delivery. Commit/push, Draft PR, merge, release,
   upgrade, live sync, and NAV History remain separate user-authorized stages.

## Stop conditions

Stop for user direction if implementation requires rewriting existing holdings,
inventing an FX-time conversion rule, removing cash-flow ledger authority, or
changing production/runtime state.

## Residual risks

- The new dataset proves local record shape, not the economic correctness of its
  numeric aggregate; that remains owned by existing ledger/operator controls.
- Historical quality artifacts use the superseded dataset identity and remain
  historical evidence only.
- Production behavior remains unchanged until a separately authorized release
  and upgrade.

## Completion state

- Current gate: `plan review pass-with-risks`.
- Next gate: `implementation S1`.
