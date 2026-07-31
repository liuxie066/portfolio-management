# S2 Implementation — Durable Holdings Workflow and Receipts

- Work unit: `holdings-validation-completion-conflict-receipts`
- Slice: `S2`
- Prerequisite: `S1` accepted at `b8f279a`
- Status: `accepted; pending slice commit`

## Scope

- Preserve global `OperationStateStore` schema version `2` and add a separately versioned holdings workflow schema.
- Persist reconciliation cases, immutable case events, event inbox rows, and typed operation receipt outbox rows.
- Materialize deterministic cases and receipts from fresh S1 reconciliation results.
- Support one-record missing-field apply, one-case conflict resolution, and explicit recovery of uncertain apply outcomes.
- Add typed receipt rendering and dispatch without changing the existing NAV receipt state machine.
- Expose local CLI surfaces for reconcile/apply, case inspection, resolve, recover, and receipt dispatch.

## Allowed Files

- `src/app/operation_state_store.py`
- `src/app/holdings_reconciliation_service.py`
- new holdings workflow and typed receipt application modules
- `src/app/notification_shells.py` only if a shared renderer primitive is required
- `src/feishu/repositories/holdings_repository.py`
- `src/feishu/mixins/holdings_mixin.py`
- `src/storage.py`
- `src/process_lock.py`
- `scripts/pm.py`
- focused tests for the files above
- this Gateflow artifact and S2 review/fix artifacts

## Non-goals

- No live Feishu or Futu calls, table changes, event subscription, or message delivery.
- No webhook/event listener activation; that is S3.
- No NAV preflight integration; that is S4.
- No push, PR, release, deployment, service installation, or production mutation.
- No changes to existing NAV receipt retry semantics.

## Verification

- Focused store, workflow, receipt, repository, and CLI tests.
- Python compilation and diff hygiene checks.
- S2-scoped DeepReview, finding fixes, and clean re-review before the slice commit.

## Result

- Durable cases/events, inbox lease primitives, typed operation receipts, and
  explicit single-record apply/resolve/recover flows are implemented.
- Existing NAV outbox semantics remain unchanged; receipt dispatch fans out
  independently to NAV and typed operation branches.
- Initial DeepReview: `docs/reviews/code-review-20260731-213427.md`.
- Fix artifact:
  `docs/gateflow/holdings-validation-completion-conflict-receipts/fix-s2-review.md`.
- Clean re-review: `docs/reviews/code-review-20260731-214726.md` (`pass`).
- Focused regression suite: `126 passed`; Ruff, compilation, and diff checks
  passed.
