# Gateflow Plan Re-review 2 Fix — Feishu Bitable Contract Repair

## Gate Metadata

- Gate: plan re-review fix
- Work unit: feishu-bitable-contract-repair
- Reviewed target: docs/gateflow/feishu-bitable-contract-repair/implementation-plan.md
- Re-review artifact: docs/reviews/plan-review-20260801-231743.md
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/plan-rereview2-fix.md
- Status: fix complete; pending final plan re-review

## Finding Decision and Fix

### PRR2-01 — accepted — 已修复

- Split maintenance-derived repair from historical base-fact reconstruction.
- Backfill/patch now plan only restricted derived/details patches for one fresh-read unique existing row.
- Identity and total/cash/stock/fund/region base facts are immutable in this work unit; input/remote drift blocks.
- Missing dates, duplicate dates, upsert-create, and base replacement return `historical_evidence_required` with zero write.
- Apply/rollback use a restricted field-patch API plus journal/CAS/readback, never full `write_nav_record(s)`.
- Legacy snapshot evidence is preserved and cannot be upgraded to v2 by maintenance.
- Historical creation/base reconstruction is assigned to a separate work unit requiring date-bound normalized valuation/snapshot evidence.
- Updated both design and implementation plan so their maintenance contracts agree.

## Validation

- Re-read current backfill input/default/write behavior and patch apply/rollback calls.
- Re-inventoried direct NAV writers and classified canonical, CLOSED, derived-only maintenance, and repository transport layers.
- Expanded S8 tests to include CLI entrypoint fail-closed behavior.
- No source implementation or live data was changed in this gate.

## Docs Decision

- Updated the design document and Gateflow implementation plan.
- Prior review artifacts remain immutable.

## Residual Risks

- Historical NAV creation/base reconstruction: assigned to a later explicitly authorized work unit.
- Existing business-row compatibility: assigned to separately authorized read-only pre-deployment audit.
- External protocol canaries and cross-host concurrency retain their classified destinations.
- All residual risks are classified.

## Completion Status

- Fix gate: complete
- Next gate: final plan re-review
