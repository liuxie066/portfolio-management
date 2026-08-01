# Implementation Plan — Holdings Case Precondition Identity

## Goal

Prevent an unrelated holding-field change from causing
`holding case key collision with different semantics`, while retaining the
precondition protection that prevents a stale manual confirmation or apply from
being reused after its actual authority inputs change.

The concrete acceptance scenario is record `recvfmaw53roJx` (`sy / SPY`): after
`asset_type` and `asset_name` are corrected, a record-scoped notify must close
the obsolete `asset_name` case even though the unchanged legacy `created_at` and
`updated_at` values still have open nonblocking cases.

## Motivation and Direct Evidence

- `_build_case()` currently puts `asset_id`, `asset_type`, `account`, and
  `broker` into every field's `case_precondition_digest`.
- The case key does not contain that digest. Therefore an unrelated
  `asset_type` edit leaves a timestamp case key stable but changes its digest.
- `OperationStateStore.materialize_holding_cases()` treats a same-key digest
  difference as a semantic collision and rolls back before the fresh scan can
  close the repaired `asset_name` case.
- The collision guard correctly prevented partial state changes. The fix must
  classify only the known legacy-to-field-specific transition; it must not turn
  arbitrary same-key digest changes into refreshes.

## Success Signals

- A fresh SPY record-scoped notify completes without collision.
- The old SPY `asset_name` case becomes `resolved_external` and queues exactly
  one closure receipt.
- Existing `created_at` and `updated_at` cases retain their case keys and open
  states; each may receive one audit-only precondition migration event, but no
  discovery, supersession, or closure receipt.
- No holdings field is written by notify.
- A legitimate `currency` or `asset_class` dependency change cannot reuse an
  old confirmation scope or apply attempt.
- A migratable legacy `resolved_keep` remains confirmed through NAV preflight
  and is upgraded without changing state or sending a new receipt.
- Repeating the same notify is idempotent and creates neither another migration
  event nor another operation receipt.

## Non-goals and Scope Boundary

- Do not normalize the legacy timestamp values.
- Do not change tag parsing or close the `tag="[]"` cases.
- Do not change the `asset_class` economic-exposure policy.
- Do not enable or redesign the Feishu listener; its existing record-scoped
  workflow is only covered as another caller of case materialization.
- Do not change the semantic case-key contract, SQLite schema, HTTP surface,
  CLI commands/options, or manually edit production operation state. The
  `case_precondition_digest` value exposed by diagnostic case JSON remains an
  opaque internal token but will visibly gain the v2 prefix.
- Do not commit, push, release, or upgrade a remote environment in this work
  unit without separate authorization.

## Contract Changes

### Stable semantic case identity

- Keep `CASE_CONTRACT_VERSION = "holdings-case.v1"`.
- Keep the existing case-key payload byte-for-byte unchanged.
- Do not include the precondition digest or its contract version in the case
  key.

The case key continues to identify the user-visible fact: record identity,
field, kind, current/proposed value, authority id, and policy. A whole-record or
irrelevant-field edit does not create a new lifecycle.

### Field-specific precondition v2

Add a pure internal contract module used by workflow planning and store
transition checks. It owns:

- `PRECONDITION_CONTRACT_VERSION = "holdings-precondition.v2"`;
- construction of the precondition payload and digest;
- construction of confirmation scope;
- classification of a legacy-to-v2 transition.

New record-case digests use an explicitly recognizable representation:

```text
holdings-precondition.v2:<sha256-of-canonical-payload>
```

Existing unprefixed SHA-256 digests are treated as the one supported legacy
contract. Unknown prefixes are never migration candidates.

Every v2 precondition payload contains:

- record id;
- stable identity (`asset_id`, `account`, `broker`);
- field;
- canonical current field value.

Only decisions whose validation policy reads raw `asset_type` include it as an
additional authority input:

- `currency`;
- `asset_class`.

`asset_name`, `asset_type`, `created_at`, `updated_at`, `tag`, `industry`,
`quantity`, record issues, and other manual/invalid fields do not include
`asset_type`. Exact Futu authority changes remain represented by `authority_id`
and evidence refreshes. The synthetic global-orphan case keeps its existing
independent precondition formula and is not migrated by this slice.

For transition verification, each candidate also carries the legacy digest
computed from the current record using the old broad formula. This is internal,
transaction-only metadata and is neither persisted nor exposed in receipts.

### One-way compatibility predicate

The shared predicate returns `exact`, `legacy_migratable`, or `reject`.

`legacy_migratable` requires all of the following:

1. case key, record id, identity, field, kind, current/proposed values,
   authority id, and policy version are unchanged;
2. stored digest is an unprefixed legacy SHA-256 and candidate digest has the
   recognized v2 prefix;
3. for `currency` and `asset_class`, stored digest exactly equals the
   candidate's legacy digest computed from the current record;
