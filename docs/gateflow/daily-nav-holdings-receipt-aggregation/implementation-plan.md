# Gateflow Implementation Plan — Daily NAV Holdings Receipt Aggregation

- Work unit: `daily-nav-holdings-receipt-aggregation`
- Base: `origin/main@02ce7f8`
- Branch: `codex/aggregate-daily-nav-holdings-receipts`
- Design source: confirmed user request and current repository behavior
- Current gate: `accepted plan`
- Next entry point: `S1 implementation`

## Goal and motivation

One real Daily NAV run must produce one user-visible task receipt. Holdings
preflight must continue to durably materialize every semantic case and case
event, but automatic discovery, supersede, and external-resolution transitions
from that preflight must not generate one Feishu message per case.

The current system generates Holdings lifecycle receipts during preflight, then
sends the consolidated NAV receipt immediately and the case receipts later from
the shared operation dispatcher. A run that closed 32 cases therefore produced
33 messages in an order that hid the business sequence.

## Success signals

- A formal Daily NAV account or global preflight persists the same cases,
  lifecycle states, and reconciliation events while returning no per-case
  operation receipt keys.
- The final NAV receipt renders a `Holdings 预检` section before account NAV
  details. It reports per-account created, reopened, closed, superseded,
  pending, and blocking counts when any such count is nonzero.
- A scope with newly discovered actionable cases retains at most five frozen
  action items (Case, record, field, state, and exact command) and reports how
  many additional items were omitted.
- Once a Holdings preflight result exists, later valuation, persistence, or
  report-payload failures still carry its sanitized aggregate facts into the
  final NAV receipt.
- A global orphan preflight is represented once at task scope rather than once
  per account.
- Manual notify/resolve/apply/recover and event-listener workflows retain their
  existing individual receipt behavior.
- Re-dispatch of the same NAV `run_id` remains idempotent through the existing
  `nav:<run_id>` key.
- The regression equivalent to 13 `lx` plus 19 `sy` automatic closures yields
  one NAV receipt and zero Holdings operation outbox rows, while all 32 case
  events remain queryable.

## Non-goals and scope boundary

- No schema migration, outbox replacement, delivery timer change, Feishu API
  change, release, deployment, production job, or real notification.
- No change to case identity, validation policy, NAV blocking rules, finality,
  receipt retry/unknown semantics, or manual action commands.
- No general field-level aggregate in this work unit. Field and record values
  are retained only for bounded, actionable discovery items whose frozen
  payload already forms part of the existing receipt contract.
- No aggregation of standalone event-listener notifications because they have
  no Daily NAV parent envelope.

## First-principles judgment and direct code evidence

- `OperationStateStore._materialize_holding_cases_tx()` atomically persists
  cases/events and currently also inserts discovery and supersede receipts.
- `OperationStateStore._resolve_absent_holding_cases_tx()` atomically advances
  repaired cases and currently always inserts closure receipts.
- `HoldingsNavPreflightService` is the canonical Daily NAV caller and already
  receives workflow arrays such as `created_case_keys`, `closed_case_keys`, and
  `enqueued_receipt_keys`.
- `AccountNavRecorderService` and `DailyAccountNavService` already propagate a
  sanitized `holdings_preflight` result into each successful account item.
- `NavHistoryReceiptService` renders consolidated account rows but currently
  ignores `holdings_preflight.workflow`.
- `NavReceiptOutboxService.enqueue_and_dispatch()` already provides the durable,
  idempotent run-level envelope; another aggregate outbox would duplicate
  ownership.

The work unit is therefore valid, and the smallest coherent design is to make
per-case enqueueing an explicit workflow choice while reusing the existing NAV
receipt as the sole automatic task envelope.

## Contract and state-machine decisions

### Receipt enqueue contract

Add an `enqueue_receipts: bool = True` argument at the Holdings workflow/store
boundary. The default preserves every existing manual and event call site.

Daily NAV preflight passes `False` for:

- current-case materialization and discovery receipts;
- semantic supersede closure receipts;
- fresh-scan external/supersede closure receipts;
- global orphan discovery and closure receipts.

