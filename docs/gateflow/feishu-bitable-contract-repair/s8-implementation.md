# Gateflow S8 Implementation — Canonical NAV Calculation and Safe Maintenance

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S8
- Base: `6cbe411`
- Recorded at: 2026-08-02T06:03:21+08:00
- Status: implementation and accepted fixes complete; pending re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s8-implementation.md`

## Scope

The implementation follows the accepted S8 allowlist plus the bounded
production additions recorded in `s8-scope-correction.md`. The unrelated
untracked `docs/reviews/code-review-20260801-084655.md` remains excluded and
untouched.

## Implemented Contract

- `NavCalculator.project_valuation()` is the runtime-to-persisted valuation
  authority. Runtime stock is equity-only; persisted `stock_value` is complete
  non-cash and already includes the `fund_value` subset. Component totals and
  weights are derived at persisted precision, and runtime total drift fails.
- Canonical NAV records persist `cash_flow` as the target day's flow. Gap flow,
  previous NAV date, `(previous, target]` window semantics, dataset contract
  version, and both dataset fingerprints live in `details.cash_flow_basis`.
- `assert_nav_invariants()` runs after all mapping. It checks finite values,
  total decomposition, fund subset, weights, shares/NAV, share change, daily
  and gap flow semantics, daily/month/year PnL and returns, cumulative PnL,
  dataset fingerprints, and the canonical finality writer/status vocabulary.
- `ClosedNavTarget` requires finite explicit Decimal components, exact raw and
  persisted-precision decomposition, positive total, and owns shares=0/nav=1.
  The CLOSED path uses the same S6 dataset/basis and validates before preview
  or repository write. No public/service default can manufacture stock or cash.
- Repair/backfill fresh-read complete Feishu rows with Missing/Null/Value
  envelopes. Input rows identify dates and may assert immutable identity/base
  evidence; they cannot create a missing/upsert row or replace base facts.
- Every maintenance candidate is recomputed through the canonical NAV service
  with an explicit fresh S6 `CashFlowDatasetSnapshot`. There is no maintenance
  cache fallback or implicit ledger scan. The fresh NAV working series is
  passed as an immutable service input instead of being published into the
  shared cache. Regional/base projection drift and missing ledger evidence fail
  closed.
- Maintenance details retain valuation finality, valuation run identity,
  legacy evidence, and prior dataset receipts while updating stable calculation
  evidence. Maintenance provenance separately records the repair identity and
  stable dataset fingerprints. Volatile fetch timestamps are excluded from the
  target plan, so a fresh recomputation yields the same plan digest when source
  facts are unchanged.
- Repository maintenance exposes one restricted derived/details patch. It
  rejects base columns and a manufactured v2-complete snapshot claim, keeps
  state envelopes through journal/rollback, fresh-reads after each mutation,
  and rejects non-null values lost during canonical parsing before publishing
  cache authority.
- Patch and backfill share the journal/CAS workflow. The journal contains all
  immutable base states, calculation dependencies, complete original/desired
  maintenance states, and only the actually changed write subset. Base drift,
  dependency/identity drift, any maintenance-field drift, successor
  inconsistency, readback mismatch, and changed resume digest block before
  further writes.
- Default changed-scope validation recomputes the immediate non-target
  successor against the post-plan working series. A mismatch blocks apply
  before a journal or Feishu mutation.

## Validation

- Exact S8 suite: `89 passed`.
- Expanded NAV/finality regression: `133 passed`.
- Full repository suite: `1297 passed`.
- Scoped Ruff, Python compile, and `git diff --check`: passed.
- Regression coverage includes daily-versus-gap weekend flow, valuation/fund
  mapping, CLOSED rejection, ledger evidence failure, fresh parse loss,
  incomplete/duplicate history dependencies, immutable-base and complete-state
  CAS drift, successor validation, partial apply/resume, and Missing/Null/Value
  rollback.
- No live Feishu/Futu read or write, schema mutation, business-data repair,
  merge, release, or deployment occurred.

## Expected Assertions Closed

- stock=700 equity + fund=100 + cash=200 persists noncash=800 and total=1000.
- Friday NAV plus weekend flow plus Monday no flow persists daily=0 and records
  the positive weekend gap in the basis.
- Missing/duplicate targets, base drift, upsert creation, unavailable ledger
  evidence, and invalid successors produce zero business writes.
- A partial backfill resumes through the generated dates-only patch request
  with the same canonical plan digest.
- Rollback changes only fields recorded as changed and retains legacy evidence.

## Residual Risks

- Feishu field clearing has no distinct wire operation for physical Missing
  versus present Null. The journal preserves the observed state, but readback
  depends on Feishu returning a cleared field consistently.
- Fresh-read plus same-host locks cannot provide a remote atomic compare-and-
  swap against a concurrent external editor. Base evidence and post-write
  readback detect observed drift but cannot eliminate a race after readback.
- This slice repairs only derived fields. Historical base reconstruction and
  live history repair remain separately authorized work units.

## Next Gate

Run a fresh DeepReview over the complete uncommitted S8 diff and accepted-fix
artifact. Obtain a no-findings re-review before creating the scoped local S8
commit.
