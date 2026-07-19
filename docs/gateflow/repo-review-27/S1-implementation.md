# S1 Implementation Artifact

- Gate: `implementation -> code review -> accepted slice commit`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S1-financial-invariants`
- Base commit: `ce24f86`
- Status: `complete; ready for accepted slice commit`
- Artifact path: `docs/gateflow/repo-review-27/S1-implementation.md`
- Code review artifact: `docs/reviews/code-review-20260719-172502.md`

## Objective

Fix source findings 01, 02, 03, and 23 at the authoritative financial-write boundary: finite/positive validation, same-host idempotency, replay short-circuit, and oversell/missing-holding rejection.

## Changed production files

- `src/process_lock.py` — stdlib same-host advisory lock keyed by logical financial operation.
- `src/models.py` — runtime-only replay state for `Transaction` and `CashFlow`.
- `src/write_guard.py` — finite positive trade/cash validation and non-negative finite fee validation.
- `src/app/trade_service.py` — account serialization, early validation, exact holding checks, and replay short-circuit.
- `src/feishu/repositories/transactions_repository.py` — stable dedup identity, locked/fail-closed check-create, persisted replay result.
- `src/feishu/repositories/cash_flow_repository.py` — locked content check-create and persisted replay result.
- `src/feishu/repositories/holdings_repository.py` — missing/negative mutation rejection.
- `skill_api.py` — replay output and locked validated compatibility cash writes.

## Changed tests

- `tests/test_trade_service.py`
- `tests/test_write_guard.py`
- `tests/test_feishu_storage.py`
- `tests/test_portfolio.py`
- `tests/test_process_lock.py`

## Invariants proved

- Account lock is acquired before repository idempotency lock.
- Transaction dedup content is computed before random fallback request ID.
- Idempotency lookup errors fail closed.
- Runtime replay metadata never serializes.
- Replay returns persisted data and exits before holdings/cash mutations.
- Invalid values, broker mismatch, missing holdings, and oversells perform zero ledger/holding/cash writes.
- Same-host process locking serializes competing processes.
- Default identical content replays; explicit distinct request IDs permit identical split trades.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_trade_service.py tests/test_write_guard.py tests/test_feishu_storage.py tests/test_portfolio.py tests/test_models.py tests/test_model_validators_decimal.py tests/test_process_lock.py -q -p no:cacheprovider` -> `173 passed`.
- `python3 -m compileall -q src skill_api.py` -> passed.
- `git diff --check` -> passed.
- DeepReview -> pass; no accepted S1 issue.

## Residual risks

- Cross-host uniqueness: `assigned to later infrastructure decision`; same-host locking is the approved contract.
- Secondary-write crash recovery: `covered by later approved slice S2`.

## Completion state

- Current gate: `accepted slice commit`
- Next entry point after commit: `S2 implementation`
- Stop condition: reached for S1.
