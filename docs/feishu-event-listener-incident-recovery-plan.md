# Feishu Event Listener Incident Recovery Plan

- Incident date: 2026-08-17
- Production host: `liuxie-incus`
- Listener app: `cli_aae7f9dc6bf95be4`
- Service: `portfolio-holdings-event-listener.service`
- Status: local source work unit complete; production incident remains open

## Goal and completion contract

Restore and prove Feishu Bitable record-change delivery for both configured
production targets without weakening any financial write boundary:

- Holdings: file `JxtqbK2uSaw22tssuO6cDLfBnKg`, table
  `tbl6HZQqQPEqfpUr`.
- Cash Flow: file `IujkbawNXakL2Ls3dlwcOR24nCf`, table
  `tbls769bHLrw7ql0`.

The incident is complete only when all four independent contracts have current
evidence:

1. **Upstream**: the Listener app's published configuration contains long-
   connection delivery and `drive.file.bitable_record_changed_v1`.
2. **Transport and routing**: one new, genuine event for each target has the
   same Feishu `event_id` in upstream logs and the corresponding durable local
   inbox; each event is accepted by exactly one target and reaches `processed`.
3. **Cash Flow completion**: one naturally occurring eligible Cash Flow row
   entered with generated fields blank and converged through exact-record
   reconcile/readback. A later self-write event is an `already_complete` no-op.
4. **Financial authority**: listener processing does not mutate CASH holdings.
   Any resulting effect remains pending until an operator separately chooses
   `apply_delta` or `already_reflected` with the existing preview/confirm flow.

Transport recovery may be reported separately for one target, but the combined
listener incident must not be closed until both target routes are proven. Cash
Flow completion must not be claimed from an event for an already-complete row.

### Source work unit completion contract

The local source work unit is complete when S1 and S2 are implemented, focused
checks and the full pytest/compileall/diff-check baseline pass, every changed
file passes lint, the implementation artifact is current, and repeated
DeepReview has no unresolved material finding. Project-wide lint must also pass
unless every remaining finding is proven unchanged from the inherited baseline
and recorded explicitly; such inherited findings do not authorize unrelated
cleanup. That completion does not close the production incident.

The production incident remains open until separately authorized source
delivery, release, upgrade, any required listener restart, and the Phase 3
natural-event acceptance produce the correlated evidence above. Absence of
that later authority or of a natural event is not a failure of the local source
work unit and must not be used to broaden its scope.

## Confirmed incident evidence

- Both Base documents returned `is_subscribe=true` through the Listener app on
  2026-08-17. Document re-subscription is not a default repair action.
- The production service was active with an established WebSocket connection
  and no restart loop.
- `cash_flow_event_inbox` and `holding_event_inbox` both contained zero rows,
  including after later writes to both production tables.
- The affected source record was `recvswtZXpJt7e`. The initial effect
  `cfe_5a0c39acd9a1462d94eac0004dffba36` was blocked at
  `2026-08-17T12:00:05.756603` Asia/Shanghai because all five generated fields
  were absent. Exact-record reconciliation created the replacement effect at
  `2026-08-17T12:37:32.449211`; its authorized holding application completed at
  `2026-08-17T12:38:10.810790`.
- The existing adapter registers the correct inbound event type, but
  `FeishuBitableEventAdapter.start()` discards the combined callback outcome.
  Target mismatches return `accepted=false` without writing a durable inbox
  row. An empty inbox therefore proves no target-matched durable acceptance;
  it does not by itself prove that the SDK callback never ran.
- The currently available browser identity cannot view Listener app
  `cli_aae7f9dc6bf95be4` in the Feishu developer console.
- A read-only production audit on 2026-08-18 established an independent local
  runtime failure in the active `v0.1.40` listener:
  - `pm_operation_state.sqlite3` passed `PRAGMA quick_check`, its parent and
    database files were writable by the service user, and both event inboxes
    still contained zero rows;
  - the listener process had a soft open-file limit of 1024 and fluctuated
    between 986 and 1016 open descriptors; one sample contained 1009
    descriptors for the operation-state SQLite database or its WAL;
  - from `2026-08-17 23:45:10` Asia/Shanghai through the inspected window, the
    unit emitted 21,237 `unable to open database file` worker failures and no
    adapter/callback outcome logs;
  - the API process had approximately 20 descriptors and the other PM units
    had no matching database/open-file failures.
- Direct source evidence explains the descriptor exhaustion:
  `OperationStateBase._connect()` and `_connect_inbox_accept()` return raw
  `sqlite3.Connection` objects, while every operation-state caller uses them as
  context managers. Python's connection context manager commits or rolls back
  but does not close the connection. The two resident event workers therefore
  leak connections during one-second polling.
