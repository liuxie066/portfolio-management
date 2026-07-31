# Implementation Plan — Cash Flow Event Generated-Field Completion

- Gate: `plan`
- Work unit: `cash-flow-event-generated-field-completion`
- Goal confirmation: `docs/gateflow/cash-flow-event-generated-field-completion/goal-confirmation.md`
- Branch: `plan/holdings-validation-event-trigger`
- Base lineage: `main@49c99e5`
- Status: `ready for plan review`

## Goal, motivation, and success signal

Replace the easily forgotten manual post-entry reconciliation step with a
durable Feishu `cash_flow` record-change trigger. The listener must converge
safe generated fields automatically, notify on evidence/conflict paths, and
leave NAV and cash-holding authority fail-closed.

Completion is proven when exact cash-flow events survive duplicate delivery and
restart, CNY rows auto-converge with readback, valid-confirmed foreign rows may
converge without weakening FX evidence, invalid/unsupported rows produce one
semantic durable receipt, self-write events are no-ops, holdings listener
behavior is unchanged, and the full repository test suite passes.

## Non-goals and scope boundary

- No live Feishu subscription, remote service action, deployment, release, tag,
  or production data write.
- No automatic CASH holding effect confirmation or holding mutation.
- No new FX provider or guessed historical rate.
- No changes to NAV calculation/finality or the holdings validation policy.
- No generic workflow engine and no event support for unrelated tables.
- No deletion or destructive rename of the existing holdings event inbox.

## Design alignment and direct code evidence

There is no separate design document. This plan follows the confirmed goal and
these source contracts:

- `src/app/holdings_event_service.py` validates schema 2.0, exact app/file/table
  routing, bounded action lists, event IDs, revisions, and payload digests.
- `src/app/holding_event_inbox_service.py` establishes callback-only durable
  acceptance, leased asynchronous claims, retry, fresh exact-record reads, and
  atomic event/case/receipt completion.
- `src/feishu/holdings_event_adapter.py` shows the subscription is file-level
  while table routing is local.
- `src/feishu/repositories/cash_flow_repository.py` owns generated-field
  derivation and exact-record reconciliation.
- `src/app/nav_record_service.py` currently owns the matching rules for local FX
  confirmation and must not be allowed to drift from the listener decision.
- `src/app/operation_state_store.py` owns durable FX confirmations, event inboxes,
  and typed operation receipt outbox state.
- `src/app/cash_flow_effect_service.py` remains the separate owner of applying
  cash-flow effects to holdings.

## Architecture and ownership decisions

### 1. Share transport, keep domain handlers separate

Add a narrow multi-target Bitable adapter that registers the existing record
change event once, subscribes each unique configured file token once, and
forwards the raw payload to a callback. Preserve
`FeishuHoldingsEventAdapter` as a compatibility wrapper for holdings-only CLI
and tests.

The combined listener callback fans a payload into the holdings and cash-flow
inbox acceptors. Each acceptor performs its own exact target validation; exactly
one accepts a known table and unknown tables are filtered without durable state.
No business decision is made in the SDK adapter.

Before status, subscription, or listener startup, a target-registry preflight
must require distinct `(app_id, file_token, table_id)` tuples for holdings and
cash flow. `status` reports the collision read-only; `subscribe` and `listen`
refuse it before any SDK request or worker startup. Same file token with distinct
table IDs is valid, as is the same table-ID string under distinct file tokens.

### 2. Add an independent cash-flow inbox, reuse private store mechanics

Add an additive `cash_flow_event_inbox` table with the same delivery-state
columns and lease semantics as `holding_event_inbox`. Preserve the existing
holdings table and public wrappers. Factor only private SQL helpers that can be
safely parameterized by an internal constant table name; do not expose caller-
provided table names or introduce a public generic workflow abstraction.

Cash-flow completion atomically records the processed event outcome and inserts
any typed operation receipts. Remote Feishu write and local completion cannot be
transactional; recovery therefore relies on fresh exact-record reconciliation:

- remote write succeeds, local commit fails -> retry fresh-reads a converged row
  and completes as a no-op;
- request or readback fails -> event remains retryable;
- permanent data/evidence problem -> durable receipt and processed outcome are
  committed together.

The cash-flow event retry budget is exactly four processing attempts. Failure
after attempts 1, 2, and 3 schedules another claim after 1, 5, and 15 minutes.
Failure on attempt 4 atomically enqueues an operator-attention receipt and marks
the event `processed` with outcome `attention_required`; it is never claimed
again automatically. A later Feishu edit has a new event ID and may trigger a
fresh attempt. The receipt points to exact-record reconcile/status commands.

