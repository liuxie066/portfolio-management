# Cash scan boundaries — implementation plan

Gate: plan. One approved slice S1 fixes the observable scan/receipt boundary end to end, avoiding mechanical per-file slices.

## Goal alignment and decisions

- Immediate failure notification: CLI uses existing `_dispatch_effect_receipts` on failed and successful scans. Failure-path send exceptions cannot replace original scan error.
- Independent retries: add cash-effect branch to existing `receipts dispatch`; absent unactivated DB is a successful skipped branch, existing corrupt DB fails visibly. Each branch is attempted despite other branch errors. No schema/service/config changes.
- Concurrent CLI/timer drains: wrap cash receipt drain in existing same-host `process_lock`, keyed to canonical DB path, through send and durable marking. This is necessary safety for adding a second delivery entry point.
- Receipt truth: capture scan completion timestamp in runtime payload. Dispatch resolves current state from latest scan of the exact scope; only a later completed scan establishes recovery. Legacy receipts use original outbox creation time. Render original error/time plus recovered time and recovery scan when proven; do not rewrite historical payload. A newer failure must not be displayed as currently recovered.
- Read responsibility: add `get_cash_holdings_fresh(account=...)` at HoldingsRepository and existing facade. Read complete raw scope; skip only rows with known noncash type and nonempty code outside CNY/USD/HKD-CASH. Unknown/missing type/code, reserved code with wrong type, CASH with unsupported code or mismatched currency raise HoldingsIntegrityError. Selected rows retain canonical conversion, numeric/identity/duplicate validation. Partial reads never publish generic caches. Scanner, cash fingerprint initialization, and CLI init pre-read before database creation use this narrow API; full holdings/NAV path unchanged. CNY-MMF remains noncash for this scanner.

## S1 — complete behavior change

Allowed: `src/feishu/repositories/holdings_repository.py`, `_holdings_mixin.py`, cash-flow effect service/receipt service, `scripts/pm.py`, affected tests/fakes, cash-flow runbook/deploy docs, Gateflow/review artifacts. No money application/state-machine changes, prices, reconciliation, version bump, new schema.

Data flow: complete raw read -> classify safely -> strict selected conversion -> cash fingerprints/effects transaction; exception -> failed scan + error outbox -> best-effort CLI send -> later independent timer retry. The new storage API has no generic cached-holdings side effects.

Tests: real repository with valid cash + nameless fund succeeds on cash path while full read fails; invalid type/code/currency/quantity/identity and duplicate cash rows fail. Scanner integration asserts no fingerprint advance on invalid cash. Failed CLI attempts send and retains original error even if send raises. Timer sends/retries cash queue despite NAV failure; absent DB does not activate. Historical recovered/error messages, scope isolation, newer failure, legacy timestamp fallback. Concurrent dispatchers send a pending row once in normal operation.

Validation: targeted pytest, then project required `python3 -m pytest tests -q`; compileall src/skill_api/scripts; ruff project targets; git diff --check. Review full actual call chains, then aggregate and PR metadata/CI; reuse unchanged code checks across review-only commits.

Docs: correct receipt timer coverage and describe narrow cash validation/runtime occurrence and recovery fields in existing operational docs. README positioning unchanged.

Residual risks: send succeeded but process crashes before durable mark can duplicate on retry — assigned to later work unit, owner PM outbox maintainer; same-host delivery lock does not claim multi-host safety. Live production transport acceptance is assigned to later user-authorized upgrade, owner operator. No blocking questions.

Completion: Draft PR URL, actual changes/tests/reviews, classified risks, no production mutation; current gate progresses automatically to final closeout pass.