`enqueue_receipts=False` changes only operation-outbox insertion. Case rows,
case events, terminal state, resolution evidence, and returned transition keys
must remain identical. `enqueued_receipt_keys` must accurately remain empty.

### Aggregate result contract

Keep existing workflow key arrays as the source of transition counts. Do not
duplicate counts in storage. A rendering helper derives:

- `created_case_keys` -> 新增;
- `reopened_case_keys` -> 重开;
- `closed_case_keys` -> 关闭;
- `superseded_case_keys` -> 替代;
- preflight `case_keys` -> 待处理;
- preflight `blocking_case_keys` -> 阻断.

The Daily NAV result will retain the successful global preflight result at
top-level as `global_holdings_preflight`. Account-local data continues through
the existing `items[*].holdings_preflight` contract. Blocking global items may
still contain compatibility fields, but the renderer must render the global
summary only once from the top-level value.

`HoldingsNavPreflightService` derives a bounded action contract from the
workflow plan's already-frozen discovery receipt payloads. Each account/global
scope carries:

- up to five `action_items`, each limited to Case key, record id, field, state,
  and exact frozen command;
- `action_item_count`, the total actionable discoveries in that scope;
- `action_item_omitted_count`, the number not embedded in the NAV envelope.

Raw evidence and snapshots are excluded. Closures and supersedes remain counts
only. The bounded contract protects the Feishu message size while preserving a
direct handling path for the cases that block the task.

### Rendering and ordering

`NavHistoryReceiptService.build_message()` renders sections in this order:

1. `Holdings 预检` aggregate rows, when nonempty;
2. `账户明细`;
3. `告警`.

Transition-only rows contain no Case identifiers or raw evidence. Actionable
discovery rows include the bounded Case/record/field/state/command contract;
durable case inspection remains the complete audit/detail surface.

### Failure, retry, and idempotency

- Normal blocking preflight results still reach the final NAV failure receipt
  and show pending/blocking counts plus bounded action items.
- Once preflight returns, every later return path in
  `AccountNavRecorderService` and `DailyAccountNavService` propagates a
  sanitized `holdings_preflight`, including snapshot/valuation/NAV-record and
  report-payload exceptions. Suppressing individual receipts therefore cannot
  make committed workflow transitions disappear from a partial/failure task
  envelope.
- NAV send failures continue through the existing durable NAV outbox and do not
  change task success.
- Manual/event per-case outbox keys and delivery state machines are unchanged.
- A process death after case materialization but before the run-level NAV
  envelope is persisted can omit a non-actionable aggregate notification, but
  cannot lose audit state. This existing cross-transaction crash window is an
  accepted residual risk for informational automatic lifecycle changes and is
  not expanded into a new distributed transaction.

## Affected files and modules

- `src/app/operation_state_store.py`
  - explicit receipt enqueue switch for materialize/resolve transactions;
  - preserve all case/event state transitions when disabled.
- `src/app/holdings_workflow_service.py`
  - pass the explicit switch through workflow methods;
  - Daily NAV preflight helpers choose aggregate behavior.
- `src/app/holdings_nav_preflight_service.py`
  - account/global preflight selects aggregate behavior and derives bounded
    frozen action items.
- `src/app/account_nav_recorder_service.py`
  - propagate sanitized preflight facts across post-preflight failures.
- `src/app/daily_account_nav_service.py`
  - preserve those facts across report-payload failures.
- `src/app/daily_nav_job_service.py`
  - retain global preflight result in the task envelope.
- `src/app/nav_history_receipt_service.py`
  - derive and render global/account Holdings summaries before NAV details.
- Tests in:
  - `tests/test_holdings_workflow_store.py`;
  - `tests/test_holdings_workflow_service.py`;
  - `tests/test_holding_event_inbox_service.py`;
  - `tests/test_holdings_nav_preflight_service.py`;
  - `tests/test_daily_nav_services.py`;
  - `tests/test_nav_history_receipt_service.py`;
  - `tests/test_operation_receipt_outbox_service.py` if routing regression
    coverage needs adjustment.

