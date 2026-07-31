# Gateflow Goal Confirmation — Cash Flow Event Generated-Field Completion

- Gate: `goal confirmation`
- Work unit: `cash-flow-event-generated-field-completion`
- Confirmed at: `2026-07-31 23:22:02 +0800`
- Branch: `plan/holdings-validation-event-trigger`
- Base lineage: `main@49c99e5`
- Status: `confirmed`

## Goal and motivation

Manual Feishu `cash_flow` insertion and editing must no longer depend on an
operator remembering to run `pm cash-flow reconcile --apply --confirm`.
Feishu record-change events shall become a durable trigger that fresh-reads the
exact record and completes safe system-managed fields automatically.

The transport and recovery model should match the existing holdings event
listener, while the business decision remains cash-flow-specific.

## Success signals

- `record_added` and `record_edited` events for the exact configured
  `cash_flow` table are durably accepted before callback return.
- A worker claims, retries, and processes exact record IDs using fresh Feishu
  facts rather than trusting event payload fields.
- Complete CNY manual rows converge automatically to the canonical generated
  fields and pass a fresh readback/reconcile check.
- A complete foreign-currency row is auto-completed only when its existing
  local FX confirmation still matches the fresh row; missing or stale evidence
  remains fail-closed.
- Missing manual fields, invalid values, foreign FX evidence gaps, and uncertain
  write outcomes enqueue durable operator receipts rather than being guessed.
- Duplicate delivery, worker retry, and the listener's own Feishu writeback are
  idempotent; a converged record becomes a semantic no-op.
- Daily NAV cash-flow preflight remains the final fail-closed backstop.

## Scope boundary

Included:

- shared Feishu Bitable event transport for holdings and cash flow;
- exact target routing by app, file token, and table ID;
- a separate durable cash-flow event inbox using the existing operation-state
  database;
- exact-record cash-flow classification, safe auto-completion, fresh readback,
  retries, audit outcome, and typed receipts;
- local CLI, installer template, tests, and operator documentation required to
  run the combined listener in a future controlled release.

Excluded:

- creating or changing a live Feishu subscription;
- deploying, restarting services, publishing a release, or changing remote
  runtime state;
- automatically confirming or applying cash-flow effects to CASH holdings;
- adding a new FX provider, guessing a historical rate, weakening local FX
  evidence, or writing FX evidence into Feishu;
- changing NAV calculation, NAV finality, holdings reconciliation policy, or
  the holdings manual-confirmation boundary;
- implementing a general workflow engine or supporting unrelated Bitable
  tables.

## First-principles judgment and direct evidence

The work unit is justified because the present source of truth has a trigger
gap:

- `CashFlowRepository.reconcile_cash_flows()` derives generated fields but only
  writes when explicitly called with `dry_run=False`.
- `cmd_cash_flow_reconcile()` defaults to dry-run and requires both `--apply`
  and `--confirm` for a write.
- `DailyNavJobService._cash_flow_blocker()` calls reconciliation in dry-run mode
  and blocks instead of repairing pending rows.
- `portfolio-cash-flow-scan.timer` invokes `cash-flow effects scan`; it discovers
  holding effects and does not reconcile generated fields.
- the existing long-connection target resolves only the configured holdings
  table, while the remote subscription is document-level and the event payload
  already carries `table_id` and exact `record_id` actions.

Therefore an event-triggered application workflow removes the forgotten manual
step without moving FX or holdings authority into the transport layer.

## Confirmed policy decisions

- Reuse the holdings listener's durability and recovery pattern, not its
  no-write business policy.
- Automatically write only system-owned fields whose inputs are fresh and whose
  authority is already established.
- CNY uses the canonical deterministic rate `1.0`.
- Foreign-currency generated fields require a still-valid local FX confirmation;
  otherwise notify and require the existing exact-record confirmation flow.
- Receipt delivery is asynchronous and durable; event processing must not wait
  for Feishu message delivery.
- The future runtime uses one long connection and fans each payload into exact
  table-specific inboxes. Unknown tables are filtered.

## Why this is not overdesigned

The plan reuses the existing SDK adapter, SQLite operation-state store, leased
inbox pattern, receipt outbox, repository reconciliation, and systemd unit. It
adds one table-specific inbox and handler instead of introducing a generic
workflow framework, cross-table transaction protocol, new external service, or
new FX authority.

## Blocking open questions

- None. The user confirmed this boundary in conversation.

## Next entry point

`plan`
