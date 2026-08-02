# Gateflow S5 Fix — DeepReview Findings

## Gate Metadata

- Gate: fix
- Slice: S5
- Work unit: feishu-bitable-contract-repair
- Review artifact: `docs/reviews/code-review-20260802-033515.md`
- Recorded at: 2026-08-02T03:40:20+08:00
- Status: all findings fixed; final re-review accepted
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s5-fix.md`

## Finding Decisions

### DR-S5-01 — accepted — fixed

- Apply success is now derived from fresh readback completion, not transport
  return. Every target row must be unique, error-free, patch-free, and expose an
  observed generated-field fingerprint.
- A stale/missing/duplicate readback returns `success=false`, stable
  `cash_flow_readback_not_verified`, and a nonzero CLI result. A legitimate
  empty account-scope remains a successful no-op; a missing exact record does
  not.
- Results retain `change_count`, `updated_count`, row blockers, and
  `partial_write_possible` so failure does not erase mutation impact evidence.
- Regression coverage proves stale readback and a concurrent post-write
  expected-key duplicate cannot be reported as completed.

### DR-S5-02 — accepted — fixed

- Once a nonempty batch request is attempted, affected account memory and local
  aggregate caches are invalidated in `finally`, including timeout/error paths.
- An exception returns stable `cash_flow_batch_update_failed` with
  `partial_write_possible=true`; a response-count mismatch returns the separate
  `cash_flow_batch_update_count_mismatch` state. Neither path records FX
  confirmation or claims a completed readback.
- Fault injection verifies timeout-after-send clears all loaded cache
  authority and preserves the partial-write warning.

### DR-S5-03 — accepted — fixed

- Added one domain authority, `normalize_cash_flow_rate_source()`, shared by
  reconciliation and confirmation evaluation.
- The validator trims valid evidence and rejects blank/non-text sources plus
  known placeholders including `manual`, `unknown`, `n/a`, `na`, `none`,
  `null`, `-`, `tbd`, `todo`, and `placeholder`.
- Manual source rejection occurs before any Feishu scan/update. The CLI records
  no local confirmation, and an imported/direct confirmation with a placeholder
  source fails the downstream evidence gate.

### DR-S5-RR1 — accepted — fixed

- A post-write scan or plan exception now returns stable
  `cash_flow_readback_failed`, the known `updated_count`, and conservative
  `partial_write_possible`; it never escapes as an evidence-free raw failure.
- The manual FX CLI short-circuits on any repository `success=false` before it
  inspects rows or attempts confirmation, preserving the repository reason and
  mutation-impact fields in JSON and the nonzero exit code.
- The CLI also invokes the shared source validator before constructing storage,
  so a placeholder source performs no Feishu access and no confirmation write.
- Fault coverage injects a successful batch followed by readback timeout and
  proves the known update impact survives end to end.

### DR-S5-RR2 — accepted — fixed

- The compatibility `_resolve_cash_flow_exchange_rate()` retains only the
  deterministic CNY identity rate of 1.
- Every foreign-currency invocation now fails closed with a stable instruction
  to use the date/source-bound reconciliation contract. It no longer derives a
  rate from `cny_amount / amount` or accepts an undated bare rate cache.
- Regression coverage proves the compatibility facade cannot reintroduce a
  second FX authority.

## Regression Evidence

- Final S5 focused suite: `120 passed`.
- Final full repository suite: `1232 passed`.
- Scoped Ruff, Python compileall, and `git diff --check`: passed.
- No live Feishu or Futu request was made.

## Next Gate

Accepted by `docs/reviews/code-review-20260802-034542.md`; commit S5.
