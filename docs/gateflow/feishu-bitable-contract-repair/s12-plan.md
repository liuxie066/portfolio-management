# Gateflow S12 Plan — Compensation Current-State Mirror

- Gate: implementation plan
- Work unit: feishu-bitable-contract-repair
- Slice: S12
- Base: `b33ff48`
- Recorded at: 2026-08-02
- Status: accepted parent plan refined; implementation pending
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s12-plan.md`

## Decision

The fsync'd local compensation event log remains the sole task authority. The
optional Feishu `compensation_tasks` table is a best-effort projection of the
latest local fold: it cannot make a task exist, change its authoritative
status, roll back a local transition, or recursively create another task.

Only actionable tasks are mirror eligible. A task recorded with a complete,
supported recovery target is eligible immediately. A locally prepared intent
is initially ineligible; it becomes eligible only when it transitions to
`PENDING`, `RUNNING`, or `FAILED`. A direct `PREPARED -> RESOLVED` success
therefore performs no Feishu mirror operation.

The structural registry remains the unique source for the mirror's fields,
wire encodings, lifecycle options, business key, and write validation. The
generated `docs/schema.md` is a projection and is added as a bounded scope
extension so accepted registry changes cannot leave the published schema
stale.

## Allowed Scope

- `src/app/compensation_service.py`
- `src/domain/compensation_contracts.py` (bounded lifecycle unique truth)
- `src/feishu_storage.py`
- `src/feishu/contracts/registry.py`
- `tests/test_compensation_service.py`
- `tests/test_feishu_storage.py`
- `docs/schema.md` (generated projection only)
- S12 Gateflow and DeepReview artifacts

The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` is explicitly excluded.

## Contract Changes

1. Every authoritative lifecycle transition is appended and fsync'd locally
   before any mirror call. A per-task process lock serializes append followed
   by mirror so an earlier remote projection cannot overtake a later local
   transition on the same host.
2. The folded local task stores monotonic `mirror_eligible` metadata. Supported
   `PENDING` records are eligible; prepared tasks remain local until an
   actionable transition, and direct prepared success never activates the
   mirror.
3. Replace create-only mirroring with `mirror_compensation_task`. The method
   first distinguishes an unconfigured optional table and returns
   `skipped_unconfigured` without a remote list or write.
4. A locally retained `mirror_record_id` is used for updates. If it is absent
   or stale, perform a fresh escaped lookup by `task_id`: zero matches creates,
   one match updates, and multiple matches return `duplicate` without a
   mutation. Every returned row is checked against the requested task id.
5. Create/update fields are selected from the canonical table registry and
   encoded by the shared wire converter. The projection includes current
   `status`, `retry_count`, `updated_at`, `resolved_at`, `resolution`, and
   `error`; update may explicitly clear the registered clearable `error`.
6. After each eligible mirror attempt, append a local `MIRROR` receipt event
   containing the outcome, timestamp, remote record id when known, and error
   detail when unsuccessful. This receipt is observability metadata only.
   Remote errors, duplicates, and receipt-append errors are logged and never
   alter the already durable task lifecycle or create recursive compensation.
7. Keep `error` structurally required but make it create-row-optional and
   clearable. A running or resolved current-state row need not manufacture an
   error, while an update can remove prior failure text. Regenerate
   `docs/schema.md` from this registry.
8. Define local statuses, mirrorable statuses, and legal transitions once in a
   domain lifecycle contract. The service validates every transition before
   append; the Feishu registry projects its select options from the same truth.

## Validation

Primary command:

`PYTHONPYCACHEPREFIX=/tmp/pm_s12 python3.12 -m pytest -q -p no:cacheprovider tests/test_compensation_service.py tests/test_feishu_storage.py`

Required evidence:

- configured tasks project `PENDING -> RUNNING -> FAILED/RESOLVED`, preserving
  one remote identity and always observing the local event first;
- unsupported records and direct `PREPARED -> RESOLVED` operations make zero
  mirror calls;
- unconfigured table returns `skipped_unconfigured` explicitly;
- stale ids recover through a fresh lookup, while duplicate task ids and
  remote errors leave the local lifecycle unchanged and appear in receipts;
- resolved projection clears stale error and carries resolution timestamps;
- the registry, generated schema, wire conversion, and storage payload agree;
- full repository tests, schema generation check, scoped Ruff, compileall, and
  `git diff --check` pass.

## Non-goals

- no live Feishu read, write, table creation, or schema mutation;
- no cross-host compensation authority or distributed transaction protocol;
- no historical duplicate repair or backfill;
- no change to target application, overwrite authority, or retry confirmation.

## Exit

After implementation, run DeepReview against `b33ff48`, fix every accepted
finding, re-review to zero findings, and create one scoped local S12 commit.
