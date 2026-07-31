# Gateflow Implementation Artifact — S1

- Gate: `implementation`
- Work unit: `holdings-validation-completion-conflict-receipts`
- Slice: `S1 — raw validation, resolver, and cache safety`
- Branch: `plan/holdings-validation-event-trigger`
- Prerequisite: accepted plan commit `9966b30`
- Status: `S1 code review pass; pending accepted-slice commit`
- Artifact path:
  `docs/gateflow/holdings-validation-completion-conflict-receipts/s1-implementation.md`

## Objective and expected outcome

Read Feishu holdings without typed/cache defaults, classify every raw field,
resolve currency from `asset_type` plus permitted exact instrument evidence,
publish only fully valid typed holdings into cache v2, and expose a local
read-only reconcile CLI. A blank currency must never become CNY implicitly.

## Allowed files and modules

- `src/app/holdings_validation.py` (new)
- `src/app/holdings_reconciliation_service.py` (new S1 read-only surface)
- `src/app/futu_balance_sync_service.py` (read-only observation contract only)
- `src/feishu/repositories/holdings_repository.py`
- `src/feishu/_holdings_mixin.py`
- `src/feishu_client.py` only for strict pagination completeness
- `src/local_cache.py`
- `scripts/pm.py`
- focused S1 tests and this artifact

## Exact changes and invariants

- Raw records preserve `record_id`, raw fields, source, and fetch time and never
  pass through `_from_feishu_fields()` or `_dict_to_holding()`.
- Missing quantity differs from numeric zero; non-finite and malformed numerics
  are invalid.
- Required identity fields are never inferred from query filters or another
  row/account/broker.
- Currency policy is versioned as `holdings-currency.v1`; reporting/pricing
  fallbacks are not accepted evidence.
- Futu observation is fresh, read-only, at most once per account, and exposes
  whether position currency was explicit rather than market-defaulted.
- Local holdings cache v2 ignores legacy/v1 payloads and accepts only strict
  typed rows produced from valid fresh data.
- `pm holdings reconcile` is local/read-only: no holdings mutation, SQLite
  workflow mutation, receipt enqueue, or external message send.
- Existing `pm holdings` list behavior and Futu sync write authority remain
  unchanged.

## Non-goals

- No cases, confirmations, workflow SQLite schema, receipt outbox, event SDK,
  listener service, NAV gate, external subscription, production read/write,
  release, or deployment in S1.

## Validation plan

- Focused validator/resolver/repository/cache/CLI/Futu observation tests.
- Existing holdings preload, Feishu storage, Futu sync, and CLI regression
  tests.
- Python compile validation for changed modules.
- S1 DeepReview after implementation; all accepted findings must be fixed and
  re-reviewed before the accepted-slice commit.

## Residual risks

- Case persistence/notification and human apply are covered by approved S2.
- Event-triggered discovery is covered by approved S3.
- Official NAV fresh preflight and snapshot handoff are covered by approved S4.
- Feishu conditional-write absence is irrelevant to this read-only slice and
  remains assigned to S2 confirmed apply recovery.

## Implemented files

- `src/domain/holdings.py`
- `src/app/holdings_validation.py`
- `src/app/holdings_reconciliation_service.py`
- `src/app/futu_balance_sync_service.py`
- `src/app/__init__.py`
- `src/feishu/repositories/holdings_repository.py`
- `src/feishu/_holdings_mixin.py`
- `src/feishu_client.py`
- `src/feishu_storage.py`
- `src/local_cache.py`
- `scripts/pm.py`
- focused validator, repository, cache, Feishu/Futu, and CLI tests

## Validation evidence

- Initial focused gate: `156 passed`.
- Post-review fix gate: `164 passed`.
- Expanded affected regression gate: `305 passed`.
- Python compile validation passed for `src` and `scripts/pm.py`.
- `git diff --check` passed.
- DeepReview artifact:
  `docs/reviews/code-review-20260731-205546.md`.
- DeepReview re-review artifact:
  `docs/reviews/code-review-20260731-210927.md`.
- Finding disposition/fix artifact:
  `docs/gateflow/holdings-validation-completion-conflict-receipts/fix-s1-review.md`.

## External actions not performed

- No live Feishu or Futu call, table mutation, message send, subscription, or
  service activation.
- No push, pull request, release, deployment, or production upgrade.
