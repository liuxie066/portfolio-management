# S4 Review Fix Artifact

- Gate: `fix -> re-review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S4-nav-recovery`
- Review artifact: `docs/reviews/code-review-20260719-182038.md`
- Status: `all accepted findings fixed`
- Artifact path: `docs/gateflow/repo-review-27/S4-fix.md`

## Accepted findings and fixes

### S4-01 — same-account repair serialization

- Added the existing `account_lock_key(account)` outside the per-journal lock for both apply/resume and rollback.
- The lock order is account lock -> journal lock, matching compensation recovery ownership.
- Added a regression proving apply and rollback acquire the account lock before their journal locks.

Status: `已修复`.

### S4-02 — torn JSONL tail recovery

- Journal reading now tolerates only a malformed final fragment without a trailing newline.
- The next append, while holding the repair locks, truncates that torn tail to the last complete newline before appending and fsyncing a new event.
- Malformed complete/middle lines still fail closed.
- Fault injection appends torn bytes after a partial apply; resume completes and the resulting journal is fully parseable.

Status: `已修复`.

### S4-03 — recovery receipt error propagation

- Existing-NAV recovery now exposes canonical `error` in addition to `snapshot_error`.
- Snapshot partial payloads also expose canonical `error` from the shared failure helper.
- Added receipt coverage proving the Feishu message shows the real recovery error and never `unknown error`.

Status: `已修复`.

## Re-review validation

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_daily_nav_services.py tests/test_nav_record_service.py tests/test_snapshot_service.py tests/test_nav_history_patch.py tests/test_entrypoint_consolidation.py tests/test_nav_history_receipt_service.py -q -p no:cacheprovider` -> `63 passed`.
- `python3 -X pycache_prefix=/tmp/pm_pycache_s4_rereview -m compileall -q src skill_api.py scripts/pm.py scripts/nav_history_repair.py` -> passed.
- `git diff --check` -> passed.

## Residual risk classification

- Cross-host repair serialization remains `assigned to later infrastructure decision`.
- Feishu transport response ambiguity remains `covered by later approved slice S6`.
