# S4 Implementation Artifact

- Gate: `implementation -> code review -> accepted slice commit`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S4-nav-recovery`
- Base commit: `d860a68`
- Status: `complete; ready for accepted slice commit`
- Artifact path: `docs/gateflow/repo-review-27/S4-implementation.md`
- Code review artifact: `docs/reviews/code-review-20260719-182038.md`
- Fix artifact: `docs/gateflow/repo-review-27/S4-fix.md`

## Objective

Fix source findings 06, 18, 19, and 25: preserve truthful NAV partial/recovery state, keep snapshot dry-runs free of formal local writes, and make multi-row NAV history patches preflighted, journaled, resumable, and reversible.

## Changed production files

- `src/app/account_nav_recorder_service.py` — detects failed snapshot state without requiring an error string and exposes task/retry metadata.
- `src/app/daily_account_nav_service.py` — reports real post-NAV report failures as `partial` with `nav_persisted=true`.
- `src/app/daily_nav_job_service.py` — preserves downstream partial status and detects existing-NAV recovery from persisted details or unresolved local compensation.
- `src/app/snapshot_service.py` — skips the formal local holdings snapshot file during dry-run.
- `src/maintenance/nav_history_repair/patch.py` — exact target preflight, changed-plus-successor validation, stable plan digest, fsync'd append-only journal, deterministic resume, reverse rollback, and truthful result metadata.
- `scripts/nav_history_repair.py` — explicit apply/dry-run/resume/rollback modes and non-zero exit for unsuccessful structured results.

## Changed tests

- `tests/test_daily_nav_services.py`
- `tests/test_snapshot_service.py`
- `tests/test_nav_history_patch.py` (new)
- `tests/test_entrypoint_consolidation.py`

## Documentation

- `docs/runbook.md` — operator workflow for NAV patch preview/apply/resume/rollback and state-conflict handling.
- `docs/INDEX.md` — canonical patch dry-run diagnostic command.

## Invariants proved

- A real NAV write followed by report-payload failure remains `partial` through the account and daily-job layers and exposes `nav_persisted=true`.
- An existing NAV with failed snapshot details or an unresolved compensation task returns `recovery_required`, task ID, and retry command without recalculating historical NAV.
- A complete existing NAV remains a no-write success.
- Snapshot dry-run leaves an existing local snapshot's content and mtime unchanged.
- Patch apply performs zero Feishu writes unless every target date resolves to exactly one stable record ID and validation passes.
- Default changed-scope validation includes each changed date and its first chronological successor.
- The repair journal is append-only under `${PM_DATA_DIR}/nav_repair`, uses `O_APPEND`, flush, `fsync`, and a process lock, and records original/target fields plus per-row outcomes.
- A row-N failure reports confirmed applied, failed, and pending rows and returns exact resume/rollback commands.
- Resume rejects a changed plan digest, verifies converged rows, and continues only original/target states.
- Rollback restores recorded originals in reverse order and reports `rollback_partial` on conflict or failure.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_daily_nav_services.py tests/test_nav_record_service.py tests/test_snapshot_service.py tests/test_nav_history_patch.py tests/test_entrypoint_consolidation.py -q -p no:cacheprovider` -> `63 passed`.
- `python3 -X pycache_prefix=/tmp/pm_pycache_s4_gate -m compileall -q src skill_api.py scripts/pm.py scripts/nav_history_repair.py` -> passed.
- `git diff --check` -> passed.

## Residual risks

- Ambiguous/partial Feishu transport responses are `covered by later approved slice S6`.
- NAV repair is same-host serialized by its journal lock; cross-host concurrent repair execution is `assigned to later infrastructure decision`, matching the repository's current local-operator contract.

## Completion state

- Current gate: `accepted slice commit`.
- Next entry point after commit: `S5 implementation`.
