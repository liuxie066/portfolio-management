# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `cash-observation-only`
- Branch: `fix/cash-observation-only`
- Base: `origin/main@02ce7f8b549f`
- Artifact path: `docs/gateflow/cash-observation-only/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `按这个逻辑执行` after confirming that PM stores one FX-converted aggregate CASH value and must not reconcile Futu CASH by currency
- Confirmed at: `2026-08-01 10:46:04 +0800`

## Why this work unit exists

The current Futu readback reconciler compares the provider's CNY, USD, and HKD
cash observations with three holdings identities. PM production holdings instead
store one CNY-denominated aggregate CASH record after FX conversion. Missing
`USD-CASH` and `HKD-CASH` rows are therefore reported as
`SECURITIES_CASH_MISMATCH` even though those rows are outside the chosen PM data
model. The same obsolete assumption is embedded in NAV quality gates, public API
freshness dependencies, Futu-to-cash-effect bridging, and receipt wording.

## Target outcome

Align synchronization and validation with the aggregate PM CASH model:

1. Futu CNY/USD/HKD cash fields remain source observations and are never written
   to holdings.
2. Futu cash values are not compared with PM holdings values, because the two
   representations have different currency semantics.
3. PM quality uses one `pm.cash_aggregate` dataset that checks only the local
   `CNY-CASH` aggregate record contract; it does not claim broker-value equality.
4. Futu synchronization never creates per-currency CASH effects or holdings.
5. Cash-flow ledger effects that deliberately update the aggregate PM CASH row
   remain available and retain their existing confirmation and NAV gates.
6. Futu receipts explain the observation-only boundary and no longer render
   misleading CASH Effects zero counters.

## Success signals

- A valid aggregate `CNY-CASH` row is accepted regardless of the observed Futu
  CNY/USD/HKD values.
- The reconciler does not read `USD-CASH` or `HKD-CASH`, does not emit
  `pm.securities_cash`, and cannot emit `SECURITIES_CASH_MISMATCH`.
- Cash observation alone never causes the 30-second reconciliation retry.
- Full and balance-only Futu results expose `pm.cash_aggregate` with a reason
  code that describes local aggregate structure, not replica equality.
- Successful real Futu synchronization and sync-first NAV no longer call the
  Futu cash-effect observation bridge or attach `cash_effects` counters.
- Quality artifacts, official NAV gates, and `/api/v1` freshness dependencies
  consistently use `pm.cash_aggregate`.
- Futu source receipts continue to preserve complete per-currency source-field
  metadata and the `securities_cash` observation stage remains succeeded.
- Focused tests, full tests, compile checks, and review gates pass without
  writing production data.

## Scope boundary

### In scope

- Futu readback reconciliation datasets and retry selection.
- Aggregate CASH structural verdict and reason codes.
- Futu application/NAV cash-effect bridge removal.
- Futu receipt wording and CASH Effects omission.
- PM quality, NAV policy, and public API freshness dataset identity migration
  from `pm.securities_cash` to `pm.cash_aggregate`.
- Operator and design documentation that currently promises per-currency CASH
  holdings reconciliation.
- Tests for the changed contracts.

### Out of scope

- Changing Futu's raw per-currency source queries or evidence capture.
- Recomputing, correcting, or migrating existing `CNY-CASH` values.
- Creating, deleting, or splitting any production holdings record.
- Removing cash-flow ledger reconciliation or its confirmed aggregate-CASH
  mutation workflow.
- Initializing or migrating the cash-flow-effect database.
- Commit/push, Draft PR, merge, release, remote upgrade, timer changes, service
  restart, live synchronization, NAV History execution, or production writes.

## Contract decisions

- `CNY-CASH` is the only PM aggregate CASH identity required by this work unit.
- Its quantity is already CNY-denominated and is not mathematically derivable
  from the current Futu observation without an explicit FX-time contract.
- Aggregate validation checks presence, exact account/broker identity,
  `asset_type=cash`, `currency=CNY`, and a finite quantity. It does not compare
  quantity with any provider cash field.
- `USD-CASH` and `HKD-CASH` remain legacy/manual model constants but are not
  read, created, updated, or required by Futu synchronization.
- `pm.cash_aggregate` remains NAV-blocking when missing or structurally invalid;
  this preserves fail-closed NAV behavior without making a false replica claim.
- A structural aggregate failure is not expected to converge after a 30-second
  provider readback wait, so it does not trigger retry and does not turn an
  otherwise successful Futu write into a business failure. It does set
  reconciliation/quality status untrusted.

## Blocking open questions

- None. The user selected the aggregate model and explicitly rejected
  per-currency CASH validation.

## Residual risks

- Correctness of the numeric aggregate CASH value still depends on its existing
  ledger/operator authority; this work unit only stops an invalid comparison.
- Historical receipts containing `pm.securities_cash` remain readable artifacts
  but do not satisfy the new current quality contract.
- Remote timer and runtime state stay unchanged until separately authorized
  release and upgrade work.

## Completion state

- Current gate: `goal confirmation pass`.
- Next gate: `plan`.
