# Gateflow Fix Artifact — Aggregate DeepReview

- Gate: `fix`
- Work unit: `nav-finality-write-authority`
- Review artifacts: `docs/reviews/code-review-20260721-131946.md`, `docs/reviews/code-review-20260721-134319.md`
- Artifact path: `docs/gateflow/nav-finality-write-authority/fix-aggregate-review.md`
- Status: `fix complete; pending aggregate re-review`

## Finding decision and fix

### DR-AGG-01 — accepted — fixed

The aggregate review found that the persisted local NAV index discarded `NAVHistory.details`. After a process restart, a valid canonical final row could therefore be reconstructed with `details=None`, causing `evaluate_nav_finality()` to return `missing_finality` and the daily job to report `existing_nav_not_final` instead of `skipped_existing_nav`.

Fixes applied at the NAV index ownership boundary:

- `_build_nav_index_payload()` now persists `details` for every indexed NAV row.
- `_nav_to_index_row()` now preserves `details` during incremental cache updates after writes and patches.
- `_ensure_nav_index_loaded()` now restores `details` when reconstructing `NAVHistory` objects from the local cache.
- Added `_store_nav_index_payload()` as the single helper for publishing a freshly built index to memory and the local cache.
- Account-scoped `audit_nav_history_duplicates()` now reuses the remote records it already fetched to rebuild and publish the fresh account NAV index. This makes the subsequent existing-row decision consume the same remote facts without adding a second Feishu read.

Final status: `已修复`.

### DR-AGG-02 — accepted — fixed

The aggregate re-review found that `FieldNameNotFound` compatibility handling could silently remove `details` and retry a single or batch NAV create/update. For finality-bearing rows this made the write appear successful even though Feishu, the authority, did not persist `details.finality`; the local cache could then temporarily claim a stronger state than the remote row.

Fixes applied at the Feishu write boundary:

- Added `_fields_contain_finality()` to identify serialized or dictionary `details.finality` payloads before compatibility fallback.
- Added `_fail_closed_if_finality_would_be_dropped()` as the shared single/batch guard.
- Single create/update now raises a clear `RuntimeError` on `FieldNameNotFound` instead of retrying without authoritative finality.
- Batch create/update now applies the same guard before constructing no-details fallback payloads.
- The guard runs before local cache publication, so failed authoritative writes cannot create local-only finality.
- Legacy low-level writes without `details.finality` retain the existing optional-details compatibility retry.

Final status: `已修复`.

## Regression coverage

- Added an account-scoped duplicate-audit regression starting from a stale legacy local cache and a remote row with valid finality. The test verifies that:
  - duplicate audit remains clean;
  - the in-memory existing row receives the remote finality metadata;
  - `evaluate_nav_finality()` returns eligible;
  - the refreshed local cache persists the same finality metadata.
- Added an incremental-write/restart regression proving that `details` survives a local-cache round trip.
- Existing daily-job tests continue to prove that an eligible final row becomes `skipped_existing_nav`, a legacy row becomes `existing_nav_not_final`, and snapshot recovery takes precedence.
- Updated the legacy Feishu storage update test to opt in explicitly with `overwrite_existing=True`, matching the accepted safe-default contract.
- Added fail-closed regressions for single create, single update, batch create, and batch update when Feishu returns `FieldNameNotFound` for a finality-bearing payload. Each test proves there is no no-details retry and no local cache publication.
- Added a legacy single-create compatibility regression proving that a payload without `details.finality` may still retry without the optional details field.

## Validation

Focused repository/fallback suite:

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_nav_bulk_upsert_minimal.py \
  tests/test_feishu_storage.py

89 passed in 0.32s
```

Complete relevant NAV/service/CLI/publisher suite:

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_nav_bulk_upsert_minimal.py \
  tests/test_nav_history_patch.py \
  tests/test_nav_write_defaults.py \
  tests/test_nav_record_service.py \
  tests/test_entrypoint_consolidation.py \
  tests/test_pm_cli.py \
  tests/test_service_client.py \
  tests/test_service_http.py \
  tests/test_service_application.py \
  tests/test_daily_report_entrypoints.py \
  tests/test_daily_nav_services.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_feishu_storage.py

245 passed in 1.51s
```

Full repository suite:

```text
python3.12 -m pytest -q -p no:cacheprovider

684 passed in 3.84s
```

```text
python3.12 -m compileall -q src skill_api.py scripts
# pass

git diff --check
# pass
```

## Docs decision

No additional public documentation is required for the repository-internal cache and FieldNameNotFound fail-closed fixes. Public finality, overwrite, CLI, service, and publisher behavior is already documented by S1/S2 changes. This artifact records the aggregate review fix and validation evidence.

## Residual risks

- Cross-host or external-writer uniqueness remains `assigned to later infrastructure decision`; the current lock is intentionally same-host only.
- Legacy rows without trusted finality remain `operator-owned after deployment`; they fail closed until validated repair.
- Live Feishu mutation/canary remains `out of scope for this work unit`; deterministic repository, service, CLI, and full-suite tests cover the changed behavior locally.
- Yahoo/Finnhub/Futu provider behavior remains `assigned to a separate work unit` and is not changed here.

## Next gate

`aggregate deepreview re-review` of DR-AGG-01, DR-AGG-02, and the complete work unit.
