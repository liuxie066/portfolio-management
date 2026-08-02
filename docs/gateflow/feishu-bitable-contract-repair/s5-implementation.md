# Gateflow S5 Implementation — Reconciliation, FX Evidence, and Duplicate Gate

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S5
- Base: `8c9266e`
- Recorded at: 2026-08-02T03:45:42+08:00
- Status: accepted after DeepReview and re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s5-implementation.md`

## Scope

Production changes:

- `src/domain/cash_flow_contracts.py`
- `src/feishu/repositories/cash_flow_repository.py`
- `src/app/cash_flow_fx_confirmation.py`
- `scripts/pm.py`

Regression changes:

- `tests/test_cash_flow_contracts.py`
- `tests/test_cash_flow_event_completion_service.py`
- `tests/test_cash_flow_fx_confirmation.py`
- `tests/test_pm_cli.py`

Gate artifacts and reviews are included in the accepted commit. The unrelated
untracked `docs/reviews/code-review-20260801-084655.md` remains excluded.

## Implemented Contract

- `ManualCashFlowFacts` now owns the only expected flow-type and canonical
  dedup derivation. `CashFlowManualDatasetAudit` validates fresh raw rows and
  groups duplicates by that expected key, never the observed `dedup_key`.
- A duplicate group requires at least two distinct record IDs. Every in-scope
  member is blocked; no first-row winner, deletion, merge, or rewrite occurs.
- Reconciliation uses one deterministic sequence: fresh account/global scan,
  manual validation, full-scan duplicate grouping, generated patch plan,
  optional batch update, fresh readback, completed validation.
- Exact-record operations still scan the complete account (or global scope when
  account is unknown), so record filters and tampered observed keys cannot hide
  another canonical duplicate.
- Flow type remains system-owned. A conflict is a proposed patch with no
  observed generated fingerprint; downstream completion remains blocked until
  fresh readback returns the canonical value.
- `cash_flow_generated_fingerprint()` is the only generated-field fingerprint
  authority. It revalidates completed facts and covers version, flow type,
  canonical rate, cent-rounded CNY amount, persisted dedup key, and source.
- FX confirmation matching binds exact record ID, flow date, observed generated
  fingerprint, rate, CNY amount, evidence type, and a canonical traceable
  source. Pending proposals cannot be confirmed.
- Manual rate/date/source completeness, finite positive rate, exact rate date,
  and non-placeholder source are checked before any Feishu update or local
  confirmation write.
- Apply success requires every target row in fresh readback to be unique,
  complete, and patch-free. Stale/missing/duplicate readback returns a stable
  failure and nonzero CLI result.
- Batch and readback faults retain known update impact through
  `partial_write_possible`, and any attempted batch invalidates account
  aggregate cache authority even when the client raises.
- The legacy foreign FX resolver fails closed because its signature cannot
  carry dated traceable evidence; only deterministic CNY rate 1 remains.
- `pm cash-flow duplicates --json` performs only a fresh read-only audit through
  the repository and has no mutation command path.

## Validation

- Final S5 scoped suite: `120 passed`.
- Full repository suite: `1232 passed`.
- Python compileall for touched source/tests: passed.
- Ruff for touched source/tests: passed.
- `git diff --check`: passed.
- No live Feishu, Futu, schema, release, deploy, or business-data write occurred.

## Expected Assertions Closed

- Both members of an expected-key duplicate group are blocked.
- A singleton complete row is promoted normally.
- Exact reconciliation proves a fresh full-account scan and ignores tampered
  observed dedup text for duplicate identity.
- Rate-date mismatch and placeholder source cause zero Feishu update and zero
  local confirmation.
- A changed generated fingerprint invalidates prior confirmation.
- A flow-type conflict stays proposed until fresh readback confirms the patch.
- Stale readback, post-write duplicate, batch timeout, response-count mismatch,
  and readback timeout cannot be reported as completed.

## Residual Risks

- Feishu lacks compare-and-swap. External edits between scan and batch are
  detected by readback but cannot be atomically prevented.
- SQLite retains the historical column name `source_hash` for the generated
  fingerprint. The matcher accepts it as a transport alias; the canonical
  repository result uses `generated_fingerprint`.
- Duplicate resolution is intentionally manual and remains outside S5.

## Review Closure

- Initial DeepReview: `docs/reviews/code-review-20260802-033515.md`.
- First re-review: `docs/reviews/code-review-20260802-034145.md`.
- Second re-review: `docs/reviews/code-review-20260802-034431.md`.
- Fix decisions: `docs/gateflow/feishu-bitable-contract-repair/s5-fix.md`.
- Accepted final re-review: `docs/reviews/code-review-20260802-034542.md`.
- Gate decision: `docs/gateflow/feishu-bitable-contract-repair/s5-rereview.md`.

## Next Gate

Commit accepted S5, then start S6.