- This descriptor leak proves the current listener is unable to accept events
  reliably. It does not by itself prove that it caused the earlier 12:37 event:
  exact upstream payload/client correlation remains required before changing
  target normalization or duplicate-client ownership.

## Authority and non-goals

The following are separate authorization boundaries and must not be bundled:

1. read-only Feishu app/event-log inspection;
2. Listener app configuration or permission mutation and app publication;
3. production listener service restart/disable;
4. source change, commit/push, release, and production upgrade;
5. any controlled production Base probe;
6. any Cash Flow effect decision or holding write.

This plan does not:

- replay or recreate the already resolved `2026-08-17` cash-flow effect;
- call `pm events subscribe --confirm` while both subscriptions remain true;
- auto-apply CASH effects;
- add a generic event-health state machine, new SQLite telemetry schema, or
  periodic synthetic heartbeat;
- treat process liveness, document subscription, or receipt delivery as proof
  of end-to-end event delivery;
- create, edit, or delete a synthetic production business row without a
  separate exact-payload authorization.

## Evidence model

Keep these facts separate throughout diagnosis and acceptance:

```text
published app event configuration
  -> Feishu event generated
  -> Feishu event delivery result
  -> this process received the callback
  -> exactly one local target accepted it
  -> durable inbox row
  -> worker processed outcome
  -> exact-record generated-field convergence
  -> separately authorized holding effect
```

No downstream fact proves an upstream configuration, and no upstream success
proves downstream processing. Correlation uses a new event's exact `event_id`,
target file token, table ID, and timestamps; a time window alone is
insufficient when an event ID is available.

## Phase 0 — establish executable ownership and immutable evidence

This phase is read-only. Stop before mutation unless all prerequisites exist.

1. Identify the owner and tenant of `cli_aae7f9dc6bf95be4`, and a person/account
   that can inspect event logs, view configuration, and publish an app version.
2. Capture the current production app state before proposing a diff:
   - event delivery mode;
   - configured event list;
   - granted and pending permissions;
   - current published app version and publication timestamp;
   - current long-connection/client information exposed by Feishu;
   - document subscription state for both file tokens.
3. Search Feishu event logs for both known post-reconciliation mutation windows,
   beginning with `2026-08-17 12:37:00` through `12:39:00`
   Asia/Shanghai. Record for every matching target event:
   `event_id`, event type, file token, table ID, generation timestamp, delivery
   status, error code/message, and any connection/client identifier.
4. Store the screenshots/export and exact event-log results with the incident
   record. Do not copy app secrets or business-field payloads into repository
   documentation.

Phase 0 exits into exactly one Phase 1 branch. Ambiguous, unavailable, or
partial logs are a stop condition, not permission to guess the app setting.

## Phase 1 — evidence-gated diagnosis

### Branch A — Feishu generated no matching event

Compare the captured published state to the required state. The proposed app
change must contain only the missing fact:

- select long-connection delivery if another delivery mode is published;
- add `drive.file.bitable_record_changed_v1` if absent;
- add only the exact permission named by Feishu's event/API error if missing;
- publish the minimal new app version.

Do not change document subscriptions, Base target identifiers, credentials, or
unrelated events. If a permission change requires tenant-admin approval, stop
until that approval is explicit. Record the prior published version as the
rollback target before publication.

### Branch B — Feishu generated an event but reports failed delivery

Use the exact Feishu error code as the repair contract. Change only the failed
precondition identified by that error, capture the before/after diff, and
publish only if publication is required. A generic permission expansion or
blind app reconfiguration is forbidden.

### Branch C — Feishu reports successful delivery but no matching inbox row

1. Inventory authorized deployments and processes using the Listener App ID.
   The production host and the local Mac have already shown no second client;
   other hosts remain unproven.
2. If a known duplicate client exists, choose the canonical production service
   and request separate authority to stop the duplicate. Preserve its logs and
   pending state before stopping it.
3. If another client cannot be accounted for, treat Listener credential
   ownership as unresolved. Do not rotate the secret inside this plan; prepare
   a separate credential-rotation and service-cutover plan.
4. If one canonical client is established but arrival remains unobservable,
   enter source repair slice S2 below. Do not mutate Feishu app configuration
   again.

### Branch D — a matching local callback is observed but no target accepts it

Capture the allowlisted event header/file/table identifiers and the two filtered
outcomes, then fix the smallest proven normalization/routing mismatch. Do not
make raw event payload fields authoritative and do not loosen target identity
matching.

## Source repair slices

