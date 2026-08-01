# Gateflow S4 Fix — DeepReview Findings

## Gate Metadata

- Gate: fix
- Slice: S4
- Work unit: feishu-bitable-contract-repair
- Review artifact: `docs/reviews/code-review-20260802-025828.md`
- Recorded at: 2026-08-02T03:17:06+08:00
- Status: all findings fixed; final re-review accepted
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s4-fix.md`

## Finding Decisions

### DR-S4-01 — accepted — fixed

- Cash-flow replay now requires the exact remote row's observed `dedup_key` to
  equal the requested completed fact key before it can return `replayed=True`.
- A mismatched row reached through the local dedup cache evicts that mapping and
  performs one fresh filtered lookup. A matching fresh row is replayed; no match
  proceeds to create; a mismatched fresh lookup response fails closed.
- Regression coverage proves both stale-cache outcomes and prevents a different
  amount/key from being returned as a successful replay.

### DR-S4-02 — accepted — fixed

- Loaded-cache publication now remains inside the completed-fact repository
  boundary and applies the same cent-quantized Decimal addition to daily,
  monthly, yearly, and cumulative values.
- The repository publishes one coherent aggregate payload. Invalid legacy cache
  numerics cause cache invalidation rather than post-write float contamination.
- The `0.10 + 0.20` regression proves hot-cache values equal fresh aggregate
  semantics (`0.3`) in all four dimensions.

### DR-S4-03 — accepted — fixed

- The fingerprint-only amount serializer is now explicitly the historical
  `str(float(cent_quantized_decimal))` contract. Financial validation and CNY
  arithmetic remain Decimal-based.
- Scientific notation therefore remains compatible with already persisted v0
  keys. The regression covers `100000000000000000000.00 -> 1e+20` as well as
  the existing ordinary integer representation.

## Regression Evidence

- Focused cash-flow contract/storage fault suite: `45 passed`.
- Final S4 scoped and compatibility suite: `175 passed`.
- Full repository suite: `1204 passed`.
- Ruff on touched production and clean/new tests, Python compileall, and
  `git diff --check`: passed.
- No live Feishu or Futu request was made.

## Next Gate

Accepted by `docs/reviews/code-review-20260802-031618.md`; commit S4.