## Implementation slice

### S1 — One-envelope Daily NAV Holdings notifications

- Objective: implement the complete vertical behavior without leaving an
  intermediate commit that suppresses receipts before aggregation exists.
- Allowed modules: the production and test files listed above plus this work
  unit's Gateflow/review artifacts.
- Prerequisites: accepted plan and no changes to the original dirty worktree.
- Exact changes:
  1. Add and thread the explicit `enqueue_receipts` argument with a preserving
     default.
  2. Disable individual receipts only in Daily NAV account/global preflight.
  3. Derive at most five actionable items per account/global scope from frozen
     discovery receipt payloads, with total and omitted counts.
  4. Propagate sanitized preflight facts through every later account return
     path and preserve global preflight output in the Daily NAV result.
  5. Add pure NAV-renderer helpers for workflow counts, action items, and
     ordered rows.
  6. Update preflight expectations from one receipt to zero while asserting
     case/event durability.
  7. Add manual and event regression assertions proving individual receipts
     are unchanged.
  8. Add renderer and end-to-end service tests for multi-account closure
     aggregation, blocking/pending counts, bounded exact commands and overflow,
     empty summaries, and one global row.
  9. Add snapshot/record and report-payload exception regressions proving
     committed preflight facts remain visible in partial/failure receipts.
- Non-goals: schema changes, field-level details, timer behavior, release or
  deployment.
- Completion signal: focused tests pass; full suite and static checks pass;
  code review and aggregate deepreview have no unresolved accepted findings.
- Stop condition: any evidence that disabling per-case enqueue also removes a
  case/event transition, hides a blocking preflight from the NAV receipt, or
  changes manual/event receipt behavior.

## Validation commands and expected assertions

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_nav_holdings_receipt_pycache \
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_holdings_workflow_store.py \
  tests/test_holdings_workflow_service.py \
  tests/test_holding_event_inbox_service.py \
  tests/test_holdings_nav_preflight_service.py \
  tests/test_daily_nav_services.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_operation_receipt_outbox_service.py

PYTHONPYCACHEPREFIX=/tmp/pm_nav_holdings_receipt_full \
python3.12 -m pytest -q -p no:cacheprovider

python3.12 -m compileall -q src scripts tests
```

Expected assertions include:

- automatic preflight: zero operation receipts, durable case/event transitions;
- manual and event paths: existing individual receipt counts and payloads;
- NAV renderer: one ordered Holdings section with correct per-account/global
  counts, no empty section, and no more than five actionable items per scope;
- actionable blocker rendering: exact frozen command plus correct total/omitted
  counts, without raw evidence;
- post-preflight exception paths: final item/message retains workflow counts and
  action items after snapshot/record or report-payload failure;
- repeat rendering/dispatch: stable output and no duplicate run receipt;
- full suite: no baseline regression.

## Documentation decision

No user-facing runbook change is required. The user-visible contract is fully
captured by renderer tests and this Gateflow plan. If implementation exposes a
new CLI/config option, stop because that would violate the approved design.

## Risks and open questions

- Accepted residual risk: process death between preflight state commit and NAV
  envelope persistence can omit an informational aggregate while audit remains
  durable. Owner: later work unit only if operational evidence shows this is a
  material notification guarantee.
- Compatibility risk: global blocking output is currently copied into each
  account item. Renderer logic must not multiply the global summary.
- Open questions: none.

## Why this is not overdesigned

The design introduces no schema, queue, aggregate domain object, configuration,
or general notification framework. It adds one explicit persistence option and
pure rendering logic on data already present in the Daily NAV result. It keeps
standalone workflows independent and avoids binding event notifications to a
Daily NAV-only envelope.

## Completion report format

- behavior changed and preserved boundaries;
- focused/full/static validation results;
- review findings and final dispositions;
- documentation decision;
- draft PR URL;
- residual risk and owner;
- explicit statement that release/deployment/real notification were not run.
