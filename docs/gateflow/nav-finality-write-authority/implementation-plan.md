# Gateflow Implementation Plan

- Gate: `plan`
- Work unit: `nav-finality-write-authority`
- Branch: `fix/nav-finality-write-authority`
- Base: `origin/main@81e82e2`
- Goal artifact: `docs/gateflow/nav-finality-write-authority/goal-confirmation.md`
- Artifact path: `docs/gateflow/nav-finality-write-authority/implementation-plan.md`
- Status: `revised after plan review`

## Goal

Make NAV finalization explicit, make existing-row decisions finality-aware, serialize all authoritative NAV mutations, and make every public write surface fail closed by default without introducing a Feishu schema migration.

## Contract decisions

### 1. Versioned finality details

Use the existing `NAVHistory.details` JSON field:

```json
{
  "finality": {
    "version": 1,
    "status": "final|manual|initial|closed|maintenance",
    "nav_date": "YYYY-MM-DD",
    "valuation_as_of": "ISO-8601 or null",
    "writer": "daily-nav-job|nav-record|daily-report|init-nav|close-nav|nav-repair",
    "write_reason": "stable machine-readable reason",
    "run_id": "optional trace id"
  }
}
```

Only a supported contract version, `status=final`, matching `nav_date`, and absence of snapshot recovery state qualify for idempotent `skipped_existing_nav`. `writer` is mandatory provenance but is not an eligibility discriminator, so a validated repair may honestly finalize a row without pretending to be `daily-nav-job`.

Do not add or infer `quote_trading_date`; current pricing/valuation objects do not preserve provider-observed quote dates. The contract records the target NAV date and valuation timestamp honestly.

### 2. Existing-row state machine

For `overwrite_existing=False`:

```text
no row
  -> run canonical writer
row + persisted snapshot failure marker or unresolved compensation task
  -> recovery_required
row + eligible finality contract
  -> skipped_existing_nav
row + missing/mismatched/non-final finality
  -> existing_nav_not_final (blocking, no write)
```

`existing_nav_not_final` must include record id, current finality details, target date, and an operator explanation. It must be included in daily-job blocker statuses and receipt rendering. Snapshot recovery takes precedence: `snapshot_persisted is False`, `snapshot_status == failed`, or an unresolved compensation task returns `recovery_required`; absence of explicit snapshot fields remains the successful steady state and does not require a data migration.

### 3. Mutation serialization

Add a dedicated key helper such as `nav_history_lock_key()` in `src/process_lock.py`. At the repository public mutation boundary, hold the same lock around:

- `write_nav_record`, including existence lookup and create/update;
- `write_nav_records`;
- `patch_nav_derived_fields`;
- `patch_nav_details`;
- `delete_nav_by_record_id`.

Use one global same-host lock. Do not nest the lock through facade delegation. Existing repair account/journal locks acquire before entering the repository; the repository lock never acquires an account lock, preventing lock-order cycles.

Refactor the single-write path so its existence decision and mutation happen inside one lock, not a dry-run preview followed by an unlocked mutation.

### 4. Safe public defaults

Change default `overwrite_existing` from true to false through:

- repository/storage facade;
- `NavRecordService`, `PortfolioManager`, account/daily services;
- `PortfolioService`, HTTP schemas, service client;
- `PortfolioSkill` and module-level compatibility wrappers;
- `pm nav record` and `pm daily` argument mapping.

Retain explicit `--overwrite` for deliberate operator writes. Preserve `daily-job --overwrite` behavior.

Change `scripts/publish_daily_report.py` to default NAV dry-run. Add explicit `--write-nav --confirm` for retained compatibility; reject `--write-nav` without `--confirm`. Keep HTML artifact writing independent from NAV persistence.

### 5. Write classification propagation

Add one narrow immutable internal `NavWriteContext` value in `src/app/nav_finality.py`. It validates and carries:

- `status`;
- `writer`;
- `write_reason`;
- `nav_date`;
- `valuation_as_of` derived from `snapshot_time` when available;
- existing `run_id`.

The public HTTP/CLI contract does not accept arbitrary finality fields. Trusted entry methods construct the context: `DailyNavJobService` supplies canonical final metadata; manual record/daily-report/init/close/backfill paths supply explicit non-final classifications; validated repair patches may explicitly carry `status=final` while preserving `writer=nav-repair`. One optional context is passed through internal application/portfolio layers, and `NavRecordService` serializes it into existing calculation details before validation and persistence.

## Implementation slices

## Slice S1 — Finality contract and existing-row correctness

### Scope

