# Gateflow S6 Fix — DeepReview Findings

## Gate Metadata

- Gate: fix
- Slice: S6
- Work unit: feishu-bitable-contract-repair
- Review artifact: `docs/reviews/code-review-20260802-041937.md`
- Recorded at: 2026-08-02T04:19:37+08:00
- Status: all findings fixed; final re-review accepted
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s6-fix.md`

## Finding Decisions

### DR-S6-01 — accepted — fixed

- Bind every official effect gate to the exact dataset financial fingerprint
  consumed by the scan.
- Treat a missing or mismatched binding as an immutable dataset blocker, and
  re-check it at the downstream official assertion.
- Add stale-gate fault coverage where account/date/revision look valid but the
  source fingerprint belongs to another dataset.
- `CashFlowEffectService.nav_gate()` now returns the exact consumed dataset
  fingerprint. The builder creates a stable
  `EFFECT_SOURCE_FINGERPRINT_MISMATCH` blocker for missing/mismatched evidence,
  and `assert_official_scope()` independently checks the binding.

### DR-S6-02 — accepted — fixed

- Deep-freeze nested raw field values at `RawCashFlowRecord` construction.
- Ensure callers cannot mutate nested values through the original payload or a
  canonical projection after the dataset fingerprint is created.
- `RawCashFlowRecord` now recursively freezes mappings/sequences and returns a
  detached thawed projection. Regression coverage mutates both the original
  nested input and a returned projection without changing the stored row.

### DR-S6-03 — accepted — fixed

- Apply the test-only scope correction in
  `docs/gateflow/feishu-bitable-contract-repair/s6-scope-correction.md`.
- Migrate official callers to explicit matching datasets, nonofficial callers
  to complete raw fixtures, and fake top-level portfolios to one dataset
  builder/handoff.
- Restore the full suite without adding product compatibility fallbacks.
- Six integration fixtures now use raw fresh-read rows or explicit datasets.
  Unit tests no longer fall through to a real Feishu client.

## Regression Evidence

- Final S6 scoped suite: `111 passed`.
- Expanded S6/failure-set regression: `154 passed`.
- Full repository suite with `FEISHU_APP_TOKEN` unset and dummy app
  credentials: `1238 passed`.
- Scoped Ruff, Python compileall, and `git diff --check`: passed.
- No live Feishu or Futu request was made.

## Next Gate

Accepted by `docs/reviews/code-review-20260802-042659.md`; commit S6.
