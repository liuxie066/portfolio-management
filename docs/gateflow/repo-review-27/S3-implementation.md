# S3 Implementation Artifact

- Gate: `implementation -> code review -> accepted slice commit`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S3-futu-installer`
- Base commit: `4446eb3`
- Status: `complete; ready for accepted slice commit`
- Artifact path: `docs/gateflow/repo-review-27/S3-implementation.md`
- Code review artifact: `docs/reviews/code-review-20260719-180242.md`

## Objective

Fix source findings 05 and 17: prevent filtered Futu fund rows from being zeroed, serialize broker synchronization with other account writes, and preserve deployed receipt credentials and unrelated env content across installer reruns.

## Changed production files

- `src/app/futu_balance_sync_service.py` — canonical stock/ETF zeroing boundary, legacy ETF-row matching, account write locking, and duplicate existing-row refusal.
- `scripts/install_linux.py` — target env parsing/merge, explicit-only receipt replacement, missing source/key preservation, and duplicate-key preflight.

## Changed tests

- `tests/test_futu_balance_sync_service.py`
- `tests/test_install_linux.py`

## Invariants proved

- Incoming Futu positions are eligible only for `STOCK`/`ETF`.
- Incoming codes may update legacy `CN_FUND/HK_FUND/US_FUND` ETF rows and preserve their metadata.
- Only unmatched canonical stock/`EXCHANGE_FUND` rows may be zeroed.
- Unmatched legacy fund rows and fund-only filtered snapshots are untouched.
- Futu diff reads and position/cash/MMF writes share the account write lock used by compensation recovery.
- Existing target env content is preserved byte-for-byte when the source is absent.
- A partial source updates only explicit non-empty `OM_FEISHU_BOT_*` values; missing source keys preserve target values.
- Duplicate source or target env keys fail before installer writes.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_futu_balance_sync_service.py tests/test_install_linux.py -q -p no:cacheprovider` -> `37 passed`.
- `python3 -X pycache_prefix=/tmp/pm_pycache -m compileall -q src/app/futu_balance_sync_service.py scripts/install_linux.py` -> passed.
- `git diff --check` -> passed.
- DeepReview -> pass; no accepted S3 finding.

## Residual risks

- Unsupported Futu security types remain `intentionally ignored and observable` in source snapshot diagnostics.

## Completion state

- Current gate: `accepted slice commit`
- Next entry point after commit: `S4 implementation`
- Stop condition: reached for S3.
