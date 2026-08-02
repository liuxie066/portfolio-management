# Gateflow S12 Implementation — Compensation Current-State Mirror

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S12
- Base: `b33ff48`
- Recorded at: 2026-08-02T09:20:38+08:00
- Status: implementation and accepted fixes complete; pending re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s12-implementation.md`

## Scope

The implementation follows the accepted S12 parent plan and its refinement in
`s12-plan.md`. Production scope was extended to the small domain-owned
`src/domain/compensation_contracts.py` lifecycle contract and the generated
`docs/schema.md` projection. This removes duplicate status truth while keeping
the registry projection current. The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` remains excluded and untouched.

## Implemented Contract

- Local JSONL remains authoritative. `record`, `prepare`, public status changes,
  and retry transitions serialize under a per-task process lock; each lifecycle
  event is appended, flushed, and fsync'd before the mirror is called.
- Folded tasks carry monotonic `mirror_eligible` metadata. A supported
  actionable record is eligible immediately. Prepared intents are not, and a
  direct prepared success stays local. Transitioning a supported prepared task
  to PENDING, RUNNING, or FAILED activates its mirror for that and later states.
- Reserved lifecycle fields are written after caller metadata, so metadata
  cannot replace task identity, status, update time, or mirror eligibility.
  Later lifecycle evidence may now populate `related_record_id`, which is
  necessary when a prepared NAV intent learns its remote identity.
- Local status values, mirrorable status values, and allowed transitions now
  have one domain owner. Both the service state machine and Feishu select
  options consume that contract. Invalid regressions such as
  `PENDING -> PREPARED` fail before a local append or mirror request, and
  RESOLVED is absorbing.
- Replaced the create-only adapter with `mirror_compensation_task`. It first
  checks the optional table configuration; an absent table returns the explicit
  `skipped_unconfigured` outcome and performs no list/create/update request.
- A retained local mirror record id is updated directly. An explicit Feishu
  record-not-found response discards that stale hint and performs a fresh
  escaped lookup by `task_id`. Zero matches creates, one updates, and multiple
  matches return `duplicate` with no mutation. Malformed or out-of-scope rows
  fail closed.
- The storage projection selects fields from the canonical registry and uses
  the shared wire converter. Write responses must contain a stable record id
  and, when returned, the requested task id. Unknown write outcomes remain a
  local failed mirror receipt; the next transition can reconcile by lookup.
- Every eligible attempt appends a non-recursive local MIRROR receipt with its
  outcome, attempt time, remote identity, and error detail. Created/updated
  identity is retained as `mirror_record_id`; failed or duplicate projection is
  logged. None of these outcomes changes the local lifecycle.
- Retry failure now returns the fresh folded task, including its mirror receipt.
  Successful target replay records a stable resolution and mirrors the final
  RESOLVED state after target readback.
- Registered compensation `error` remains schema-required but is clearable and
  no longer create-row-required. Strict live-schema comparison therefore still
  rejects a configured table without the field, while RESOLVED updates can
  explicitly clear stale error text. `docs/schema.md` was regenerated from the
  corrected registry.

## Validation

- Exact S12 suite after accepted fixes: `118 passed`.
- Full repository suite after accepted fixes: `1369 passed`.
- Schema generator `--check`: passed.
- Ruff passed for clean/new touched source and tests. The legacy monolithic
  `src/feishu_storage.py` and `tests/test_feishu_storage.py` passed with only
  their pre-existing F401/E712/F811/F841 baseline rules excluded.
- Python compileall and `git diff --check`: passed.
- No live Feishu/Futu read or write, table/schema mutation, historical repair,
  push, merge, release, or deployment occurred.

## Expected Assertions Closed

- PENDING, RUNNING, FAILED, and RESOLVED folds are observed locally before
  projection and reuse one remote identity.
- A direct `PREPARED -> RESOLVED` operation performs zero mirror calls.
- Unconfigured, duplicate, and transport-error outcomes remain explicit local
  receipts while authoritative status is preserved.
- A stale local record id recovers through fresh lookup; fresh duplicates make
  zero remote mutations.
- RESOLVED projection clears error and includes current retry count,
  `updated_at`, `resolved_at`, and `resolution`.
- Registry, wire serialization, write validation, and generated schema agree.
- Domain lifecycle values and Feishu mirror select options share one source;
  illegal state regressions leave both local events and remote calls unchanged.

## Residual Boundaries

- Lookup-plus-create cannot guarantee cross-host uniqueness; an external writer
  racing the same `task_id` may create a duplicate that later attempts detect
  and refuse to mutate.
- The mirror is best effort. There is no background replay loop in S12; a later
  lifecycle transition or explicit retry provides the next reconciliation
  attempt.
- Historical local events infer eligibility from their first folded state.
  Unsupported legacy payloads remain local and are never made actionable by the
  mirror.

## Next Gate

Run DeepReview over the complete uncommitted S12 diff from `b33ff48`, including
the plan, implementation, generated schema, lifecycle service, storage
projection, registry change, and tests. Fix every accepted finding and obtain a
no-findings re-review before the scoped local S12 commit.
