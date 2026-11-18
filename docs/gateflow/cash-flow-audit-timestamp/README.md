# Cash-flow audit timestamp completion

## Goal and authorization

The user approved implementation, one feature commit, push/PR and merge to main
on 2026-11-18 after reviewing the audit-only proposal. Work stays in the existing
isolated task worktree, based on a8ed54d (origin/main refreshed and identical).
No production access, recovery of the 46 real pending effects, release or
deployment is part of this iteration. The previous analysis is retained in
reports/product-quality/AUDIT_ONLY_EFFECT_PLAN.md (private diagnostic artifact).

## Implementation plan

Keep full source hashes and visible versions. For a valid cash_flow whose only
source difference is updated_at, preserve a proven terminal parent's outcome
on its new audit version. Compare every other canonical source field, identity,
mode and hash contract. Do not inherit pending or in-flight outcomes. Retain
source timestamps in audit evidence; no hash/schema migration.

Use one shared application policy for scan and authoritative source readback.
An inherited confirmation explicitly references its parent and original
completion anchor, instead of inventing a human confirmation or another write.
Keep original preview/target evidence, but label it as inherited. Verify the
anchor's completion event and confirmation, and fail closed for missing proof
or compensation references (including historical compensation: no implicit
proof of recovery). Later corrections/deletion must still compute exactly once
against the last applied financial source. Existing independent holdings/NAV
gates and fingerprint owners stay authoritative.

Completion proof requires the state-specific record_only_confirmed or applied
event with the same nonblank preview_hash, and confirmation confirmed_at/run_id.
The normal applied event is emitted only after target readback; generic terminal
states or resolved_by_observation are not proof. Validate each inherited link
and its original anchor, preserving original operator/time/target evidence.

Implement an explicit account-scoped maintenance entry for existing pending
versions: default plan-only; require a saved exact plan plus --confirm for apply.
It only accepts the immediate terminal predecessor with the same strict proof,
not an arbitrary older completion. A plan binds database identity, cutover,
effect state/version/source and predecessor evidence. Fresh complete source
readback and unchanged plan are required before local SQLite state writes.
Use the existing scanner/account locks and one transaction; preserve old fields
in append-only recovery events. Replay of an exactly completed plan is a no-op;
mixed/stale plans fail, never silently enlarge or partly recover. No holdings,
NAV, cash-flow, baseline or message writes. No generic repair framework, new
database or dependency. Expose inherited evidence via existing inspection.

Plan-only uses a SQLite URI mode=ro/query_only connection (no scan/audit or local
workflow writes). Add an optional read_only store mode, default unchanged. Add
optional require_complete=False to the existing raw cash-flow repository/mixin;
maintenance passes True through both the normal projection and the missing
updated_at fallback. Do not duplicate repository filter/projection rules.

Apply requires the saved plan, --confirm and a fresh private backup destination.
Bind the plan to database/cutover/account, complete candidate snapshots and
parent completion proof. Recompute under scanner/account locks; back up only
after exact match, then revalidate within one SQLite transaction. Full replay
of the same successful plan is a no-op; a mixed/stale plan fails. Preserve
original fields/events for reviewed rollback; do not introduce generic undo.

Production execution remains explicitly unapproved. This code is verified only
with isolated fixtures. No automatic migration or recovery runs at startup/scan.

## Validation and delivery

Regression cases: both terminal outcomes, absent-to-integer timestamp, full
hash/audit lineage, repeated scans/restart, source/identity/mode changes,
missing proof/compensation, subsequent amount correction and deletion,
independent fingerprint drift, stale plans, transaction failure and idempotent
maintenance replay. Verify no external or ledger/baseline writes for inheritance
or recovery. CLI requires explicit local execution; no implicit service fallback.

Required: full pytest; compileall; project Ruff; OpenAPI export --check; diff
check. Complete independent code review and affected re-review, exact-head CI,
then one feature commit delivered via PR and authorized merge. Preserve dirty
primary checkout; report local main sync separately if blocked.

## State

- Current gate: PR delivery; implementation and required validation complete.
- Focused plan re-review passed; all three clarifications below accepted.
- Plan findings 1–3 (docs/reviews/plan-review-20261118-094636.md) accepted:
  exact completion proof, strictly read-only planning, owned complete source
  projection. Clarifications above address them before implementation.
- Source of prior acceptance: latest user instruction to implement all steps.
- Residual: the 46 production records remain pending, owned by the separate
  explicitly authorized production-recovery decision after a concrete plan.
- Historical compensation cases remain fail-closed and require individual
  review; not automatically accepted by this timestamp-only rule.

## Implementation and verification evidence

- Shared completion policy retains the full source version and validates each
  inherited link against its original state-specific completion event. Scanner
  and authoritative source readback use the same policy.
- Explicit CLI recovery uses read-only planning, complete source reads, a saved
  exact plan, private online backup, one transaction and zero financial writes.
- Independent full review: local artifact
  docs/reviews/code-review-20261118-180046.md. Its two findings were accepted:
  backup now reserves a new file at 0600 before SQLite writes; replay verifies
  the recovered child's full inherited evidence and exact event proof.
  Regression tests cover both fixes, including interrupted backup and altered
  preview/target evidence.
- Python 3.12.13: 2061 tests passed (2 existing dependency warnings), including
  41 timestamp-specific cases. Compileall, Ruff 0.15.14, OM OpenAPI drift check
  and diff whitespace check passed. CLI parser/help verified locally.
- Logs: reports/product-quality/audit-timestamp-{targeted,full,compile}.log
  (local ignored artifacts). No production or external end-to-end claim.
- HTTP/OM response schemas, financial ledgers, configuration, dependencies,
  database schema and hash-contract version are unchanged. VERSION remains
  0.2.16; release/deployment and real recovery are outside this iteration.
- Feishu and SQLite cannot share a transaction. Edits after fresh readback
  remain the normal scan/NAV gate's responsibility. Incomplete raw rows reject
  the account's plan; replay never expands into skipped or new pending rows.
