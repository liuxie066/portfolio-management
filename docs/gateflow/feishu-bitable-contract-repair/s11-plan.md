# Gateflow S11 Plan — Transactions Strict Read-Only Archive

- Gate: implementation plan
- Work unit: feishu-bitable-contract-repair
- Slice: S11
- Base: `803bf3b`
- Recorded at: 2026-08-02
- Status: accepted parent plan refined; implementation pending
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s11-plan.md`

## Decision

The Feishu `transactions` table is a legacy read-only archive. It is not an
active ledger and must not expose a code path that can create, update, or
delete remote rows. Reads return a dedicated archive model so absent remote
values remain absent instead of inheriting the writable `Transaction` model's
amount calculation and BUY/CNY/zero defaults.

The structural registry remains the unique source for the observed wire
contract. Because archive request identity is account-scoped, its business key
is corrected from `(request_id)` to `(account, request_id)`. This is a bounded
production-scope extension to `src/feishu/contracts/registry.py`; leaving the
registry unchanged would make the accepted unique-truth design contradict the
repository lookup contract.

## Allowed Scope

- `src/feishu/errors.py`
- `src/feishu/repositories/transactions_repository.py`
- `src/feishu/_transactions_mixin.py`
- `src/feishu_storage.py`
- `src/models.py`
- `src/feishu/contracts/registry.py` (bounded unique-truth correction)
- `tests/test_feishu_storage.py`
- `tests/test_trade_service.py`
- `tests/test_feishu_contracts.py`
- S11 Gateflow and DeepReview artifacts

The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` is explicitly excluded.

## Contract Changes

1. Add `LegacyReadOnlyError`. Repository and compatibility facade add/delete
   tombstones raise it before consulting caches or invoking Feishu transport.
2. Remove transaction write serialization, request-id write cache, generated
   idempotency behavior, and mutation implementation. The generic dedup lookup
   remains because the cash-flow repository still uses that shared legacy
   helper.
3. Add an immutable `ArchivedTransaction` read model. Required archive facts
   are record id, request id, dedup key, ISO `YYYY-MM-DD` text date, valid
   transaction type text, asset id, account, finite quantity/price, and
   nonblank currency. Optional amount and fee remain `None` when absent; no
   value is calculated or defaulted.
4. Rename retained request lookup to an archive read operation and require
   keyword-only `account + request_id`. The Feishu filter contains both fields;
   every returned row is strictly parsed and its account/request identity is
   checked. Zero matches returns `None`; duplicate or cross-scope responses
   fail closed.
5. Keep date range filtering as ISO text because the live `tx_date` field is
   Text. No epoch conversion or schema migration is introduced.
6. Set the registry business key to `(account, request_id)` while retaining an
   empty write-contract set and observed Text types.

## Validation

Primary command:

`PYTHONPYCACHEPREFIX=/tmp/pm_s11 python3.12 -m pytest -q -p no:cacheprovider tests/test_feishu_storage.py tests/test_trade_service.py tests/test_feishu_contracts.py`

Required evidence:

- add/delete rejection performs zero Feishu calls at facade and repository
  boundaries;
- malformed/missing archive fields raise validation errors without BUY, CNY,
  zero, amount, fee, or source manufacture;
- two accounts sharing a request id cannot cross-return;
- registry reports `(account, request_id)`, Text date/type, and no writes;
- retired TradeService entry points cannot reach transaction mutation;
- full repository tests, scoped Ruff, compileall, and `git diff --check` pass.

## Non-goals

- no live Feishu read, write, delete, or schema mutation;
- no transaction-ledger migration or historical row repair;
- no date field conversion;
- no new active writer, retry, or idempotency mechanism.

## Exit

After implementation, run DeepReview against `803bf3b`, fix every accepted
finding, re-review to zero findings, and create one scoped local S11 commit.
