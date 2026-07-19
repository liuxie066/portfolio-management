# S2 Implementation Artifact

- Gate: `implementation -> code review -> fix -> re-review -> accepted slice commit`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S2-compensation-recovery`
- Base commit: `34bcf1b`
- Status: `complete; ready for accepted slice commit`
- Artifact path: `docs/gateflow/repo-review-27/S2-implementation.md`
- Code review artifact: `docs/reviews/code-review-20260719-175157.md`
- Fix artifact: `docs/gateflow/repo-review-27/S2-fix.md`

## Objective

Fix source findings 04 and 24 with truthful partial-write results, durable same-host compensation evidence, target-level compare-and-set recovery, and explicit operator commands for inspection and retry.

## Changed production files

- `src/app/compensation_service.py` — `PartialWriteError`, append-only fsync event log, task folding, target CAS handlers, durable per-target retry progress, and retry state machine.
- `src/app/cash_service.py` — planning-only absolute cash/MMF target helpers.
- `src/app/trade_service.py` — deterministic post-ledger holding/cash target application and one complete compensation unit on secondary failure.
- `src/app/nav_record_service.py` — serialized snapshot recovery targets and durable task evidence before NAV detail patching.
- `src/feishu/repositories/nav_history_repository.py` — details-only NAV patch and `details` projection required by snapshot recovery CAS.
- `src/portfolio.py` — authoritative compensation persistence now returns the durable task and propagates persistence failure.
- `scripts/pm.py` — `pm compensation list` and confirmed `pm compensation retry` commands.
- `docs/schema.md`, `docs/runbook.md` — source-of-truth, target types, state model, inspection, conflict handling, and retry procedure.

## Changed tests

- `tests/test_compensation_service.py`
- `tests/test_trade_service.py`
- `tests/test_nav_record_service.py`
- `tests/test_pm_cli.py`
- `tests/test_portfolio.py`
- `tests/test_decimal_transactions.py`
- `tests/test_decimal_boundary_creation.py`

The last three are behavior-coupled compatibility assertions: secondary writes now use absolute target replacement rather than stale delta helpers.

## Invariants proved

- Local JSONL is durable before optional Feishu mirroring.
- Retry lock order is account -> task -> append log.
- Retry supports `PENDING`, `FAILED`, and orphaned `RUNNING`; `RESOLVED` is absorbing.
- Every applied/already-applied target outcome is appended while the task is `RUNNING` before the next target.
- Holding/cash/snapshot retries use compare-and-set and refuse unrelated later state with `state_conflict`.
- A crash before target mutation can retry from `FAILED`; a crash after mutation is absorbed as `already_applied`.
- NAV snapshot recovery reads persisted `details`, batch-upserts snapshots, patches complete details, and resolves only afterward.
- Unsupported legacy delta tasks remain inspectable and cannot be automatically replayed.
- Ledger success followed by target failure raises structured `PartialWriteError`; failure to persist recovery evidence is explicitly reported.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_compensation_service.py tests/test_trade_service.py tests/test_nav_record_service.py tests/test_pm_cli.py tests/test_audit_fixes.py -q -p no:cacheprovider` -> `75 passed` before the review fix.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_portfolio.py tests/test_cash_service.py tests/test_decimal_transactions.py tests/test_decimal_boundary_creation.py tests/test_feishu_storage.py tests/test_models.py tests/test_model_validators_decimal.py tests/test_trade_service.py tests/test_compensation_service.py tests/test_nav_record_service.py tests/test_pm_cli.py tests/test_audit_fixes.py -q -p no:cacheprovider` -> `228 passed` after the review fix.
- `python3 -X pycache_prefix=/tmp/pm_pycache -m compileall -q src scripts/pm.py` -> passed.
- `git diff --check` -> passed.
- DeepReview -> one accepted recovery-read finding, fixed and re-reviewed to pass.

## Residual risks

- Cross-host task and economic-write serialization: `assigned to later infrastructure decision`; the approved contract is same-host durability.
- Historical legacy delta tasks: `assigned to operator-managed repair`; automatic replay is intentionally refused.
- Existing-NAV recovery detection through the daily job: `covered by later approved slice S4`.

## Completion state

- Current gate: `accepted slice commit`
- Next entry point after commit: `S3 implementation`
- Stop condition: reached for S2.