- Add a small `src/app/nav_finality.py` module owning the immutable `NavWriteContext`, contract serialization/parsing, and eligibility.
- Add one optional internal write-context propagation through `AccountNavRecorderService`, `DailyAccountNavService`, `DailyNavJobService`, `PortfolioService`, `PortfolioManager`, and `NavRecordService`.
- Canonical daily-job writes `status=final`, `writer=daily-nav-job`.
- Manual/report/init/close/backfill paths use non-final classifications by default; an explicitly validated repair may write `status=final` with honest `writer=nav-repair`.
- Replace presence-only skip with the state machine above.
- Update receipt formatting for `existing_nav_not_final`.

### Primary files

- `src/app/nav_finality.py` (new)
- `src/app/daily_nav_job_service.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/daily_account_nav_service.py`
- `src/app/nav_record_service.py`
- `src/app/nav_initialization_service.py`
- `src/app/nav_history_receipt_service.py`
- `src/portfolio.py`
- `src/service/application.py`
- `skill_api.py`
- `src/maintenance/nav_history_repair/backfill.py`

### Tests

- Final daily-job row is skipped.
- Explicitly final validated-repair row is skipped even though its writer is `nav-repair`.
- Legacy row blocks as `existing_nav_not_final`.
- Manual/repair row blocks as non-final.
- Mismatched final `nav_date` blocks.
- Snapshot recovery takes precedence over finality, while finality plus absent snapshot fields is eligible.
- Newly written daily-job row persists the expected finality details.
- Manual/init/closed/backfill classification is not mistaken for final daily NAV.
- Receipt renders the new blocking status compactly.

### Validation

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_daily_nav_services.py \
  tests/test_nav_record_service.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_service_application.py
python3.12 -X pycache_prefix=/tmp/pm_nav_finality_s1 -m compileall -q src skill_api.py
```

### Residual risks

- Public default overwrite behavior and mutation races are `covered by approved slice S2`.
- Provider-observed quote date is `assigned to later pricing reliability work unit`.

## Slice S2 — Repository serialization and safe entry defaults

### Scope

- Add one repository-owned NAV mutation lock and keep single write check+mutation atomic on the same host.
- Cover full writes, bulk writes, field patches, details patches, and delete.
- Change overwrite defaults to false across all public/internal facades.
- Replace `--no-overwrite` semantics with explicit `--overwrite`, retaining a compatibility alias if needed without restoring unsafe defaults.
- Make report publishing NAV-read-only by default; require `--write-nav --confirm` for explicit compatibility writes.
- Update HTTP/service/client and documentation contracts.

### Primary files

- `src/process_lock.py`
- `src/feishu/repositories/nav_history_repository.py`
- `src/feishu/_nav_mixin.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/daily_account_nav_service.py`
- `src/app/nav_record_service.py`
- `src/portfolio.py`
- `src/service/http.py`
- `src/service/client.py`
- `src/service/application.py`
- `skill_api.py`
- `scripts/pm.py`
- `scripts/publish_daily_report.py`
- `README.md`
- `docs/runbook.md`
- `docs/service.md`
- `scripts/README_daily_report.md`

### Tests

- Concurrent same-date single writes cannot both create.
- Full write and patch/delete paths use the same lock.
- All defaults resolve to `overwrite_existing=False`.
- Explicit CLI/API overwrite still propagates true.
- Publisher default sends `dry_run=true`, `confirm=false` while still writing HTML artifacts.
- Publisher explicit NAV write requires both flags and sends `dry_run=false`, `confirm=true`.
- Existing repair patch/backfill and CLOSED flows remain functional.

### Validation

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_nav_bulk_upsert_minimal.py \
  tests/test_nav_history_patch.py \
  tests/test_pm_cli.py \
  tests/test_service_client.py \
  tests/test_service_http.py \
  tests/test_service_application.py \
  tests/test_daily_report_publisher.py
python3.12 -X pycache_prefix=/tmp/pm_nav_finality_s2 -m compileall -q src skill_api.py scripts
```

### Residual risks

- Cross-host serialization is `assigned to later infrastructure decision`.
- Existing production rows require explicit operator classification or repair and are not auto-migrated.

## Review and acceptance sequence

1. Planreview this artifact using `planreview`.
2. Fix accepted plan findings and re-review until pass.
3. Commit accepted plan artifacts.
4. Implement S1 only; run focused validation; DeepReview current changes; fix/re-review; commit accepted S1.
5. Implement S2 only; run focused validation; DeepReview current changes; fix/re-review; commit accepted S2.
6. Run aggregate DeepReview against `origin/main`; fix/re-review; run full test and compile gates; commit accepted aggregate review.
7. Push branch, create Draft PR, DeepReview the PR, fix/re-review, commit/push accepted PR review state, and write final closeout.

## Documentation decision

Update public docs because CLI defaults, publisher safety, daily-job status semantics, and persisted NAV details are public/operator-visible contracts. No schema migration document is required because the contract uses the existing `details` field and legacy rows remain readable.

## Completion state

- Current gate: `plan review pass`.
- Next gate: `accepted plan commit`.