The resource-lifecycle slice is justified by direct production and source
evidence and does not depend on resolving the earlier event's delivery branch.
The observability slice is justified by the absence of any callback-stage
evidence after upstream delivery was inspected. Neither slice changes Feishu
configuration, event target identity, business-field authority, or production
state.

### Source scope

- `src/app/operation_state/_base.py`
- `tests/test_operation_state_store.py`
- `src/feishu/bitable_event_adapter.py`
- `tests/test_feishu_bitable_event_adapter.py`
- a focused combined-listener test in `tests/test_pm_cli.py` only if required
  to prove `accepted_by`

### S1 — close every operation-state connection

- Convert `_connect()` and `_connect_inbox_accept()` into standard-library
  context managers that always close their `sqlite3.Connection` in `finally`.
- Preserve the current `sqlite3.Connection` context semantics around `yield`,
  so normal exit commits and exceptional exit rolls back before close.
- Preserve row factories, busy timeouts, WAL setup, receiver lock budget, DB
  path, schema, transaction ordering, public methods, and all call sites.
- Do not add a pool, persistent shared connection, FD-limit override, retry, or
  garbage-collection dependency. The ownership boundary already has exactly
  one implementation point for each connection mode.

### S2 — expose the adapter callback stages

- Use standard-library `logging`; add no dependency or durable schema.
- At the adapter boundary, distinguish and log failures in SDK marshal, JSON
  decode, and callback execution. Preserve the existing exception after
  logging so the SDK can report delivery failure.
- After successful JSON decode, retain `outcome = callback(payload)` and emit
  one structured journal record containing only allowlisted transport/routing
  data: event ID, event type, file token, table ID, `accepted_by`, success,
  stage, and an exception class when present.
- Emit success at `INFO` and failures at `ERROR`. Serialize a fixed-key JSON
  object as the log argument. Extract `accepted_by` and `success` only when the
  callback returns a mapping. Normalize `accepted_by` to a list containing only
  exact string values from `holdings` and `cash_flow`; discard the whole value
  when it is not a list, and discard every non-string or non-allowlisted list
  member. Record `success` only when its value is a real `bool`; otherwise use
  `null`. An unexpected return type is logged as `success=null`,
  `accepted_by=[]` without changing callback behavior.
- Never log app secrets, full payloads, record field values, amounts, accounts,
  or broker data.
- Marshal/decode failures may lack event identity and must log only the stage
  and exception class; do not attempt unsafe partial payload parsing. Do not
  log exception messages or tracebacks because decoder or callback exceptions
  can embed raw payload values.
- Do not modify `pm events status`. Subscription, connection, delivery, inbox,
  and completion remain distinct facts.

### Required tests

- normal `_connect()` exit commits and closes the connection;
- exceptional `_connect()` exit rolls back and closes the connection;
- `_connect_inbox_accept()` closes after both normal and exceptional exit;
- instrument the `_base.sqlite3.connect` boundary with a tracking connection
  factory, run repeated empty Holdings and Cash Flow claims, and assert every
  opened connection was explicitly closed; do not rely on GC timing or a
  platform-specific `/proc`/`/dev/fd` assertion;
- accepted Holdings callback logs `accepted_by=["holdings"]` once;
- accepted Cash Flow callback logs `accepted_by=["cash_flow"]` once;
- unknown table logs `accepted_by=[]` and creates no inbox row;
- SDK marshal and JSON decode failures identify the exact failed stage without
  logging raw input;
- callback exception is visible and is not swallowed;
- a malformed or hostile callback outcome logs only allowlisted values, omits
  every discarded value, and leaves callback/adapter behavior unchanged;
- duplicate delivery still relies on inbox event-ID idempotency and produces no
  second business outcome.

### Slice ordering and completion

1. Implement and validate S1 before S2 so adapter tests cannot hide a broken
   durable-acceptance boundary.
2. Implement S2 without changing target normalization or combined-listener
   business routing.
3. Run focused checks, the full project checks, and DeepReview over the complete
   current change. Fix every accepted finding and repeat DeepReview until no
   unresolved material finding remains.
4. Source implementation and local review do not authorize commit/push,
   release, remote upgrade, service restart, replay, or production canary.

Any source delivery, release, and production upgrade follow their normal
independent authorization boundaries. The current production tag must be
freshly re-read before selecting a rollback tag; do not hardcode the incident's
observed `v0.1.40` as a future deployment target.

## Phase 2 — apply one proven repair

Before mutation, produce an execution sheet containing:

- selected Phase 1 branch and evidence;
- exact app configuration diff, duplicate-service action, or source diff;
- authorized actor and authorization scope;
- rollback target;
- expected interruption, if any;
- acceptance event source and evidence locations.

Then apply exactly one branch. Do not combine an app configuration change,
credential rotation, code upgrade, and service restart into one unreviewable
operation.