### 3. Keep cash-flow derivation in the repository

The event application service calls `reconcile_cash_flows(record_id=...,
dry_run=True)` for classification. It must not duplicate formulas for
`flow_type`, `exchange_rate`, `cny_amount`, `dedup_key`, or `source`.

For an eligible write it acquires a same-host exact-record lock, repeats the
fresh preview, calls exact-record reconciliation with `dry_run=False`, and then
performs a fresh dry-run/readback. A successful completion requires one scanned
record, zero errors, and zero remaining changes. A disappearing record is an
audited no-op.

### 4. Centralize FX-confirmation validation

Extract the existing local FX confirmation comparison from
`NavRecordService._assert_cash_flow_ready_for_write()` into a small
cash-flow-owned helper/service. Both NAV and the event handler call the same
function. It compares record ID, source hash, exchange rate, CNY amount, exact
flow date, nonempty source, and allowed evidence type.

- CNY rows do not require FX confirmation and may auto-complete.
- Foreign rows may auto-complete system fields only when the preview row is
  non-error and its current local confirmation matches exactly.
- Missing/stale confirmation or missing historical rate is permanent operator
  attention, not a retryable provider outage and not an auto-write.

### 5. Typed semantic receipts

Add cash-flow receipt types and a renderer to the existing operation receipt
dispatcher. Receipt keys are semantic, derived from record ID, current record/
error digest, and reason code rather than Feishu event ID. Redelivery of the
same unresolved row therefore enqueues one receipt; an actual edit produces a
new digest and can notify again.

The issue fingerprint contract is:

- `record_id` and stable `reason_code`;
- normalized manual inputs only:
  `flow_date/account/broker/amount/currency`;
- for FX-specific reasons, the latest local confirmation identity and its frozen
  `source_hash/exchange_rate/cny_amount/exchange_rate_date/source/evidence_type`,
  or an explicit `no_confirmation` marker.

The digest explicitly excludes `updated_at`, generated fields, remark, event ID,
revision, receipt delivery state, and wall-clock time. Normalization uses the
same canonical JSON/decimal/date representation for valid and parse-error rows.

Receipts cover:

- invalid or missing manual fields;
- missing/stale foreign FX evidence;
- write outcome requiring operator attention after retry policy is exhausted or
  a non-converging fresh readback;
- unsupported/conflicting generated-field state that cannot be safely owned by
  the automatic policy.

The action text points to the existing exact-record
`pm cash-flow reconcile --record-id ...` workflow. Successful automatic
completion is silent to avoid routine message noise.

### 6. Preserve runtime and CLI compatibility

Add `pm events status|subscribe|listen` as the combined interface. `subscribe`
and `listen` retain explicit `--confirm`; `status` is local/config-only and
performs no Feishu request. Preserve existing `pm holdings events ...`
commands unchanged.

Update the existing `portfolio-holdings-event-listener.service` template in
place to execute the combined `pm events listen` command. The legacy unit name
is retained to avoid duplicate listener units during upgrade; documentation
must state that it now covers both configured tables. This work only changes the
template and docs, not any installed unit.

## Contract, schema, state-machine, and public-interface changes

### Additive SQLite schema

`cash_flow_event_inbox`:

- `event_id` primary key;
- `event_type`, `file_token`, `table_id`, `revision`, `action_list_json`, and
  `payload_digest` frozen trigger evidence;
- `state`: `pending`, `claimed`, `failed_retryable`, or `processed`;
- `attempt_count`, `next_attempt_at`, `claim_id`, and `claimed_at` lease/retry
  state;
- `outcome_json`, `last_error`, `received_at`, and `updated_at` audit state.

No existing table or row is renamed, deleted, or rewritten.

### Event states

```text
callback -> pending
pending|failed_retryable -> claimed
claimed -> processed
claimed -> failed_retryable
expired claimed -> failed_retryable -> claimed
```

`processed` is terminal for one Feishu event ID. Duplicate event ID with an
identical frozen payload is absorbed; the same ID with different payload raises
a collision error.

Cash-flow attempts 1-3 may enter `failed_retryable`; the fourth processing
failure performs the atomic `processed/attention_required + receipt` transition.

### Record outcomes

- `completed`: generated fields were written and fresh validation converged.
- `already_complete`: fresh preview had no pending changes and required FX
  evidence was valid.
