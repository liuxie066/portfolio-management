# Gateflow Plan Review Fix

- Gate: `fix`
- Work unit: `holdings-validation-completion-conflict-receipts`
- Reviewed finding sources:
  - `docs/reviews/plan-review-20260731-192839.md`
  - `docs/reviews/plan-review-20260731-201716.md`
- Fixed target: `docs/gateflow/holdings-validation-completion-conflict-receipts/implementation-plan.md`
- Branch: `plan/holdings-validation-event-trigger`
- Status: `fix complete; plan re-review pass-with-risks`
- Scope: documentation and plan contracts only; no implementation, external
  Feishu configuration, subscription, production write, commit/push, release,
  or deployment

## Product decision

The initial provisional choice allowed an added/edited holdings event to apply
`missing_completable`. During re-review preparation, the official update-record
contract was found to provide no revision/compare-and-set precondition, so a
background fresh-read/patch sequence could overwrite a human value written in
the intervening window. The user confirmed the safer superseding decision:

- event processing is validation-and-notification only;
- every outcome, including `missing_completable`, creates a durable case and
  receipt when actionable;
- every holdings-field write requires an explicit one-record/one-case CLI
  command with `--confirm`;
- populated conflicts, `missing_manual`, invalid rows, and orphans remain
  manual and blocking according to the field policy.

## Finding disposition

### PR-HOLD-EVENT-01 — accepted — 已修复

The plan now includes the exact
`drive.file.bitable_record_changed_v1` long-connection entry for the configured
Base and holdings table. The goal, non-goals, success signals, source boundary,
S3 ownership, tests, installer service boundary, and rollout authorization are
explicit. It adds no polling timer, public webhook, Base automation, other
tables, generic event framework, or deletion workflow.

### PR-HOLD-EVENT-02 — accepted — 已修复

The plan records exactly one caller of the missing-only apply path: confirmed
single-record CLI apply. Event `event_validate_notify` can only validate,
materialize cases, and queue receipts; it cannot call apply, resolve, or
recover. Conflicts/manual/invalid/orphan outcomes remain manual and blocking
according to the existing field matrix.

### PR-HOLD-EVENT-03 — accepted — 已修复

The plan adds a transport-only `holding_event_inbox`, `event_id` uniqueness,
durable-before-return ordering, claim/retry states, exact resource filtering,
fresh-record worker processing, multi-action replay, semantic no-op suppression,
and explicit separation between transport and business identities. S2 owns the
durable inbox contract; S3 owns the SDK adapter, singleton receiver/worker,
case/receipt notification, subscription/status CLI, disabled-by-default unit,
and failure/restart/loop tests. Service startup owns migration/integrity work;
the callback uses a pre-initialized inbox accept path with a one-second SQLite
busy deadline and a two-second receiver budget, failing without acknowledgement
when durability is unavailable. NAV preflight remains an independent fresh-read
backstop.

## Validation

- Reviewed all changed plan sections for goal/non-goal consistency, state and
  authority ownership, implementation slice sequencing, test obligations,
  rollout separation, and residual-risk classification.
- No code or live dependency validation is applicable to this plan-only fix.
- Re-review result:
  `docs/reviews/plan-review-20260731-201921.md` — `pass-with-risks`.

## Latest re-review finding disposition

### PR-HOLD-EVENT-04 — accepted — 已修复

The one-record/one-case and explicit-confirmation rule now applies only to
completion/correction writes initiated by the new reconciliation workflow. The
existing separately confirmed Futu synchronization writer keeps its current
authority, lock, and tests; it is not a reconciliation apply caller, and its
post-write raw rows still pass the same NAV validation gate.

### PR-HOLD-EVENT-05 — accepted — 已修复

Discovery receipts now freeze state-specific actions: pending apply includes the
exact one-record reconcile/apply/confirm command; pending conflict includes the
case resolve command and allowed decisions; pending manual edit includes repair
and confirmed notify/rescan guidance that can record `resolved_external`
without changing holdings. S2 renderer acceptance covers all three.

### PR-HOLD-EVENT-06 — accepted — 已修复

The transport inbox no longer owns or hands off remote apply uncertainty. Its
processed outcome contains only action dispositions plus case/receipt keys and
must never contain an apply attempt or holdings-mutation result. Apply
uncertainty remains exclusively in cases reached through explicitly confirmed
CLI commands, with an S3 negative test protecting that boundary.

## Residual risks

- Feishu event/subscription outage delays prompt processing but is covered for
  correctness by later S4 NAV preflight; operations SLO/alerting is assigned to
  a later work unit.
- Formula-only changes do not trigger the record event and are covered by fresh
  NAV preflight.
- Listener topology remains one singleton in the current same-host deployment;
  multi-host ingestion stays assigned to a later work unit.
- Record deletion policy is explicitly outside this missing-field completion
  work unit.
