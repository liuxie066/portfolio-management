# Gateflow Fix Artifact — S3 DeepReview

- Gate: `fix`
- Work unit: `holdings-validation-completion-conflict-receipts`
- Slice: `S3`
- Review artifact: `docs/reviews/code-review-20260731-221705.md`
- Status: `fix complete; pending re-review`

## Finding decisions and fixes

### DR-S3-01 — accepted — fixed

`events status` no longer constructs `OperationStateStore`. A dedicated
read-only path resolver and SQLite `mode=ro` inspector report existing inbox
evidence without parent-directory creation, DDL, migration, WAL-mode changes,
integrity migration, or chmod. A missing database is reported as
`initialized=false`.

### DR-S3-02 — accepted — fixed

Event-triggered discovery receipts now freeze the minimal trigger provenance:
event id/type, exact Base/table target, record id, canonical action list, and
revision. Manual discovery receipts remain unchanged, and neither path stores
credentials or the full event body in receipt payloads.

### DR-S3-03 — accepted — fixed

The worker loop now catches a cycle-level claim/failure-transition exception,
writes the failure to stderr/journald, waits on the bounded poll interval, and
continues. A deterministic regression proves that a second cycle still runs
after the first cycle raises.

## Verification

- Focused S1/S2/S3 regression suite: `110 passed`.
- Ruff on all touched Python files: passed.
- Python compilation: passed.
- Installer shell syntax: passed.
- `git diff --check`: passed.