- `stale_record_missing`: the event's record no longer exists.
- `attention_required`: a semantic receipt was atomically enqueued.
- transient exception: no terminal outcome; inbox retry state advances.

The last allowed transient failure is converted to `attention_required`; the
state machine has no invisible forever-retry branch.

### Public interfaces

- New `pm events status|subscribe|listen` commands.
- Existing `pm holdings events ...`, repository methods, and NAV interfaces stay
  compatible.
- New typed operation receipt names are internal durable contracts and must be
  registered by the dispatcher before the listener can enqueue them.

## Implementation slices

### S1 — Shared Bitable transport and additive cash-flow inbox

- **Objective**: accept and recover exact cash-flow events without changing any
  cash-flow or holdings business row.
- **Allowed files/modules**:
  - `src/app/cash_flow_event_service.py` for target normalization only;
  - `src/app/cash_flow_event_inbox_service.py` for callback/lease shell;
  - `src/feishu/bitable_event_adapter.py`;
  - compatibility-only edits in `src/feishu/holdings_event_adapter.py`;
  - `src/app/operation_state_store.py`;
  - corresponding unit tests.
- **Exact changes**:
  - exact `cash_flow` target resolution and bounded event normalization;
  - additive inbox schema and accept/claim/fail/get/status/complete primitives;
  - multi-file adapter with unique-token subscription;
  - no domain writes and no receipt rendering yet.
- **Invariants**:
  - callback does no remote read or write;
  - other app/file/table events are filtered;
  - event payload fields never become financial facts;
  - existing holdings adapter, inbox, and tests retain behavior.
- **Tests**:
  - target mismatch, malformed/oversized payload, duplicate/collision;
  - holdings/cash-flow target collision refusal before subscription/listening;
  - callback budget, claim lease expiry, retry, terminal processing shell;
  - same-base unique subscription and distinct-base multi-subscription;
  - existing holdings event suites.
- **Completion signal**: durable cash-flow event ingress is testable but cannot
  mutate Feishu.
- **Stop condition**: any required destructive migration or change to holdings
  event semantics.

### S2 — Exact-record completion policy and receipts

- **Objective**: implement safe automatic generated-field convergence and
  operator-attention outcomes.
- **Prerequisite**: S1 accepted.
- **Allowed files/modules**:
  - `src/app/cash_flow_event_completion_service.py`;
  - a small shared FX-confirmation validation module;
  - `src/app/nav_record_service.py` only to delegate existing comparison logic;
  - `src/process_lock.py`;
  - `src/app/cash_flow_receipt_service.py`;
  - `src/app/operation_receipt_outbox_service.py`;
  - `src/app/cash_flow_event_inbox_service.py` integration;
  - focused tests.
- **Exact changes**:
  - exact-record lock and repeat-preview-before-write;
  - CNY eligibility, valid-confirmed foreign eligibility, exact apply, and fresh
    convergence check;
  - permanent attention classification with semantic receipt keys;
  - transient exceptions remain retryable;
  - atomic processed outcome plus receipt insertion.
- **Invariants**:
  - only repository reconciliation writes generated fields;
  - manual input fields are never patched;
  - no foreign auto-write without matching local confirmation;
  - no cash-holding write or effect confirmation;
  - no success before fresh post-write convergence.
- **Tests**:
  - CNY added/edited auto-completion and readback;
  - duplicate/self-write event no-op;
  - missing fields and malformed values -> one semantic receipt;
  - foreign missing/stale confirmation -> receipt/no write;
  - valid confirmed foreign row -> safe convergence;
  - record deletion -> audited no-op;
  - remote write success plus local failure -> retry/no duplicate financial
    mutation;
  - read/write outage -> failed_retryable;
  - failures 1-3 schedule 1/5/15 minute retries and failure 4 atomically becomes
    processed attention with one receipt;
  - concurrent edit/non-convergence -> retry or attention without false success;
  - receipt fingerprint stability across redelivery, generated-field-only edits,
    and `updated_at`, plus change on every material manual input or FX
    confirmation change;
  - NAV FX validation regression tests.
- **Completion signal**: handler reaches only proven terminal outcomes or durable
  retry state.
- **Stop condition**: a safe implementation would require provider FX lookup,
  Feishu CAS support, or changing manual fields.

### S3 — Combined runtime entry point and operator documentation

- **Objective**: run holdings and cash-flow inboxes from one long connection
  while preserving explicit external-action boundaries.
