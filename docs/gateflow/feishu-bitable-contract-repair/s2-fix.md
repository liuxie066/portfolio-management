# Gateflow S2 Fix — DeepReview Findings

## Gate Metadata

- Gate: fix
- Slice: S2
- Work unit: feishu-bitable-contract-repair
- Review artifacts:
  - docs/reviews/code-review-20260802-003531.md
  - docs/reviews/code-review-20260802-004937.md
  - docs/reviews/code-review-20260802-005905.md
  - docs/reviews/code-review-20260802-011033.md
  - docs/reviews/code-review-20260802-013230.md
- Recorded at: 2026-08-02T01:45:39+08:00
- Status: all findings through fourth re-review fixed; pending final S2 re-review
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/s2-fix.md

## Finding Decisions

### DR-S2-01 — accepted — fixed

- The `replace_holding(Holding)` compatibility adapter now derives ownership
  from Pydantic's explicit field set, including explicit `None` for
  `avg_cost`, `asset_class`, and `industry`, and explicit `tag=[]`.
- Omitted model defaults still have no write authority.
- Added a regression that proves the wire payload sends null/`[]` and the
  independent fresh readback returns cleared values.

### DR-S2-02 — accepted — fixed

- `HOLDING_ZERO_DELETE` now binds deletion to the recorded base record ID,
  canonical holding digest, serialized before-state, and zero quantity.
- A fresh row reusing the same business key cannot inherit deletion authority;
  it returns `state_conflict` without calling the delete path.
- Successful deletion is followed by an independent fresh absence proof before
  the compensation target can resolve.

### DR-S2-03 — accepted — fixed

- The reconciliation patch now treats transport update, single-record
  readback, value comparison, complete account-slice read, and cache publish as
  one proof block.
- Any exception after a write attempt invalidates and flushes the account's
  memory and persistent cache before re-raising.
- Added fault injection where the remote update and single-row readback succeed
  but the complete-slice proof fails; both cache layers are proven empty.

### DR-S2-RR-01 — accepted — fixed

- The registry now requires the complete holdings identity and canonical
  required values for every single or batch create.
- `tag` is non-null-clearable; explicit `[]` remains its only empty value.
- Update validation rejects null for every registry-nonclearable field.
- Domain identity, required create values, and null-clearable values are derived
  from the registry instead of repeating structure constants.

### DR-S2-RR-02 — accepted — fixed

- A create target now rejects every non-neutral optional value that is not
  explicitly owned. Neutral unowned values remain `None` for nullable fields
  and an empty tag tuple.

### DR-S2-RR-03 — accepted — fixed

- Added target-bound zero deletion at the repository boundary.
- Compensation passes the original `HoldingTarget`; the final delete is sent
  only to its `base_record_id` after another record/digest/zero check.
- A same-key replacement record is rejected before transport.

### DR-S2-RR-04 — accepted — fixed

- `init_db(initial_cash=...)` now requires an explicit broker, performs an exact
  fresh lookup, and carries the canonical broker into the created holding.
- `init_db()` without an initial cash write remains backward compatible.

### DR-S2-RR-05 — accepted — fixed

- `get_holdings()` canonicalizes an optional account once and reuses it for the
  loaded marker, remote preload, and cache filtering.

### DR-S2-RR2-01 — accepted — fixed

- `init_db(initial_cash=...)` now constructs a create-only `HoldingTarget` with
  `base=None` and calls absolute replace instead of additive upsert.
- A same-key row appearing after the initial existence check is rejected by the
  repository's final fresh-base proof with zero write authority.

### DR-S2-RR3-01 — accepted — fixed

- Cash-flow confirmation now pairs every recomputed target with its hash-bound
  before-row and compares it with the final fresh holding before constructing a
  mutation target or entering `applying`.
- A quantity or identity change after hash validation fails closed and requires
  a new preview/confirmation; the regression proves zero holdings writes.

### DR-S2-RR3-02 — accepted — fixed

- `asset_name` now uses the same required, trimmed, nonblank rule in domain
  patches/targets, typed raw conversion, registry validation, and single/batch
  client request boundaries.
- Domain identity/value/system projections have a complete registry coverage
  assertion, preventing a field from silently falling between structure and
  mutation contracts.

### DR-S2-RR3-03 — accepted — fixed

- Fresh-base failures in target, patch, bulk, and exact-record delete paths now
  invalidate and flush every affected account cache before re-raising.
- Fresh no-mutation exits publish the observed complete account slice instead
  of leaving older cache facts in place.
- Durable compensation classifies repository CAS conflicts as
  `state_conflict`; regressions prove zero transport and empty memory plus
  persistent caches for both single and bulk conflicts.

### DR-S2-RR4-01 — accepted — fixed

- Cash confirmation now carries the final fresh row's unowned fields into the
  target while owning only quantity for an existing holding.
- Confirm write decisions, independent readback, compensation already-applied
  checks, compensation readback, and final effect resolution all reuse
  `holding_owned_fields_match()` as the sole completion definition.
- Regressions inject a concurrent manual `tag` edit and prove the quantity
  target completes without overwriting the tag or creating an unnecessary
  compensation write.

### DR-S2-RR4-02 — accepted — fixed

- Added immutable `HoldingRepairPatch` for rows that cannot yet be represented
  as a typed `Holding`. It carries the complete identity, record ID, exact raw
  base digest, and only explicitly authorized repair values.
- The confirmed workflow constructs this contract from the operator-visible
  raw row. The repository compares a fresh exact-row read with both identity
  and digest before transport, then proves the requested values and publishes
  a complete fresh account slice.
- A changed confirmed base now raises `HoldingMutationConflictError` before
  transport and flushes both memory and persistent account caches. Post-write
  proof failures also invalidate every observed account identity.

## Validation Evidence

- Initial S2 scoped suite: 141 passed in 1.39s.
- Expanded re-review suite: 231 passed in 1.57s.
- Full repository suite after re-review fixes: 1122 passed in 8.27s.
- Third re-review targeted suite after all fixes: 230 passed in 1.57s.
- Full repository suite after all third re-review fixes: 1129 passed in 8.01s.
- Fourth re-review focused suite: 98 passed in 1.09s.
- Expanded S2 suite after fourth re-review fixes: 328 passed in 1.50s.
- Full repository suite after fourth re-review fixes: 1133 passed in 8.25s.
- Generated schema docs check and registry expectations command: passed.
- No live Feishu or Futu request was made.

## Next Gate

Independent S2 DeepReview re-review.
