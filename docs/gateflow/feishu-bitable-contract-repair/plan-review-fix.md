# Gateflow Plan Review Fix — Feishu Bitable Contract Repair

## Gate Metadata

- Gate: plan review fix
- Work unit: feishu-bitable-contract-repair
- Reviewed target: docs/gateflow/feishu-bitable-contract-repair/implementation-plan.md
- Review artifact: docs/reviews/plan-review-20260801-230933.md
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/plan-review-fix.md
- Status: fix complete; pending re-review

## Finding Decisions and Fixes

### PR-01 — accepted — 已修复

- Assigned the only storage-backed `CashFlowDatasetSnapshot` builder to `CashFlowSummaryService`.
- Added `NavInitializationService` to S6 ownership and made it generate/pass an explicit run_id and dataset.
- Defined `cash_flow_dataset` propagation through PortfolioManager/NavRecordService and fail-closed compatibility checks.
- Defined the common `[config.start_year-01-01, nav_date]` window and explicit repair/backfill dataset requirement.
- Added entrypoint assertions for manual/service/skill, daily, and initialization flows.

### PR-02 — accepted — 已修复

- Selected an explicit transmission design: `ValuationService` constructs `NormalizedValuationSnapshot`; `PortfolioValuation` is a compatibility projection only.
- `PortfolioReadService` returns both, and official record/init flows pass both through the NAV boundary.
- Added the missing NAV, portfolio, account-recorder, initialization, and tests to S9's allowed scope.
- Added a mismatch test that rejects a mutated compatibility projection.

### PR-03 — accepted — 已修复

- Added a minimal immutable `SnapshotWriteAuthority` scoped to account/as_of/run_id/issuer/target digest.
- Defined plan binding and fsynced target/plan digests before the first NAV mutation.
- Added propagation ownership for account record, initialization, PortfolioManager, and NavRecordService.
- Restricted compensation reuse to an exact bound authority and added zero-write mismatch tests.

### PR-04 — accepted — 已修复

- Corrected duplicate semantics to require at least two distinct in-scope record_ids in an expected-dedup group.
- Added singleton-pass, full-account-scan, tampered-observed-dedup, and duplicate-all-blocked assertions.

## Validation

- Plan/source call paths rechecked for manual/service/skill, daily, initialization, repair/backfill, NAV, valuation, snapshot, and compensation boundaries.
- No source implementation was changed in this gate.
- No live business records were read and no Feishu write/schema mutation was performed.

## Docs Decision

- Revised only the Gateflow implementation plan.
- Historical review artifact remains immutable.

## Residual Risks

- Existing business-row compatibility: assigned to a separately authorized read-only pre-deployment conformance audit.
- Feishu Number/null/exact-set wire behavior: assigned to separately authorized nonproduction canaries.
- External-editor race and cross-host coordination: assigned to a later concurrency work unit.
- All residual risks are classified; none changes the source implementation strategy.

## Completion Status

- Fix gate: complete
- Next gate: plan re-review
