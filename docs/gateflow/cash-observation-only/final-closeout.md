# Gateflow Final Closeout

- Work unit: `cash-observation-only`
- Branch: `fix/cash-observation-only`
- Base: `origin/main@02ce7f8b549f`
- Status: `implementation complete; source-control delivery authorized`
- Aggregate review: `docs/reviews/code-review-20260801-110017.md`

## Outcome

The implementation now matches the confirmed aggregate CASH model:

- Futu CNY/USD/HKD cash remains complete source observation evidence.
- No Futu cash amount is compared with PM holdings.
- Futu synchronization reads no `USD-CASH` or `HKD-CASH` record and creates no
  per-currency holding or CASH Effect.
- `pm.cash_aggregate` checks only the local `CNY-CASH` aggregate record contract.
- Cash-only aggregate structure does not trigger the 30-second retry or turn a
  completed provider write into a false rollback result.
- NAV quality policy and all public freshness consumers use the same new
  dataset identity and old receipts fail closed.
- Futu receipts explain the boundary and never show obsolete CASH Effects zero
  counters.
- The separate cash-flow-ledger effect workflow remains explicitly confirmed
  and NAV-gated; Futu drift no longer feeds it automatically.

## Final validation

- Full pytest: `1018 passed in 7.19s`.
- Touched-file Ruff: `All checks passed!`.
- `python3.12 -m compileall -q src scripts skill_api.py`: passed.
- `git diff --check`: passed.
- Active-source reference checks found no `pm.securities_cash`,
  `SECURITIES_CASH_MISMATCH`, or Futu bridge caller. The sole old dataset
  occurrence is an intentional version-skew regression fixture.
- S1 DeepReview: no material findings.
- S2 DeepReview: no material findings.
- Aggregate DeepReview: no material findings.

## Delivery boundary

The shared source checkout already contained unrelated uncommitted work, so this
work unit was implemented and validated in the isolated clone:

```text
/private/tmp/pm-cash-observation-only.iwdfiL/repo
```

At the local implementation checkpoint, no commit, push, PR, merge, version
change, release, remote upgrade, service restart, live synchronization, NAV
History execution, or production write had been performed. The user subsequently
authorized scoped commit, push, and merge to `main`. Version changes, release,
remote upgrade, service restart, live synchronization, NAV History execution,
and production writes remain separate authorization boundaries.

## Residual risk

- The aggregate verdict proves record structure, not the economic correctness
  of the stored CNY amount.
- A future deployment needs a new full Futu sync receipt before the new NAV
  quality contract can pass.
- Historical broker reconciliation records remain audit/recovery facts; new
  Futu synchronization does not generate or advance them.
