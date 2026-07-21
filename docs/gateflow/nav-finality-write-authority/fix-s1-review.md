# Gateflow Fix Artifact — Slice S1 Code Review

- Gate: `fix`
- Work unit: `nav-finality-write-authority`
- Slice: `S1`
- Review artifact: `docs/reviews/code-review-20260721-115304.md`
- Artifact path: `docs/gateflow/nav-finality-write-authority/fix-s1-review.md`
- Status: `fix complete; pending re-review`

## Finding decisions and fixes

### DR-S1-01 — accepted — fixed

- Added a single writer/status compatibility matrix shared by construction and eligibility parsing.
- `final` is valid only for `daily-nav-job` and `nav-repair` in contract version 1.
- Existing-row parsing now rejects unsupported writers, writer/status mismatches, missing `valuation_as_of`, invalid timestamps, and invalid explicit run IDs.
- Added fail-closed tests for unknown writer, mismatched writer/status, invalid timestamp, and missing timestamp.

Final status: `已修复`.

### DR-S1-02 — accepted — fixed

- `NavWriteContext.with_runtime()` now normalizes incoming runtime facts and raises on conflicting `run_id` or `valuation_as_of`.
- Added tests for both conflict paths.

Final status: `已修复`.

### DR-S1-03 — accepted — fixed

- `NavRecordService.record_nav()` now normalizes `datetime` input to `date` before calculation and finality comparison.
- Added direct service regression coverage.

Final status: `已修复`.

## Additional maintenance fix

- Updated the `close_nav` inline contract text to describe CLOSED finality provenance.
- Normalized import ordering in repair backfill.

## Validation

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_daily_nav_services.py \
  tests/test_nav_record_service.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_service_application.py

73 passed in 0.34s
```

```text
python3.12 -X pycache_prefix=/tmp/pm_nav_finality_s1 -m compileall -q src skill_api.py
# pass

git diff --check
# pass
```

## Docs decision

Public documentation remains owned by S2 because the operator-visible default and CLI/publisher changes occur there. S1 internal contract and review evidence are documented in Gateflow artifacts and tests.

## Residual risks

- Repository mutation race/serialization: `covered by later approved slice S2`.
- Unsafe overwrite/publisher defaults: `covered by later approved slice S2`.
- Existing legacy production data remediation: `operator-owned after deployment`.
- Cross-host serialization: `assigned to later infrastructure decision`.

## Next gate

`re-review` of all three accepted findings.