Listener restart is not the default consequence of app publication. Request a
separate restart only when Feishu evidence or a failed post-publication natural
event shows that the existing connection must be re-established. If authorized,
restart only `portfolio-holdings-event-listener.service`, then verify unit
state, restart count, WebSocket connection, credentials preflight, and unchanged
inbox durability. Do not restart the API or timers.

## Phase 3 — layered acceptance

### A. Transport and routing acceptance

Use the next genuine record-change event for each configured table. For each
target retain:

- the upstream Feishu event-log row;
- the same local `event_id`, file token, and table ID;
- exactly one `accepted_by` target, when adapter logging exists;
- exactly one durable inbox row;
- terminal `processed` state and outcome;
- no duplicate terminal business outcome on redelivery/self-write.

If only one table produces a natural event, report only that route as restored;
leave the other route explicitly pending. Do not fabricate a production row to
turn a pending verification into a pass.

### B. Cash Flow generated-field completion acceptance

Use the next genuine eligible Cash Flow entry whose repository-owned generated
fields begin blank. Capture before, event, and fresh readback for the exact
record ID. A passing CNY case requires:

- the manual business fields remain unchanged;
- only `flow_type`, `exchange_rate`, `cny_amount`, `dedup_key`, and `source`
  converge through exact-record reconciliation;
- the inbox outcome is `completed`;
- a listener self-write event, if delivered, becomes `already_complete` and
  performs no second record update;
- `pm cash-flow reconcile --record-id <id>` returns zero remaining changes;
- `pm cash-flow review --account <account>` has no generated-field blocker for
  the record.

A foreign-currency row without exact current FX confirmation is expected to
produce `attention_required`; it is not a completion canary and must not be
made to pass by guessing FX.

### C. Financial-authority acceptance

Before and after listener processing, fresh-read the corresponding CASH holding.
It must be unchanged. A newly discovered non-Futu cash effect may be `pending`;
that is the required safe state. Applying or marking that effect already
reflected remains a separate operator workflow and is outside listener
acceptance.

### D. Immediate verification request

If natural events are not available and immediate proof is required, stop and
write a separate canary sheet containing the exact production record, exact
before/after field values, effect/fingerprint impact, rollback, and cleanup
semantics. Obtain explicit production-write authorization for that payload.
This plan does not pre-authorize such a canary.

## Rollback and stop conditions

### App configuration rollback

Republish the captured prior app configuration/version. Re-check both document
subscriptions and the event log. Do not attempt rollback by guessing the prior
event list or permission set.

### Duplicate-client action rollback

Re-enable only the previously identified service with its preserved unit/config
if disabling it caused a regression. Never run two clients merely as a rollback
experiment.

### Source/deployment rollback

Use the freshly captured pre-upgrade tag and the project's controlled upgrade
procedure. Source rollback, release, and remote downgrade remain separate
authorized actions.

Stop immediately on any of the following:

- app owner/tenant or current published version is unknown;
- event-log evidence is unavailable or does not identify the selected branch;
- a proposed permission change exceeds the exact event requirement;
- target file/table identity differs from the confirmed production registry;
- multiple clients cannot be accounted for;
- callback evidence contains unexpected business payloads or secrets;
- a canary would require an unauthorized production business mutation;
- CASH changes during listener-only acceptance;
- a repair requires replaying the resolved effect.

## Validation for the source repair

Run the project-required checks before source delivery:

```bash
python3 -m pytest tests/test_operation_state_store.py tests/test_feishu_bitable_event_adapter.py tests/test_pm_cli.py -q
python3 -m pytest tests -q
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
git diff --check
```

Passing static/unit checks does not prove live delivery. Production closure
still requires the Phase 3 correlated natural-event evidence.

## Prior review finding resolution

- **PR-01**: resolved by separating upstream delivery, adapter arrival, target
  acceptance, and inbox state; a conditional allowlisted boundary log provides
  common-event-ID correlation before routing or cluster conclusions.
- **PR-02**: resolved by independent per-target transport acceptance and a
  separate eligible Cash Flow completion acceptance.
- **PR-03**: resolved by hard preconditions for app owner/tenant, current
  published state, minimal diff, rollback version, and independent authority.
- **PR-04**: resolved by removing the proposed generic `events status`
  expansion; the only conditional code is a minimal structured callback log.
- **PRR-FD-01**: resolved by giving the local source work unit its own
  completion contract while retaining Phase 3 as the separate production
  incident-closure contract.
- **PRR-FD-02**: resolved by enforcing value-level `accepted_by` and `success`
  normalization at the adapter log boundary and requiring a hostile-outcome
  regression test.
