# Gateflow S5 Re-review — Reconciliation, FX Evidence, and Duplicate Gate

- Gate: re-review
- Work unit: feishu-bitable-contract-repair
- Slice: S5
- Base: `8c9266e`
- Status: accepted
- Final review artifact: `docs/reviews/code-review-20260802-034542.md`
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s5-rereview.md`

## Review Chain

- `docs/reviews/code-review-20260802-033515.md`: three findings, accepted and fixed.
- `docs/reviews/code-review-20260802-034145.md`: one incomplete failure-surface finding, accepted and fixed.
- `docs/reviews/code-review-20260802-034431.md`: one legacy resolver finding, accepted and fixed.
- `docs/reviews/code-review-20260802-034542.md`: no actionable findings.

## Acceptance Decision

S5 is accepted. Fresh manual facts and the expected canonical dedup key are the
only duplicate identity authority. Exact-record operations cannot narrow away
other account rows, and every duplicate member remains blocked.

Generated-field proposals are distinct from observed completed facts. Only a
fresh unique readback can expose the fingerprint used by local FX confirmation;
invalid or stale evidence cannot be confirmed or consumed by NAV/effect gates.

All attempted writes invalidate aggregate cache authority. Batch/readback
failure, nonconvergence, missing rows, and concurrent duplicate appearance
return explicit failure plus conservative mutation-impact evidence. The new
duplicates command is read-only.

## Verification

- 120 final S5 tests passed.
- 1232 full repository tests passed.
- Compile, scoped lint, and diff hygiene passed.
- No live external read or mutation was performed.

## Next Gate

Commit the accepted S5 scope, then start S6 from that commit.