4. for fields that do not depend on `asset_type`, the unchanged semantic case
   identity is sufficient because the only legacy authority input removed by
   v2 is raw `asset_type`;
5. stored state is eligible under the state matrix below.

Any v2-to-different-v2 mismatch, unknown contract, changed immutable semantic
fact, or failed relevant-dependency equality remains a hard collision.

### State matrix

| Stored state | Legacy-to-v2 behavior | State/receipt effect |
|---|---|---|
| `pending_apply` | migrate atomically | state unchanged; audit event only |
| `pending_confirmation` | migrate atomically | state unchanged; audit event only |
| `pending_manual_edit` | migrate atomically | state unchanged; audit event only |
| `resolved_keep` | migrate only if the stored resolution's confirmation scope exactly matches the stored legacy case | rewrite only the scope to v2; preserve decision, reason, operator context, and state; audit event only |
| `resolved_accept`, `resolved_external`, `superseded` | migrate only when the same semantic case is legitimately reopened by existing rules | migration and reopen are one transaction; no migration receipt |
| `applying`, `failed_retryable`, `apply_outcome_unknown` | reject | no mutation; existing recovery path must complete first |

The audit event is `precondition_contract_migrated` and records old/new contract,
old/new digest, state, and trigger. It is not an operation receipt. Existing
discovery and terminal receipt payloads remain frozen history.

### Confirmation, apply, and outage safety

- `_confirmation_scope()` moves to the shared contract module so workflow and
  store calculate the same value.
- `_require_same_scope()` accepts only `exact` or an eligible
  `legacy_migratable` transition. Direct resolve/apply still performs a fresh
  read under existing locks before any holding patch.
- Both store materialization paths apply the same transition helper before
  their immutable comparison. `materialize_and_prepare_holding_apply()` may
  migrate a `pending_*` case and move it to `applying` in the same transaction;
  no remote write starts if that transaction fails.
- `plan_evaluation()` treats a legacy `resolved_keep` as confirmed only when the
  shared predicate validates the stored legacy confirmation scope. Normal
  materialization then upgrades its digest and resolution scope atomically
  before preflight returns.
- `apply_outage_manual_confirmations()` uses the same field-specific builder and
  compatibility predicate. If provider evidence is unavailable and there is no
  materializable current candidate, a validated legacy keep may remain read-only
  compatible for that run and stays legacy until a future evidence-complete
  materialization. A changed relevant dependency is never accepted.

## Ownership and Affected Files

- `src/app/holding_case_contract.py` (new private module)
  - versioned precondition construction;
  - confirmation-scope construction;
  - pure legacy transition classification.
- `src/app/holdings_workflow_service.py`
  - consume the shared builder in `_build_case()` and outage handling;
  - keep case-key construction unchanged;
  - use compatibility classification for planning and fresh-scope checks.
- `src/app/operation_state_store.py`
  - apply the state matrix atomically in both case materialization paths;
  - emit audit events without touching the operation receipt outbox;
  - keep generic same-key collision checks fail-closed.
- `tests/test_holding_case_contract.py` (new)
  - field dependency map and transition classification.
- Existing workflow, store, event-inbox, and NAV-preflight tests
  - exact regression and caller-specific state-machine coverage.
- `docs/gateflow/holdings-case-precondition-identity/`
  - plan/review/implementation artifacts only.

No production database migration or public schema change is planned.

## Implementation Slices

### S1 — Pure contract and compatibility matrix

- Add the shared contract module.
- Preserve the case-key function and add golden assertions captured from the
  current v1 implementation before changing precondition construction.
- Produce v2 precondition digests plus current-record legacy digests.
- Unit-test every field group, unknown prefixes, exact v2 matches,
  legacy-migratable cases, relevant-dependency mismatch, and v2 mismatch.

S1 has no store mutation and cannot send a receipt.

### S2 — Atomic store migration

- Route both `_materialize_holding_cases_tx()` and
  `materialize_and_prepare_holding_apply()` through one store-private migration
  helper before immutable comparison.
- Implement every state-matrix row, including exact resolution-scope validation
  for `resolved_keep` and hard rejection of in-flight/recovery states.
- Preserve the surrounding transaction: candidate refresh, migration event,
  optional reopen/apply preparation, case changes, and existing receipts either
  commit together or all roll back.
- Do not insert, reset, or modify an operation receipt for a migration-only
  transition.

### S3 — Workflow/NAV/outage integration

- Use the shared precondition and confirmation-scope functions everywhere the
  workflow reconstructs scope.
- Make normal notify, event-inbox planning, record apply/resolve, account NAV
  preflight, and outage confirmation consume the same compatibility result.
- Preserve current locking and the rule that notify/listener paths never patch
  holdings.
- Keep exact repaired-field closure after materialization; it must see the same
  active case keys because semantic keys do not change.

### S4 — Regression and rollout artifact

