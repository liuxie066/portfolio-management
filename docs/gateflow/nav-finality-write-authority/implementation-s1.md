# Gateflow Implementation Artifact — Slice S1

- Gate: `implementation`
- Work unit: `nav-finality-write-authority`
- Slice: `S1 — Finality contract and existing-row correctness`
- Branch: `fix/nav-finality-write-authority`
- Base: `origin/main@81e82e2`
- Created: `2026-07-21T11:48:07+08:00`
- Artifact path: `docs/gateflow/nav-finality-write-authority/implementation-s1.md`
- Completion status: `implementation and review loop complete; ready for accepted slice commit`

## Scope completed

- Added immutable internal `NavWriteContext` and versioned `details.finality` serialization.
- Added finality eligibility evaluation for existing rows.
- Changed daily-job existing-row handling from presence-only skip to:
  - snapshot failure or unresolved compensation -> `recovery_required`;
  - eligible explicit finality -> `skipped_existing_nav`;
  - legacy, non-final, malformed, or date-mismatched finality -> blocking `existing_nav_not_final`.
- Propagated trusted classifications:
  - daily job -> `final` / `daily-nav-job`;
  - direct manual NAV record -> `manual` / `nav-record`;
  - daily report bundle -> `manual` / `daily-report`;
  - initialization -> `initial` / `init-nav`;
  - close NAV -> `closed` / `close-nav`;
  - backfill -> `maintenance` / `nav-repair`.
- Preserved top-level `details.run_id` compatibility while also storing it in finality provenance.
- Added receipt blocker accounting and rendering coverage for `existing_nav_not_final`.

## Changed files

- `src/app/nav_finality.py` (new)
- `src/app/nav_record_service.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/daily_account_nav_service.py`
- `src/app/daily_nav_job_service.py`
- `src/app/nav_initialization_service.py`
- `src/app/nav_history_receipt_service.py`
- `src/portfolio.py`
- `skill_api.py`
- `src/maintenance/nav_history_repair/backfill.py`
- `tests/test_daily_nav_services.py`
- `tests/test_nav_record_service.py`
- `tests/test_nav_history_receipt_service.py`

## Invariants

1. An existing row is never considered final solely because it exists.
2. Snapshot recovery state takes precedence over finality eligibility.
3. Absence of snapshot success fields is treated as the successful steady state.
4. `writer` records provenance; eligibility depends on version, explicit `status=final`, matching date, and complete required provenance.
5. A validated repair may use `writer=nav-repair` with `status=final` and qualify without impersonating the daily job.
6. Legacy/non-final rows block rather than being overwritten or silently skipped.
7. Trusted internal write context must match the NAV record date.

## Validation

Baseline before implementation:

```text
62 passed in 0.36s
```

Post-implementation focused tests:

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_daily_nav_services.py \
  tests/test_nav_record_service.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_service_application.py

73 passed in 0.34s
```

Compile and whitespace validation:

```text
python3.12 -X pycache_prefix=/tmp/pm_nav_finality_s1 -m compileall -q src skill_api.py
# pass

git diff --check
# pass
```

## Documentation decision

No public operator documentation is changed in S1. Public default/CLI/publisher contract changes are owned by approved Slice S2, where README/runbook/service documentation will be updated together. This artifact and tests document the new persisted internal finality contract for the current gate.

## Residual risks and uncovered areas

- Repository mutation races and same-host serialization: `covered by later approved slice S2`.
- Unsafe overwrite defaults across CLI/API/storage/publisher surfaces: `covered by later approved slice S2`.
- Cross-host uniqueness/serialization: `assigned to later infrastructure decision`.
- Existing production legacy rows need explicit classification/repair and are not auto-migrated: `assigned to operator maintenance after deployment`.
- Provider-observed quote date is not available in current valuation objects: `assigned to later pricing reliability work unit`.

## Review outcome

- Initial review: `docs/reviews/code-review-20260721-115304.md` (`changes-requested`).
- Fix artifact: `docs/gateflow/nav-finality-write-authority/fix-s1-review.md`.
- Re-review: `docs/reviews/code-review-20260721-115858.md` (`pass`).
- All accepted findings are `已修复`.

## Next gate

`accepted slice commit` for S1.
