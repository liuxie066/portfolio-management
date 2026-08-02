# Gateflow Plan Re-review Fix — Feishu Bitable Contract Repair

## Gate Metadata

- Gate: plan re-review fix
- Work unit: feishu-bitable-contract-repair
- Reviewed target: docs/gateflow/feishu-bitable-contract-repair/implementation-plan.md
- Re-review artifact: docs/reviews/plan-review-20260801-231403.md
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/plan-rereview-fix.md
- Status: fix complete; pending final plan re-review

## Finding Decision and Fix

### PRR-01 — accepted — 已修复

- Added `SkillAPI.close_nav` to the official NAV writer inventory.
- S6 now removes its direct repository write, delegates through `AccountNavRecorderService.record_closed`, and constructs the same run-scoped cash-flow dataset.
- Corrected ownership of the redundant daily reconcile scan to `DailyNavJobService._cash_flow_blocker` and added a zero-extra-scan assertion.
- S8 now owns one Decimal `ClosedNavTarget` invariant and forbids independent default/rounding formulas.
- S9 represents CLOSED as a normalized valuation with zero holding rows and explicit manual cash/noncash components.
- S10 applies the same scoped authority and exact-set state machine with an empty target-date holdings snapshot set.
- Expanded S9/S10 exact validation commands to include the newly affected NAV/daily tests.

## Prior Finding Finalization

- PR-01: expected to move from 部分修复 to 已修复 after final re-review.
- PR-02: expected to move from 部分修复 to 已修复 after final re-review.
- PR-03: expected to move from 部分修复 to 已修复 after final re-review.
- PR-04: remains 已修复.

## Validation

- Re-inventoried all direct NAV repository mutation calls.
- `skill_api.py` is the only public direct writer outside repository/maintenance paths and is now explicitly assigned to S6/S9/S10.
- Plan exact commands now cover the affected close/NAV/daily tests.
- No source implementation or live data was changed in this gate.

## Docs Decision

- Revised the implementation plan and added this Gateflow fix artifact.
- Prior review artifacts remain immutable.

## Residual Risks

- Existing business-row compatibility: assigned to separately authorized read-only pre-deployment audit.
- External wire precision/null/delete semantics: assigned to separately authorized nonproduction canaries.
- External-editor/cross-host race: assigned to later concurrency work.
- All residual risks are classified.

## Completion Status

- Fix gate: complete
- Next gate: final plan re-review