- **Prerequisite**: S2 accepted.
- **Allowed files/modules**:
  - `scripts/pm.py`;
  - `scripts/install_linux.py`;
  - dependency declarations if the existing SDK declaration needs adjustment;
  - `docs/deploy-linux.md`, `docs/schema.md`, and the cash-flow runbook;
  - CLI/installer/docs tests.
- **Exact changes**:
  - `pm events status|subscribe|listen`;
  - target-registry uniqueness preflight before any SDK call or worker start;
  - one adapter callback fans out to both exact inbox acceptors;
  - two worker loops share one stop lifecycle;
  - existing unit template switches only its ExecStart to the combined command;
  - docs distinguish file subscription, table routing, deterministic writes,
    manual FX attention, and no holding-effect auto-confirm.
- **Invariants**:
  - `status` remains read-only;
  - `subscribe` and `listen` require `--confirm`;
  - no command silently subscribes or writes production data during tests;
  - existing holdings CLI remains compatible.
- **Tests**:
  - parser and confirmation guards;
  - target collision produces read-only status failure and refuses subscribe/
    listen without an SDK call;
  - one same-base subscription and multi-base behavior;
  - fan-out accepts exactly one configured table and filters unknown tables;
  - shutdown joins both workers;
  - installer unit and docs assertions;
  - existing CLI/install tests.
- **Completion signal**: local future-release artifacts describe and launch one
  combined listener; no live subscription or service action has occurred.
- **Stop condition**: installer compatibility requires disabling/deleting a live
  unit or any remote action.

## Validation commands and expected assertions

Use Python 3.12 and disable pytest cache artifacts:

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_cash_flow_event_service.py \
  tests/test_cash_flow_event_inbox_service.py \
  tests/test_feishu_bitable_event_adapter.py
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_holding_event_inbox_service.py \
  tests/test_holdings_event_service.py \
  tests/test_feishu_holdings_event_adapter.py \
  tests/test_operation_state_store.py \
  tests/test_operation_receipt_outbox_service.py \
  tests/test_install_linux.py
python3.12 -m pytest -q -p no:cacheprovider
```

Expected assertions:

- all focused and full tests pass;
- no test performs a live Feishu subscription, message send, or financial write;
- `git diff --check` passes;
- changed Python files pass the repository's available static analysis command;
- `git status` contains only the approved work-unit artifacts before each
  checkpoint.

## Documentation decision

Update operator-facing documentation because the required post-entry action
changes from a remembered manual command to an event-driven workflow. Preserve
the manual command as recovery/confirmation tooling and state explicitly that
foreign FX and CASH holding effects remain separately confirmed.

## Risks and mitigations

- **No remote CAS**: lock on the exact record, repeat the preview immediately
  before apply, patch only system fields, and require post-write convergence.
  A racing edit triggers a later event or retry; it cannot produce terminal
  success while generated fields remain pending.
- **Write succeeded but local completion failed**: retry from fresh Feishu state;
  exact reconciliation is idempotent and a converged row becomes a no-op.
- **Self-trigger loop**: generated-field write emits another event, but the next
  fresh preview has zero changes and terminates without another write.
- **Receipt storms**: semantic keys use record/evidence digest rather than event
  ID. The digest is restricted to normalized manual inputs plus reason-specific
  FX evidence, so unchanged redeliveries and generated-only changes deduplicate
  while material operator edits re-notify.
- **Listener coupling**: transport is shared, inbox/state and business handlers
  remain table-specific; failure in one worker is logged and does not stop the
  other worker loop.
- **Version skew**: schema addition is additive; existing holdings tables and
  public commands remain intact.

## Residual risks and ownership

- Cross-host or external Feishu writers cannot participate in the same-host
  lock. This is mitigated by fresh post-write convergence and belongs to a later
  work unit only if observed races remain operationally material.
- Live subscription permission, long-connection stability, and production
  receipt delivery require a separately authorized release/upgrade canary; they
  are not claimed by local tests.
- If holdings and cash flow use different Base files, the combined adapter needs
  one explicit subscription per unique file token. The plan supports this but
  does not perform it. A future subscribe call reports per-token results and is
  idempotently retryable; it does not claim rollback of a token already
  subscribed before another token failed.

## Completion report format

The final closeout will report:

- implemented listener/handler behavior and exact files;
- focused and full validation results;
- plan/code/deepreview findings and final status;
- documentation changes;
- local commits;
- unperformed external actions;
- remaining risks with owners and the next authorized entry point.

## Next entry point

`plan review`