- Add the SPY regression and the focused state/receipt/NAV tests below.
- Run focused suites, full baseline, and diff checks.
- Record the forward-compatibility and later rollout gates in the implementation
  artifact. Do not perform release or production steps in this work unit.

## Tests and Expected Assertions

1. Exact SPY regression from real failure shape:
   - seed legacy v1 name/type/timestamp cases;
   - change raw `asset_type` and `asset_name`, leaving timestamps invalid;
   - record-scoped notify succeeds and performs no holdings patch;
   - name becomes `resolved_external` with exactly one closure receipt;
   - timestamp keys/states are unchanged;
   - each timestamp gets at most one migration event and zero discovery,
     supersession, or closure receipts.
2. Stable identity and idempotency:
   - case-key golden values do not change;
   - a second identical notify produces zero state event and receipt increments.
3. Real dependency invalidation:
   - unchanged `currency`/`asset_class` inputs migrate only when the stored
     legacy digest matches the legacy digest of the fresh record;
   - changed raw `asset_type` cannot migrate an old keep or authorize apply;
   - a realistic changed validation outcome creates/supersedes the appropriate
     semantic case under existing rules.
4. `resolved_keep` NAV continuity:
   - a non-dependent legacy keep with an unrelated `asset_type` change remains
     confirmed in `plan_evaluation()`;
   - non-dry-run preflight migrates digest and scope, remains successful, keeps
     `resolved_keep`, and sends no receipt;
   - dry-run performs no migration;
   - an ineligible keep remains blocking.
5. Store state matrix in both materialization paths:
   - pending states migrate once;
   - keep scope is validated and rewritten atomically;
   - terminal reopen follows existing lifecycle;
   - applying/failed-retryable/outcome-unknown and injected failures leave case,
     event log, apply attempt, and outbox unchanged.
6. Direct resolve/apply:
   - eligible legacy pending cases may migrate and resolve/apply under the fresh
     lock-protected read;
   - relevant changes and unknown/v2 mismatches reject before a Feishu patch;
   - the combined materialize-and-prepare transaction leaves no window between
     migration and `applying`.
7. Event and outage entry points:
   - one record-change event produces the same silent migration and exact closure
     set as manual notify;
   - a compatible outage keep is honored read-only without mutating a row that
     lacks evidence-complete materialization;
   - a dependency mismatch remains fail-closed.
8. Validation commands:
   - focused contract/workflow/store/event/NAV tests;
   - complete `python3.12 -m pytest -q -p no:cacheprovider` baseline;
   - `git diff --check`.

## Migration and Later Rollout Gate

Migration is per existing semantic case and occurs only on its next mutating
materialization. Account NAV preflight may therefore migrate multiple eligible
rows, but it sends no migration receipts and preserves eligible keep decisions.

Before any separately authorized remote upgrade:

1. read-only inventory legacy cases by state and require zero `applying`,
   `failed_retryable`, and `apply_outcome_unknown` rows, or complete their
   existing recovery on the old release;
2. inventory operation outbox and record the expected exact SPY delta;
3. suspend holdings materializers and the operation-receipt sender, take an
   operation DB backup, upgrade, run the exact SPY canary, and verify case/event/
   outbox counts plus SQLite integrity before resuming them;
4. assert the canary delta is exactly one name closure receipt, zero migration
   receipts, stable timestamp keys, and no holdings write;
5. only then resume account NAV and listener materialization.

Binary rollback is safe before the first v2 precondition is persisted. After a
case is migrated, the previous binary does not understand the prefixed digest;
therefore rollback during the suspended canary window must restore the paired
operation DB backup before any receipt delivery, and normal operation after the
gate is forward-fix only. This limitation must appear in release notes and needs
separate deployment authorization; this implementation work does not execute it.

## Documentation Decision

No public holdings or SQLite schema documentation changes. The implementation
artifact records the private precondition v2 format, its visibility in
diagnostic case JSON, the state matrix, audit event, test evidence, and
forward-only boundary.

## Risks and Open Questions

- The compatibility predicate deliberately trusts an internally consistent
  unprefixed legacy digest for non-`asset_type`-dependent fields because the old
  digest cannot reconstruct a historical raw `asset_type`. This is bounded by
  exact semantic identity, known digest shape, eligible state, SQLite integrity,
  and one-way migration; unknown/v2 mismatches still fail closed.
- Previous binaries cannot consume migrated prefixed digests. The remote rollout
  must use the suspended canary/paired-backup gate above and is not authorized by
  plan implementation.
- No product choice remains open: migration-only changes are audit events, not
  user-facing receipts; real semantic changes retain existing receipt behavior.

## Completion Report

Report changed files, case-key golden evidence, state-matrix behavior, exact
SPY/NAV/outage/receipt assertions, focused/full test results, review findings,
remaining rollout risk, and the explicit next authorization boundary.
