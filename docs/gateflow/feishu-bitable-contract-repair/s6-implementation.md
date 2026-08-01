# Gateflow S6 Implementation — Run-scoped Cash-flow Dataset

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S6
- Base: `cca91c6`
- Recorded at: 2026-08-02T04:14:15+08:00
- Status: accepted after DeepReview and re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s6-implementation.md`

## Scope

Production changes are limited to the S6 allowlist:

- `src/domain/cash_flow_contracts.py`
- `src/app/cash_flow_summary_service.py`
- `src/app/cash_flow_effect_service.py`
- `src/app/daily_nav_job_service.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/nav_initialization_service.py`
- `src/app/nav_record_service.py`
- `src/portfolio.py`
- `skill_api.py`

Regression changes are limited to the five S6 test files. The unrelated
untracked `docs/reviews/code-review-20260801-084655.md` remains excluded.

## Implemented Contract

- `CashFlowDatasetSnapshot` is the immutable run-scoped handoff for one
  account, NAV date, run ID, inclusive aggregation window, raw source set,
  completed facts, blockers, aggregates, fingerprints, FX evidence, and effect
  gate revision.
- `derive_cash_flow_dataset_rows()` is the sole raw-to-completed row derivation
  authority. Both the builder and the official-write integrity assertion use
  it, including duplicate source record IDs, expected-key duplicates, manual
  validation, account scope, and completed-field validation.
- `CashFlowSummaryService.build_dataset()` is the only storage-backed dataset
  builder. It performs one fresh complete account read and does not consult the
  historical aggregate cache.
- Financial and full fingerprints sort by record ID and preserve Missing,
  Null, and Value as distinct states. The snapshot validates its raw-row
  fingerprints, raw-to-completed derivation, source blockers, audit-only set,
  aggregates, FX evidence fingerprint, and effect-gate scope before a write.
- Rows from `start_year-01-01` through `nav_date` contribute to totals. Valid
  out-of-window rows are retained as audit-only evidence; an unknowable date or
  any other invalid source fact blocks official authority.
- `AccountNavRecorderService`, `NavInitializationService`, and the CLOSED
  compatibility entrypoint each resolve the run/date once, build one dataset at
  their approved top-level boundary, and pass the same object through
  `PortfolioManager` to `NavRecordService`.
- `DailyNavJobService` no longer runs an independent cash-flow reconciliation
  pre-scan. `CashFlowEffectService` consumes the dataset's completed rows and
  performs no second cash-flow storage read for the gate.
- Persisting or previewing an official NAV fails closed without a complete
  dataset matching account, NAV date, run ID, window, fingerprints, completed
  derivation, FX evidence, and embedded effect revision.
- NAV details persist dataset contract version, financial/full fingerprints,
  fetch time, inclusive window, FX evidence fingerprint, effect revision, and
  run ID.
- Nonofficial summary queries build a fresh independent dataset. The old
  aggregate cache remains outside official and summary authority.
- `PortfolioSkill.close_nav()` delegates through
  `AccountNavRecorderService.record_closed()` and performs no direct NAV
  repository write. S8 remains responsible for the final CLOSED calculation
  invariant and removal of compatibility value inference.

## Validation

- S6 scoped suite: `111 passed`.
- Expanded S6/failure-set regression: `154 passed`.
- Python compileall for touched source: passed.
- Ruff for touched source/tests: passed; `skill_api.py` was checked while
  ignoring its pre-existing `E402` and `F541` findings.
- `git diff --check`: passed.
- Full repository suite: `1238 passed` with `FEISHU_APP_TOKEN` unset and dummy
  app credentials. The DeepReview-approved test-only scope correction migrated
  every affected old integration fixture without adding a product fallback.
- No live Feishu, Futu, schema, release, deploy, or business-data write
  occurred.

## Expected Assertions Closed

- Old cache preload cannot hide a later add, edit, or delete.
- Precheck, effect gate, summary, and NAV write share one object/fingerprint.
- Daily, initialization, manual/service, and CLOSED paths build once at their
  approved boundary.
- Mismatched account/date/run/window/effect revision blocks.
- Missing date blocks and valid future rows do not affect current totals.
- Repeated record IDs cannot double-count, and completed rows cannot detach
  from the fingerprinted raw source.
- `skill_api.py` makes zero direct `write_nav_record` calls for CLOSED.

## Residual Risks Before Review

- Feishu has no cross-system transaction with an external editor; readback and
  fingerprint evidence detect, but cannot atomically prevent, later edits.
- S8 owns the canonical CLOSED target and final NAV calculation invariants.
- S9/S10 own normalized replayable snapshot persistence and exact-set recovery.

## Review Closure

- Initial DeepReview: `docs/reviews/code-review-20260802-041937.md`.
- Fix decisions: `docs/gateflow/feishu-bitable-contract-repair/s6-fix.md`.
- Scope correction: `docs/gateflow/feishu-bitable-contract-repair/s6-scope-correction.md`.
- Accepted re-review: `docs/reviews/code-review-20260802-042659.md`.
- Gate decision: `docs/gateflow/feishu-bitable-contract-repair/s6-rereview.md`.

## Next Gate

Commit accepted S6, then start S7.
