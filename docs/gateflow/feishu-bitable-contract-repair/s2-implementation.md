# Gateflow S2 Implementation — Holdings Mutation Contracts

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S2
- Base: 4af82f6
- Status: accepted after DeepReview and re-review
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/s2-implementation.md

## Implemented Contract

- Added immutable `HoldingIdentity`, tri-state `HoldingPatch`, complete
  `HoldingTarget`, and fresh-base/readback error types in the domain layer.
- Canonicalized identity text, currency, finite numbers, registry-owned select
  values, payloads, returned models, and memory/persistent cache keys.
- Required `(asset_id, account, broker)` for every mutation. A compatibility
  read without broker first loads a complete account slice and succeeds only
  for one candidate.
- Bound patches and targets to a fresh record ID plus full state digest; updates,
  creates, deletes, bulk writes, and compensation only publish cache or resolve
  after an independent account-slice readback proves the owned fields.
- Made missing distinct from explicit null. Only `avg_cost`, `asset_class`, and
  `industry` may be explicitly cleared; `tag=[]` requires explicit ownership.
- Changed cash, cash-flow effect, and compensation paths to carry canonical
  targets derived from the same fresh base, preserving manual metadata.
- Replaced complete account/all-account cache slices atomically after successful
  conversion and migrated legacy delimiter cache keys to collision-safe JSON
  identity keys.

## Deterministic Verification

- Scoped S2 suite: `138 passed`.
- Full repository suite: `1111 passed`.
- No live Feishu schema or business-record request was made.

## Expected Assertions Closed

- Omitted broker with two broker rows raises before any write.
- Whitespace/lowercase input produces one canonical payload, return value, and
  cache key.
- Explicit allowed clear emits `null`; omitted/default optional values do not
  clear remote manual metadata.
- A successful transport response with stale remote readback raises proof error,
  invalidates the account cache, and cannot resolve compensation.

## Final Acceptance Evidence

- Final independent review: `docs/reviews/code-review-20260802-014740.md`.
- Final re-review result: no actionable findings.
- Expanded S2 suite: 328 passed.
- Full repository suite: 1133 passed.
