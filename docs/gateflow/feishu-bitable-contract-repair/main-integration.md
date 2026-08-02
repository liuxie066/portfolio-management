# Gateflow Main Integration — Feishu Bitable Contract Repair

- Gate: post-aggregate main integration
- Work unit: feishu-bitable-contract-repair
- Incoming base: `origin/main@cf47cd9`
- Incoming work unit: feishu-dual-app-credentials
- Review: `docs/reviews/code-review-20260802-101238.md`
- Recorded at: 2026-08-02T10:12:38+08:00
- Status: accepted; ready for merge commit and Draft PR
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/main-integration.md`

## Why Integration Was Required

The branch's historical implementation base remained
`59625b0d6c666da338c0a520e85a221932846949`, while current `origin/main` had
advanced through the merged dual-App credential work. A read-only merge-tree
preflight found seven overlapping files and a real conflict in configuration,
so a Draft PR without integration would not have been mergeable.

## Conflict Decisions

### `src/config.py`

Retained current main's role-specific Bitable/conversation credential resolver
and secure systemd credential handling. Retained this work unit's canonical
`parse_table_ref` as the only table-reference parser for deployment validation
and public table lookup. Removed the retired remote price-cache mapping.

### `tests/test_cash_flow_effect_service.py`

Retained current main's conversation-role receipt test. Retained this work
unit's canonical rule that remark-only source changes produce a visible new
cash-flow effect version, along with source and observed-dedup fingerprint
coverage.

## Integration Corrections

- Removed stale `Union` and `datetime` imports left by the automatic client
  merge.
- Made `FeishuCredentialConfigError` non-frozen so standard traceback
  assignment cannot become `FrozenInstanceError`; added direct regression.
- Bound affected credential/client/CLI tests to empty temporary config files so
  developer or production `config.yaml` cannot change deterministic results.
- Removed one incoming historical artifact's extra EOF blank line so the final
  staged diff satisfies the repository check.

## Validation

- Incoming-main integration suite: `248 passed`.
- Merge-focused final suite: `178 passed`.
- Full repository suite: `1415 passed`.
- Scoped Ruff, compileall, generated schema `--check`, migration expectations,
  staged `git diff --check`, and unmerged-entry check: passed.
- Post-integration DeepReview: no material findings.

## Boundaries

- The merge is into the feature branch only; `main` is not mutated.
- No live Feishu/Futu read or write, schema change, business-row repair,
  release, deployment, service change, Ready transition, or reviewer request
  occurred.

## Next Gate

Commit the accepted merge result, push the feature branch, and create a Draft
PR against current main. Then review the actual PR diff and checks before final
closeout.
