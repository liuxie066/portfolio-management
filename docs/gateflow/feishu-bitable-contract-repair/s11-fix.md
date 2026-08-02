# Gateflow S11 Fix Artifact — Transactions Strict Read-Only Archive

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S11
- Review artifact: `docs/reviews/code-review-20260802-090110.md`
- Base: `803bf3b`
- Recorded at: 2026-08-02T09:02:24+08:00
- Status: all accepted findings fixed; pending aggregate re-review

## Finding Decisions and Fixes

### DR-S11-01 — accepted — fixed

The runtime registry already owned the corrected transactions business key
`(account, request_id)`, but its generated `docs/schema.md` projection still
published `request_id` alone and the repository generator check returned
failure.

`docs/schema.md` was added as a bounded S11 scope correction and regenerated
with `scripts/generate_feishu_schema_docs.py`; the generated transactions block
now reports both account and request id. No generated line was edited by hand
and the human policy section was preserved.

Because the projection was stale before S11, the same deterministic generator
also published the already-accepted S10 registry clearability for snapshot
`asset_name`, `avg_cost`, and `source`. Those three mechanical lines do not add
new S11 semantics; retaining them is required for the generated block to be a
zero-drift projection of the unique registry.

## Bounded Scope Expansion

- `docs/schema.md`: generated registry projection only.
- No production module, live schema, runtime configuration, or business data
  was changed by this fix.
- The unrelated untracked
  `docs/reviews/code-review-20260801-084655.md` remains excluded and untouched.

## Validation

- `python3.12 scripts/generate_feishu_schema_docs.py --check`: passed.
- Exact S11 suite: `111 passed`.
- S11 plus schema-check suite: `119 passed`.
- Full repository suite: `1360 passed`.
- Scoped Ruff: passed.
- Python compileall: passed.
- `git diff --check`: passed.
- No live Feishu/Futu reads or writes, schema mutation, historical repair,
  merge, release, or deployment occurred.

## Next Gate

Run aggregate DeepReview re-review over the complete uncommitted S11 diff from
`803bf3b`, including the plan, implementation, review, fix artifact, and
generated schema projection. S11 may be locally committed only after the
re-review reports no findings.
