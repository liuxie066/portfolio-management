# Gateflow S11 Implementation — Transactions Strict Read-Only Archive

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S11
- Base: `803bf3b`
- Recorded at: 2026-08-02T08:57:44+08:00
- Status: implementation complete; pending DeepReview
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s11-implementation.md`

## Scope

The implementation follows the accepted S11 slice and its refinement in
`s11-plan.md`. Production scope was extended only to
`src/feishu/contracts/registry.py` so the structural unique source reports the
same account-scoped business key enforced by archive lookup. The unrelated
untracked `docs/reviews/code-review-20260801-084655.md` remains excluded and
untouched.

## Implemented Contract

- Added `LegacyReadOnlyError`. Both the public compatibility facade and direct
  repository add/delete entry points are now non-operational tombstones that
  raise before any cache access or Feishu transport call.
- Removed transaction create/delete implementation, write serialization,
  request-id write lookup/cache behavior, generated request ids, same-host
  write locking, and replay marking from the Feishu transaction boundary.
- Retained the generic private dedup lookup because the active cash-flow
  repository still delegates to it; it is a read helper, not a transactions
  writer.
- Added immutable, extra-forbidding `ArchivedTransaction`. It exactly mirrors
  the registered transaction fields plus remote `record_id`; required identity
  and core values cannot be blank or missing, date/type must arrive as Text,
  the date must be canonical `YYYY-MM-DD`, and numeric facts must be finite.
- The archive model is intentionally independent of writable `Transaction`.
  Missing optional `amount` and `fee` remain `None`; no amount calculation,
  BUY/CNY/zero fallback, source, tax, broker, or related-account value is
  manufactured during reads.
- Replaced writer-oriented request-id lookup with
  `find_archived_transaction_by_request_id(account=..., request_id=...)`.
  Both values are mandatory, both are pushed into the remote filter, every
  result is strictly parsed and checked against the requested scope, and
  duplicate or cross-scope responses fail closed.
- Corrected the registry business key to `(account, request_id)`, retained
  observed Text wire types for `tx_date` and `tx_type`, and retained an empty
  write-contract set. A contract test proves the archive model and registry
  field sets are identical.
- Date range reads continue to use canonical ISO Text filters; no epoch
  conversion or live schema migration was added.
- TradeService and PortfolioManager tests now explicitly prove that retired
  transaction mutation entry points are absent.

## Validation

- Exact S11 suite: `111 passed`.
- Full repository suite: `1360 passed`.
- Ruff passed for every touched clean/new source and test surface. The legacy
  monolithic `tests/test_feishu_storage.py` and composition
  `src/feishu_storage.py` retain documented pre-existing unused-import/style
  findings; Ruff passed there with only those baseline rules excluded.
- Python compileall and `git diff --check`: passed.
- No live Feishu/Futu read or write, schema mutation, historical repair,
  merge, release, or deployment occurred.

## Expected Assertions Closed

- Facade and repository add/delete attempts raise `LegacyReadOnlyError` with
  zero client calls.
- Missing type/currency/quantity, non-finite price, epoch date, and unregistered
  source data fail validation rather than producing a partially defaulted
  transaction.
- Optional amount and fee remain absent when the archive row omits them.
- Two accounts may share one request id and return only their own row;
  out-of-scope responses and duplicate composite keys are rejected.
- Registry identity, registered field set, Text date/type, and structural
  read-only status are executable assertions.

## Residual Boundaries

- Existing malformed historical rows now fail strict reads. S11 does not repair
  them or define a migration destination.
- Feishu filtering is not transactional; returned rows are therefore validated
  locally before publication, but an external edit after the response remains
  outside this read boundary.
- `Transaction` and its historical dedup helpers remain in the domain module
  for non-Feishu compatibility. They are no longer reachable as a remote
  transaction write implementation.

## Next Gate

Run DeepReview over the complete uncommitted S11 diff from `803bf3b`, including
the plan and implementation artifacts and the bounded registry correction.
Fix every accepted finding and obtain a no-findings re-review before the scoped
local S11 commit.
