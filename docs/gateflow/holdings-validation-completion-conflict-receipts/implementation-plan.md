# Holdings Validation, Completion, and Conflict Confirmation — Implementation Plan

- Work unit: `holdings-validation-completion-conflict-receipts`
- Gate: `plan`
- Base: `origin/main@49c99e5`
- Status: `plan accepted; pass-with-risks`
- Review chain:
  - `docs/reviews/plan-review-20260731-163239.md`
  - `docs/reviews/plan-review-20260731-182549.md`
  - `docs/reviews/plan-review-20260731-192839.md`
  - `docs/reviews/plan-review-20260731-201716.md`
  - `docs/reviews/plan-review-20260731-201921.md`
- Scope: raw holdings validation, evidence-backed missing-field completion,
  exact-resource Feishu record-event triggering, conflict persistence and
  receipts, explicit human confirmation, cache compatibility, and official NAV
  blocking

## 1. Goal and success signals

When a row is added to the Feishu `holdings` table, PM must distinguish raw
missing or invalid input from legitimate zero/empty optional values, propose
only evidence-backed completions, and never turn a missing currency into `CNY`
by default.

Success requires all of the following:

1. The configured Feishu Base `holdings` record-added/edited event is durably
   accepted by `event_id` within the receiver deadline, then processed outside
   the callback. The event payload is a trigger, never holdings-field authority.
2. Validation reads raw Feishu fields and preserves `record_id`; model and cache
   defaults cannot hide missing input.
3. `asset_type` selects the currency resolver. Currency is decided from the
   exact asset policy and permitted evidence, not from reporting currency or a
   pricing normalizer fallback.
4. Event processing never writes holdings business fields. It fresh-reads the
   one event `record_id`, classifies all outcomes, and durably creates cases and
   receipts. Even `missing_completable` remains pending until an operator uses
   the one-record `--apply --confirm` path.
5. A populated field that disagrees with authoritative evidence is never
   overwritten by the listener. It creates a durable case and queues a Feishu
   receipt. Financially critical cases block NAV until a human confirms
   `accept-proposed` or `keep-current` with a reason; noncritical descriptor
   cases remain visible but do not invalidate valuation.
6. Confirmation survives restarts, is invalidated by relevant fact or policy
   changes, and recovers safely from remote-write/local-state partial failure.
7. Official NAV uses the exact typed holdings snapshot that passed validation;
   it does not validate one read and value another.
8. A case attributable to an account blocks only that account. A row without
   `account` is a confirmed global integrity blocker and blocks every official
   NAV until repaired.
9. Existing NAV receipt delivery keeps its behavior while holdings receipts
   gain durable, typed, retry-safe delivery.
10. NAV preflight performs a fresh raw validation independently of listener
    health, so a missed, delayed, duplicate, or formula-only event cannot admit
    invalid holdings into an official NAV.

## 2. Confirmed product decisions

The following decisions were confirmed in conversation on 2026-07-31:

- Use `asset_type` to select the currency rule, with asset/source details used
  where the type is generic or an explicit source contradicts the type.
- A human may choose `keep-current` with a required reason. The decision
  unblocks NAV only for the frozen record, authority, and policy scope; a
  relevant change reopens review.
- A holdings row without `account` blocks all official NAV and emits one global
  data-quality receipt.
- The first slice may scan a whole table or account, but every holdings write or
  conflict resolution initiated by this reconciliation workflow operates on
  only one Feishu `record_id` or one case.
- Use the exact `drive.file.bitable_record_changed_v1` long-connection event for
  the configured Base and `holdings` table as a prompt trigger. Handle only
  `record_added` and `record_edited` in this work unit.
- The event worker is validation-and-notification only. `missing_completable`,
  populated conflicts, `missing_manual`, invalid rows, and orphans all create
  durable cases and receipts; no holdings field is written from event
  processing.
- Every holdings-field completion or correction initiated by this new
  reconciliation workflow requires an explicit one-record or one-case CLI
  command with `--confirm`. This supersedes the earlier provisional
  auto-completion choice after confirming that the Feishu update-record
  contract has no atomic revision/compare-and-set precondition. It does not
  redefine the existing, separately confirmed Futu synchronization writer.
- The receiver durably accepts before acknowledging, and background processing
  fresh-reads the record. `event_id` owns transport deduplication; `case_key`
  continues to own semantic reconciliation deduplication.

### 2.1 First-principles judgment and current code evidence

The event can reduce discovery latency but cannot improve the truth quality of
its payload. Financial safety therefore comes from a fresh full-row read,
versioned authority policy, a human-confirmed single-record workflow, durable
attempt state, and readback. Prompt triggering and official NAV correctness
stay independent: NAV preflight always validates current raw facts even if
event delivery is unhealthy.

Current repository facts supporting this boundary:

- `src/feishu/repositories/holdings_repository.py` currently performs typed
  conversion/cache restoration that can synthesize `CNY`, zero quantity, and
  fallback asset type; the new validator must enter before those defaults.
- `src/app/operation_state_store.py` and the receipt dispatcher already provide
  durable SQLite/outbox and lease patterns that can own workflow and transport
  state without making SQLite the holdings source.
- `src/app/daily_nav_job_service.py` already has pre-write duplicate, cash-flow,
  and existing-final checks; the holdings gate belongs in that preflight/order,
  not in `nav_history` storage.
- `ValuationService.calculate_valuation(holdings=...)` already accepts supplied
  holdings, so an explicit validated snapshot is smaller than a valuation
  redesign.
- The repository has no Feishu event SDK, event subscription handler, or
  long-running event unit today. The listener and dependency are explicit new
  S3 ownership, not assumed infrastructure.

## 3. Non-goals and authority boundaries

- Do not write any holdings business field from the event listener/worker.
- Do not let the new reconciliation/event workflow automatically correct a
  populated holdings field.
- Do not infer `asset_id`, `account`, or `broker` from names, remarks, another
  account, or another broker.
- Do not make SQLite an investment-fact source. Feishu remains the holdings
  business source; SQLite owns workflow state, audit events, confirmations,
  and receipt delivery state.
- Do not duplicate the existing Futu portfolio synchronization writer. The
  reconciler consumes a completed Futu snapshot or post-sync Feishu rows. That
  writer retains its current separate confirmation/lock contract and is not a
  caller of the reconciliation apply path; post-sync raw validation remains
  mandatory before NAV.
- Do not use normalized price payload currency, reporting currency, FX route,
  market cache, or any `CNY` fallback as holdings-currency authority.
- Do not add interactive Feishu buttons, a general security master, batch
  correction, an HTTP mutation endpoint, a polling scheduler/timer, a generic
  event framework, or cross-host locking in this work unit.
- Do not use Base automation or a public webhook callback, listen to another
  table/app, or create record-deletion business behavior. `record_deleted` is
  recorded as ignored by this ingress and remains outside the completion scope.
- Do not synthesize historical `created_at`. Feishu record creation metadata is
  not currently preserved by the repository contract. `updated_at` is changed
  only as a consequence of an authorized holdings patch.
- Do not release, deploy, inspect or repair production rows under this plan-only
  authorization.

### 3.1 Why this is not overengineered

The plan adds one transport adapter, one durable inbox table, and one singleton
service because the external protocol is at-least-once and has a short callback
deadline. All business rules, cases, confirmation, recovery, receipts, locks,
and NAV validation remain shared with CLI/NAV paths. It deliberately avoids a
generic event bus, multi-table routing, public webhook server, polling timer,
event-sourced holdings store, security-master service, or interactive workflow.
Removing the inbox would create an acknowledge-before-durable loss window;
running reconciliation in the callback would create timeout/redelivery risk.

## 4. Raw record and field authority contract

Add an internal raw repository method that returns complete pagination as
records shaped like:

```text
RawHoldingRecord(record_id, raw_fields, source="feishu", fetched_at)
```

It must not call `_dict_to_holding()`, add query-filter values back into a row,
or default `asset_type`, `quantity`, `broker`, or `currency`. An incomplete
page/read is a source failure, not a smaller valid dataset.

Validation treats whitespace-only text as missing, distinguishes numeric zero
from a missing quantity, validates finite numbers, and reports errors per row.
It constructs `Holding` only after all required raw fields are valid or have an
approved completion.

### 4.1 Field matrix

| Field | Required for official NAV | Permitted completion authority | Populated disagreement |
|---|---:|---|---|
| `asset_id` | yes | none; manual input only | invalid/manual edit |
| `account` | yes | none; manual input only | invalid/manual edit |
| `broker` | yes | none; no alias or remarks inference | invalid/manual edit |
| `quantity` | yes; zero is valid | existing Futu sync only, not this reconciler | invalid/manual edit |
| `asset_name` | no | exact Futu position/security metadata | nonblocking conflict |
| `asset_type` | yes | exact Futu market/security type or an explicitly suffixed supported asset id | conflict |
| `currency` | yes | the versioned resolver in section 5 | conflict |
| `asset_class` | no | deterministic derivation from confirmed asset type/currency | conflict if populated |
| `avg_cost` | no | none in this reconciler | warning only |
| `industry` / `tag` | no | none in this reconciler | warning only |
| `created_at` / `updated_at` | no | excluded from standalone completion | warning only |

`detect_asset_type()` may supply a `heuristic_suggestion`, but its generic
fallbacks are not write or conflict authority. A suggestion without permitted
authority remains `missing_manual`.

### 4.2 Validation outcomes

Each field has exactly one outcome:

- `valid`;
- `optional_missing`;
- `missing_completable` with proposed value and authority;
- `missing_manual`;
- `conflict` with current/proposed values and authority;
- `invalid` with a stable reason code.

`missing_completable`, `missing_manual`, `conflict`, and `invalid` block
official NAV only when the field is required in the matrix. Noncritical cases
are still persisted/notified when materialization is authorized, but do not
block valuation.

Duplicate normalized business identity `(asset_id, account, broker)` blocks
every account represented by the duplicate rows. A missing-account row is an
`orphan` global blocker rather than being assigned to an account.

## 5. Versioned currency resolver

Use policy id `holdings-currency.v1`. The resolver outputs the proposed
currency, authority classification, authority id, source/as-of evidence, and a
reason code. Only `manual_confirmed`, `futu_explicit`, `asset_id_explicit`, and
`asset_type_policy` may write or create an actionable conflict. A syntactically
valid populated currency with no permitted contrary authority remains the
Feishu business fact with `manual_raw_unverified` provenance; it may be valued
but is never reused to complete another row or represented as externally
verified. A blank value in the same situation remains `missing_manual`.

Authority precedence for one record is:

1. an active `manual_confirmed` decision with matching confirmation scope;
2. exact-match Futu OpenD `position.currency` with account/profile/snapshot
   identity;
3. explicit `CASH`/`MMF` asset-id prefix (`CNY-CASH`, `USD-MMF`, and so on);
4. a region-specific asset type in the matrix below;
5. a generic type plus an explicit supported market suffix;
6. unresolved.

| Raw `asset_type` | Resolver behavior |
|---|---|
| `a_stock`, `cn_fund`, `otc_fund` | `CNY` by `asset_type_policy` |
| `us_stock`, `us_fund` | `USD` by `asset_type_policy` |
| `hk_stock`, `hk_fund` | select the HK resolver, but `HKD` is only a heuristic until explicit Futu trading currency or a populated manual value is available; this prevents RMB-counter misclassification |
| `cash`, `mmf` | require and parse the currency prefix in `asset_id`; type alone is insufficient |
| `exchange_fund`, `fund` | `.US` implies USD and `.SH/.SZ` implies CNY; `.HK` identifies market only and still requires explicit trading currency or a populated manual value |
| `crypto`, `bond`, `other` | require a populated manual value or later explicit evidence; missing stays manual |

An explicit source that contradicts the type does not silently choose a side.
For example, a Futu HK position reporting a non-HKD trading currency creates
both descriptor evidence and a conflict requiring human review.

The following are `defaulted_forbidden`: `src/pricing/payload.py` fallback
currency, provider-adapter market constants without raw instrument provenance,
cached normalized price currency, CNY reporting currency, and any
non-instrument-specific FX result.

The pure validator receives an immutable `HoldingsEvidenceBundle`; it never
opens a network connection. If Futu evidence is needed, orchestration calls one
read-only `observe_portfolio(account)` operation extracted from the existing
Futu provider path. It fetches at most once per account, returns source snapshot
identity/as-of plus exact position descriptors, and performs no holdings or
workflow write. Existing Futu sync reuses this observation contract rather than
the reconciler duplicating its provider or writer logic. Non-Futu external
instrument metadata is not added in v1.

`authority_id` is stable across observations (for example,
`asset_type:us_stock` or a Futu profile/position identity) and excludes fetch
time and snapshot id. Futu evidence is actionable only when the current run
completed a fresh observation; cached prior observations are audit evidence,
not completion/conflict authority. An active manual decision can remain valid
during a later Futu outage, but any newly observed semantic Futu change creates
a new case.

## 6. Stable identities, evidence, and confirmation scope

Use versioned canonical JSON plus SHA-256. Do not use one hash for three
different jobs:

```text
case_key = hash(
  contract_version, record_id, canonical_holding_identity, field, kind,
  normalized_current, normalized_proposed,
  authority_id, policy_version
)

evidence_instance_id = hash(
  source, source_snapshot_id, source_as_of, canonical_raw_evidence
)

case_precondition_digest = hash(
  record_id, identity_fields, target_field,
  authority_input_fields
)

confirmation_scope = hash(
  case_key, case_precondition_digest, authority_id, policy_version
)
```

Canonical values use trimmed text, lowercase enum values, uppercase currency,
and finite Decimal strings; hashes never contain binary float representations.
`canonical_holding_identity` matches the repository business key after
whitespace normalization only. It does not strip market suffixes or apply
heuristic ticker normalization. Provider-specific Futu code normalization is
used only to find one exact evidence match; zero or multiple matches are
unresolved rather than guessed.

`record_digest` covers all validation-relevant raw fields and identifies the
whole valuation snapshot. `case_precondition_digest` is narrower: it excludes
unrelated optional or separately repairable fields so an `asset_name` edit does
not invalidate a currency decision. Both exclude fetch time and non-semantic
transport metadata. A newer observation of the same semantic conflict updates
`latest_evidence_instance_id` without creating a new case or discovery receipt.
Current/proposed value, identity, authority input, or policy changes supersede
the old case and create a new one.

An active `keep-current` applies only while its `confirmation_scope` remains
equal. It is not a global override for the asset, type, account, or broker.

## 7. Workflow persistence and state machine

Keep the global `OperationStateStore` schema at v2 because the new tables are
additive and an old binary currently rewrites the global version marker during
startup. Add and validate a separate
`holdings_workflow_schema_version=1` meta key in the same initialization
transaction. Preserve `cash_flow_fx_confirmations` and `nav_receipt_outbox`;
never reset the database or lower a feature-schema version on migration
failure.

Add:

### 7.1 `holding_reconciliation_cases`

Core fields:

- `case_key` primary key;
- `record_id`, nullable `account`, `field`, `kind`;
- `blocks_official_nav`;
- `policy_version`, `authority_id`;
- `current_json`, `proposed_json`, `record_digest`,
  `case_precondition_digest`;
- `latest_evidence_instance_id`, `evidence_json`;
- `state`, `resolution_json`, `target_json`, `before_json`;
- `apply_attempt_id`, `remote_attempt_started_at`;
- `last_error`, `created_at`, `updated_at`.

### 7.2 `holding_reconciliation_events`

Append-only events for discovery, evidence refresh, supersession, confirmation,
apply start, remote outcome, readback, recovery, resolution, and receipt enqueue.

### 7.3 `holding_event_inbox`

This table owns only transport acceptance and worker progress. It never stores
an authoritative holdings projection:

- `event_id` primary key;
- `event_type`, event `file_token`, `table_id`, `revision`;
- canonical frozen `action_list_json` and `payload_digest`;
- `state`, `attempt_count`, `next_attempt_at`;
- `claim_id`, `claimed_at`;
- `outcome_json`, `last_error`, `received_at`, `updated_at`.

Allowed states are:

```text
pending/failed_retryable -> claimed -> processed
claimed lease expiry     -> failed_retryable
target resource mismatch -> filtered (not inserted)
deleted/unsupported only -> processed with ignored outcome
```

The receiver inserts or recognizes an existing `event_id` in one SQLite
transaction before returning successfully to the SDK. A claimed event may be
reprocessed after lease expiry because downstream reconciliation is idempotent.
A processed inbox outcome stores only per-action validation disposition plus
the resulting case and receipt keys. It never stores an `apply_attempt_id` or a
remote holdings-mutation outcome. Apply uncertainty belongs exclusively to a
semantic case created or acted on by an explicitly confirmed CLI command.

Concurrent delivery of the same event must serialize on the primary key. An
existing event id is a valid no-op only when its stored payload digest equals
the incoming digest; the same id with different canonical content is an
integrity error, is not acknowledged as newly accepted work, and is surfaced in
service health.

`event_id` is transport identity and must not replace `case_key`,
`evidence_instance_id`, `case_precondition_digest`, or `confirmation_scope`.
Re-delivery of one event is an inbox no-op. A new event id for PM's own patch is
fresh-read and becomes a semantic no-op when its current digest/cases are
already resolved.

### 7.4 Case states

```text
missing_completable -> pending_apply
conflict            -> pending_confirmation
missing_manual /
invalid / orphan    -> pending_manual_edit

pending_apply or pending_confirmation
  -> applying
  -> resolved_accept | resolved_keep | resolved_external
  -> superseded
  -> failed_retryable
  -> apply_outcome_unknown
```

`pending_manual_edit` may transition only to `resolved_external` after a fresh
scan proves repair, or to `superseded` when the source identity/facts change.
Any open case may be superseded by a new case with a different semantic key.

- `resolved_keep` is legal only for `conflict`, requires a nonblank reason, and
  performs no Feishu write.
- `resolved_external` is produced when a later scan proves the source row was
  manually repaired.
- `failed_retryable` means no remote mutation occurred or fresh read proves the
  exact before value remains.
- `apply_outcome_unknown` never retries automatically.
- Pending/applying/unknown/manual-edit states block only when
  `blocks_official_nav=true`. A resolved decision unblocks only while its
  confirmation scope remains valid.

## 8. Missing-field apply and conflict resolution

### 8.1 Missing-only completion

There is exactly one authorized caller of the missing-only apply path:

- explicit operator apply:
  `pm holdings reconcile --record-id RECORD_ID --apply --confirm`.

The event worker cannot call apply, resolve, or recover. In
`event_validate_notify` mode it may classify `missing_completable`, materialize
the case, and queue the frozen confirmation receipt only. A dependent field is
`missing_completable` only when all of its authority inputs are valid and
non-conflicting; for example, disputed `asset_type` cannot authorize a currency
completion proposal.

After explicit confirmation, the operator path may patch all eligible missing
fields for that one row in one request. One `apply_attempt_id` binds all
involved cases. It must:

1. perform an initial raw lookup only to identify the candidate account, then
   acquire the existing account lock followed by a record-specific same-host
   lock; no path may acquire them in the opposite order;
2. fetch the raw row again under both locks, require the same nonblank account,
   and fetch fresh permitted evidence;
3. recompute the plan and record digest;
4. reject if any target field is now populated, evidence changed, or the row is
   no longer the selected record;
5. atomically create any not-yet-materialized cases and deterministic discovery
   receipts, then commit every involved case to `applying`, with the shared
   attempt id, before/target values, trigger identity, confirmed operator
   context, and audit events;
6. immediately before the network call, durably set
   `remote_attempt_started_at` for every involved case; then send a narrow
   Feishu patch containing only still-missing target fields plus
   `updated_at`;
7. fresh-read and compare the target fields;
8. atomically store terminal states/events and enqueue closure receipts.

It never includes populated conflicts in the missing-field patch.
If readback is mixed, each field is classified independently: target matches
resolve that case, a different value supersedes it, and an attempted field
still equal to before becomes unknown. The shared attempt remains in the audit
events; one case's success cannot falsely resolve another.

An event row may contain independent `missing_completable`, manual, and
conflict outcomes. The worker materializes every actionable case and its
discovery receipt in one local transaction, then marks the inbox action
processed; it performs no holdings write. Reprocessing the event is a semantic
no-op because deterministic case and receipt identities suppress duplicates.
After a separately confirmed CLI patch, the resulting PM-authored edit event
fresh-reads the completed row and likewise performs no write or duplicate
receipt.

### 8.2 Conflict decision

One command resolves one case:

```bash
pm holdings resolve \
  --case-key CASE_KEY \
  --decision accept-proposed|keep-current \
  --reason "human-readable reason" \
  --confirm
```

Both decisions fresh-read the row and evidence and require the exact
confirmation scope. `accept-proposed` follows the same applying/readback path
and patches one field. `keep-current` atomically stores the decision and
closure receipt without a Feishu write.

Resolve and recover use the same account-then-record lock order. This
coordinates with Futu/cash-flow account writers; an external Feishu edit remains
subject to the documented read/patch/readback race boundary.

The human conflict-decision surface in this phase is local CLI only. It stores
username, hostname, command mode, and `trusted_identity=false` as audit context.
The confirmed product policy accepts this explicit local decision as
operational authority; it must not be presented as cryptographically
authenticated identity.

### 8.3 Cross-system recovery

Feishu and SQLite are not transactionally atomic. Recovery uses absolute
before/target values:

- fresh read equals target: finish as `resolved_accept` with
  `already_applied=true`;
- fresh read equals exact before value and durable state proves no remote
  attempt started: `failed_retryable`;
- fresh read differs from both: `superseded`;
- response/readback is unavailable after a remote attempt:
  `apply_outcome_unknown`, with no automatic retry.

A stale `applying` case is never blindly retried. An explicit
`pm holdings recover --case-key ... --confirm` performs the fresh-read
classification above and appends an audit event. The plan acknowledges that
Feishu exposes no atomic compare-and-set in the current client contract:
fresh-read + narrow patch + readback reduces but cannot eliminate an external
same-field race. A mismatching readback fails closed and remains auditable.
If an attempted/unknown case later reads the exact before value, it remains
unknown: the value could have been written and then changed back. Retrying it
requires a new explicit confirmation against a newly computed scope.

## 9. Durable typed receipt delivery

Keep the existing NAV-specific outbox untouched. Add an
`operation_receipt_outbox` table to `OperationStateStore` for new typed
operation receipts:

- `receipt_key` primary key;
- `receipt_type`;
- canonical `payload_json`;
- `status`, `attempt_count`, `next_attempt_at`;
- `claim_id`, `claimed_at`, `send_started_at`;
- `message_id`, `last_error`, timestamps.

Supported first-phase types are `holding_case_discovered`,
`holding_case_closed`, and `holding_case_attention_required`. The last type is
used when an apply outcome becomes unknown and still needs human action. A
renderer registry maps these types to the Feishu sender; domain services do not
depend on the concrete sender.

Case creation/event plus discovery receipt enqueue occur in one SQLite
transaction. Terminal resolution/event plus closure receipt enqueue also occur
in one transaction. Keys are deterministic:

```text
holdings:case:discovered:<case_key>
holdings:case:closed:<case_key>:<terminal_state>:<resolution_digest>
holdings:case:attention:<case_key>:<apply_attempt_id>:<state>
```

`resolution_digest` is the confirmation scope for human decisions and the
fresh external/readback digest for `resolved_external` or recovery outcomes.
For a successful explicitly confirmed missing-only apply, it is a hash of the
manual apply policy, durable `apply_attempt_id`, canonical target, confirmed
operator context, and fresh readback digest; recovery of the same attempt
therefore addresses the same closure key.
The unknown state/event and its attention receipt are committed atomically;
unknown is not mislabeled as closed.

A discovery payload contains case key, record/account/broker/asset identity,
field, current/proposed values, authority and evidence as-of, blocking flag, and
one state-specific frozen action contract:

- `pending_apply`: exact
  `pm holdings reconcile --record-id RECORD_ID --apply --confirm` guidance;
- `pending_confirmation`: exact `pm holdings resolve --case-key CASE_KEY ...
  --confirm` guidance plus the allowed decisions;
- `pending_manual_edit`: exact repair description followed by
  `pm holdings reconcile --record-id RECORD_ID --notify --confirm`, which
  fresh-reads the row and may atomically record `resolved_external` without a
  holdings write. Normal record-edit event processing may perform the same
  proof first; deterministic state/receipt identities suppress duplication.

A closure payload contains decision, reason, trigger/operator context,
before/target/readback values, and terminal state. An attention payload contains
attempt id, known remote/readback facts, and recover/receipt-resolution
guidance. Event-triggered payloads additionally contain the event id, action,
and revision but never application secrets or the whole event body. Renderers
use only the frozen payload and never reread mutable holdings.

The outbox lifecycle distinguishes claim from network attempt:

```text
pending/failed -> claimed -> sending -> sent|failed
claimed lease expiry -> failed (safe to retry; send not started)
sending lease expiry or send-success/mark failure -> unknown
unknown -> no automatic retry
```

A sender result that is itself accepted/unknown also maps to `unknown`, never
to retryable `failed`.

`pm receipts dispatch --confirm` fans out to both the unchanged NAV dispatcher
and the typed operation dispatcher. Unknown delivery needs an explicit
operator decision (`retry` or `mark-sent`) with `--confirm`; it is never reset by
lease expiry.

Fan-out executes both branches independently and aggregates their results. A
NAV dispatch exception or unknown outcome must not prevent due holdings
receipts from being attempted, and the reverse is also true. Overall status is
failed/partial if either branch is not successful.

The first phase sends one discovery receipt per case, not a grouped interactive
card. Re-observing the same case does not resend it. After the event worker has
committed all validation/case/outbox outcomes, it may ask the typed dispatcher
to send due receipts opportunistically; this occurs outside the event callback,
and any transport failure leaves the durable row for the existing receipt
timer.

## 10. Exact-resource Feishu event ingress

Use the official Python `lark-oapi` SDK long connection and only event type
`drive.file.bitable_record_changed_v1`. The data application credentials
`feishu.app_id` / `feishu.app_secret` own the connection; the receipt bot
credentials are not reused for event access. Resolve the expected
`app_token/table_id` from the existing `feishu.tables.holdings` reference plus
`feishu.app_token` when needed. Ambiguous or incomplete target configuration is
a startup failure.

The event adapter maps the SDK object into one transport-neutral internal
envelope. Domain validation and reconciliation do not import SDK event types.
The receiver callback performs only:

1. require schema 2.0, the exact event type, event `file_token` equal to the
   expected Base `app_token`, and expected
   holdings table id;
2. validate bounded required transport fields and canonicalize the action list;
3. insert-or-recognize `holding_event_inbox.event_id` transactionally;
4. return without waiting for Feishu reads, Futu observation, validation,
   reconciliation, receipt dispatch, or any business-data write.

Before the SDK connection starts, service startup must complete schema
migration, integrity validation, and inbox-store initialization. The callback
uses a dedicated pre-initialized accept path with a SQLite busy deadline no
longer than one second; it must not run migration, `quick_check`, journal-mode
changes, or other unbounded store initialization. If the inbox row cannot be
committed and read back within a two-second receiver budget, the adapter raises
an SDK-visible error and does not acknowledge the event, leaving Feishu
redelivery as the recovery path. A duplicate is acknowledged only after the
existing row's payload digest has been verified.

This ordering and bounded failure behavior provide a durable handoff inside the
three-second SDK deadline.
An unrelated app/table is filtered without invoking domain code. Malformed
target events raise a visible receiver error and remain subject to Feishu
redelivery rather than being acknowledged as valid work.

A worker loop in the same singleton service claims durable inbox rows with a
lease and processes the frozen action list. It:

1. treats `record_added` and `record_edited` as triggers; records other actions,
   including `record_deleted`, as ignored outcomes in this work unit;
2. fresh-reads each target `record_id` from Feishu; disappearing records become
   stale/no-op outcomes rather than fabricated holdings;
3. calls the same reconciliation service in `event_validate_notify` mode,
   which may write workflow cases/outbox rows but cannot write holdings fields;
4. obtains Futu evidence only through the existing read-only observation
   contract and never inside the receiver callback;
5. durably stores every action result/case/outbox handoff before marking the
   inbox event `processed`;
6. optionally invokes typed receipt dispatch only after all state commits.

One event may contain multiple record actions. Reprocessing the full frozen
list is safe: event id prevents duplicate transport insertion, case/apply
identities prevent duplicate semantic work, and fresh current-state validation
makes older/out-of-order revisions no-ops. Revision is retained for audit but
is not used to overwrite or reconstruct current holdings.

Transient read/provider/state failures move the inbox row to
`failed_retryable` with bounded backoff. The always-on worker handles due retry;
there is no polling timer. Because the event worker has no holdings-write
authority, it cannot create a remote apply outcome. A separately confirmed
PM-authored completion produces a new edit event id, but the fresh row digest
and resolved case state make it a processed semantic no-op.

The service is an installer-owned singleton
`portfolio-holdings-event-listener.service`, runs under the same user,
environment file, and `PM_DATA_DIR` as other PM units, and uses
`Restart=always`. The installer may generate the unit but must enable/start it
only with `--enable-holdings-event-listener` during a later separately
authorized upgrade. No timer or public listening socket is added.

Feishu-side activation is deliberately not implicit at service startup. The
implementation provides an exact-purpose, confirmed subscription command for
the configured holdings Base and a read-only local readiness/status command.
The later runbook must first configure the record-change event in the enterprise
custom app, publish the app configuration, call the confirmed document
subscription command, and verify a controlled canary. None of those external
mutations is authorized by this plan.

Long connection requires an enterprise custom app. Current repository
configuration proves only that app credentials exist, not the app type,
published event configuration, document owner/admin relationship, or granted
record-event permissions. The later activation preflight must verify each item
and stop if any is false; it must not silently fall back to a public webhook or
another application.

## 11. CLI contract

Refactor `pm holdings` compatibly: no subcommand remains the current list
operation, including `--account`, `--include-price`, and service behavior. New
subcommands are direct/local maintenance surfaces and are not exposed through
HTTP:

```text
pm holdings reconcile [--account ACCOUNT | --record-id RECORD_ID]
                      [--notify --confirm]
                      [--apply --confirm]
                      [--json]
pm holdings cases [--account ACCOUNT] [--state STATE] [--json]
pm holdings resolve --case-key KEY --decision ... --reason ... --confirm
pm holdings recover --case-key KEY --confirm
pm holdings events status [--json]
pm holdings events subscribe --confirm [--json]
pm receipts resolve --receipt-key KEY --decision retry|mark-sent --confirm
```

- Plain `reconcile` is strictly read-only: no Feishu write, SQLite write,
  receipt enqueue, or send.
- `--notify --confirm` materializes cases and queues discovery receipts but
  never changes holdings. A fresh repaired row may resolve or supersede an
  existing case and queue its closure receipt in the same workflow transaction.
  After commit it may opportunistically dispatch; a transport failure leaves
  the durable row queued.
- `--apply` requires `--record-id` and `--confirm`; account/all batch apply is
  rejected.
- `resolve` and `recover` each operate on one case and require `--confirm`.
- `--notify` and `--apply` are mutually exclusive.
- `events status` is read-only and reports resolved target identity, local inbox
  health, and configuration readiness; it must not claim remote subscription
  health if the platform exposes no authoritative readback.
- `events subscribe --confirm` performs only the exact configured Base document
  subscription. It neither enables the systemd service nor edits application
  event configuration and is never invoked automatically.

## 12. Cache compatibility and strict typed conversion

Bump `LocalHoldingsIndexCache.VERSION` from 1 to 2. Version 2 contains only
successfully validated typed holdings plus the validation policy version.

- Legacy unversioned and v1 payloads are ignored, not loaded into memory, and
  are atomically replaced only after a successful fresh Feishu preload.
- Remove `currency='CNY'`, `quantity=0`, and `asset_type=OTHER` synthesis from
  holdings cache restoration and raw-to-typed conversion.
- A row with missing required fields is not cached as a `Holding`.
- Account-scoped preload returns a typed integrity error containing all bad
  record ids for that account and does not mark the account cache complete.
- A global preload applies the global orphan rule. It must not let one account's
  attributed bad row masquerade as another account's result.
- Official validation and valuation never use the persistent holdings cache;
  they use the same fresh snapshot described below.

This invalidates the old fabricated-CNY path without deleting Feishu facts.

## 13. Official NAV ordering and snapshot handoff

Add a narrow internal `ValidatedHoldingsSnapshot` containing account, frozen
canonical persistent-field rows, raw record digest, normalized holdings digest,
source fetch time, policy version, and warnings. It creates private `Holding`
copies for valuation because the current valuation service mutates runtime
fields such as `current_price`, `market_value_cny`, and `weight`. Those runtime
fields are excluded from the input digest, and the frozen source rows remain
unchanged.

The official daily flow is:

1. Preserve the existing duplicate-NAV, cash-flow, and existing-final-NAV
   checks. Accounts that already have an eligible final NAV are skipped and do
   not become failures because of a holdings row added after that final NAV.
2. If at least one account still requires a new valuation, complete one raw
   global orphan scan before those account runners. Incomplete pagination or
   any missing-account row blocks all accounts still requiring valuation. A
   formal confirmed run materializes one global case/receipt; a dry-run reports
   only what would be created.
3. Inside the account runner, complete optional Futu CASH/MMF synchronization
   first. Do not hold an outer account lock while the existing Futu service
   takes its lock.
4. After sync returns, acquire the account lock, fresh-read raw account
   holdings, validate them, and freeze `ValidatedHoldingsSnapshot`; then
   release the lock.
5. A real sync validates the post-write Feishu read. A dry-run with Futu sync
   validates a projected in-memory view built only from the completed,
   authoritative Futu sync plan; if that projection is incomplete, dry-run
   fails closed rather than guessing.
6. A formal confirmed run atomically materializes every detected blocking and
   nonblocking case plus its receipt before valuation; materialization failure
   blocks rather than losing the required workflow. If blocking cases remain,
   return `holdings_confirmation_required`. If only nonblocking cases remain,
   continue with warnings. A dry-run reports all would-be cases and has no state
   or notification side effect.
7. Pass the frozen typed holdings explicitly through
   `PortfolioReadService.build_snapshot()` into the existing
   `ValuationService.calculate_valuation(holdings=...)` input. Neither layer may
   reread holdings.
8. Persist the holdings digest in NAV calculation details and include it in the
   job result/receipt so validation and valuation equality is testable.

Same-host writers coordinate through the existing account lock. External
Feishu edits after snapshot creation are represented as later facts; the NAV
records the snapshot time/digest it actually consumed.

NAV preflight never treats listener uptime, inbox emptiness, or a processed
event as validation evidence. It always performs the fresh raw scan above and
uses the same validator/case materializer. Consequently a delayed or missed
event affects prompt completion/notification latency but cannot bypass the
official NAV gate. Event processing concurrent with NAV follows the same
account-then-record lock order; NAV either observes the completed fresh row or
the still-blocking pre-completion facts.

## 14. Implementation slices

No slice is released independently. Implementation begins only after this plan
passes review and the user separately authorizes execution.

### S1 — Raw validation, resolver, and cache safety

Scope:

- raw holdings repository read with complete-pagination failure semantics;
- pure validator and `holdings-currency.v1` resolver;
- field outcomes, duplicate/orphan classification, canonical digests;
- strict raw-to-typed conversion;
- holdings cache v2 invalidation and rebuild;
- read-only `pm holdings reconcile` output.

Primary files:

- `src/feishu/repositories/holdings_repository.py`
- `src/local_cache.py`
- `src/app/holdings_validation.py` (new)
- `src/app/holdings_reconciliation_service.py` (new)
- `src/app/futu_balance_sync_service.py` for a read-only observation contract
- `scripts/pm.py`
- focused repository, validator, cache, and CLI tests

Acceptance tests:

- raw blank currency stays blank and never becomes CNY;
- raw missing quantity differs from zero;
- every asset-type resolver branch and forbidden pricing fallback;
- exact Futu evidence, generic ETF with/without explicit market, non-Futu HK
  missing currency staying unresolved, and HK source contradiction;
- Futu observation is read-only, fetched at most once per account, and stale
  cached evidence is not actionable;
- missing identity, duplicate identity, orphan, invalid enum/number;
- v1/legacy cache is ignored and v2 is rebuilt only from valid fresh rows;
- one account's attributed error does not appear as another account's result;
- plain reconcile has no local/remote writes.

### S2 — Durable cases, confirmation, recovery, and receipts

Scope:

- additive `holdings_workflow_schema_version=1` initialization while preserving
  global OperationStateStore v2;
- cases/events, event inbox/lease methods, and typed operation receipt outbox;
- atomic discovery/closure enqueue;
- receipt renderer registry and dispatch fan-out;
- single-record missing apply;
- cases/resolve/recover CLI with audit context.

Primary files:

- `src/app/operation_state_store.py`
- `src/app/operation_receipt_outbox_service.py` (new)
- `src/app/holdings_receipt_service.py` (new)
- `src/app/holdings_reconciliation_service.py`
- `scripts/pm.py`
- installer/service timer tests only if the existing dispatch invocation changes

Acceptance tests:

- feature-schema initialization preserves global v2, FX confirmations, and NAV
  outbox rows, and rejects a higher unknown holdings feature version;
- event inbox insert-or-recognize, claim expiry, retry scheduling, and processed
  outcome persistence are durable and independent of semantic case identity;
- case/event plus receipt insertion rolls back atomically;
- stable-case evidence refresh does not resend; policy/value changes supersede;
- `keep-current` requires reason and invalidates on scope drift;
- exact single-record apply and rejection of account/all batch apply;
- account-then-record lock ordering and concurrency with a Futu account writer;
- remote success followed by local commit failure, timeout-with-actual-success,
  mismatching readback, restart recovery, and double confirmation;
- claimed-versus-sending lease behavior, unknown no-auto-retry, renderer routing,
  key collision, two dispatchers, unchanged NAV outbox behavior, and exact
  action rendering for pending-apply, pending-confirmation, and manual-edit
  discovery receipts.

### S3 — Exact holdings event ingress and confirmation notification

Prerequisites: accepted S1 and S2 contracts. This slice does not change
validator authority, conflict decisions, receipt transport, or NAV policy.

Scope:

- add the official Python `lark-oapi` dependency and one SDK adapter for
  `drive.file.bitable_record_changed_v1`;
- exact app/table target resolution from existing holdings configuration;
- durable-before-return receiver callback and leased worker loop;
- added/edited fresh-read handoff to `event_validate_notify` reconciliation;
- self-write/current-digest no-op, retryable transport work, and ignored delete
  outcome;
- exact-purpose subscription/status CLI;
- installer-owned singleton systemd service generation, disabled by default;
- deployment/runbook documentation without activating external state.

Primary files:

- `requirements.txt`
- `src/app/holding_event_inbox_service.py` (new)
- `src/app/holdings_event_service.py` (new)
- `src/app/holdings_reconciliation_service.py`
- one narrow adapter under `src/feishu/` for the SDK long connection
- `src/app/operation_state_store.py`
- `src/config.py` only for validation of existing credentials/table reference;
  do not introduce duplicate target configuration
- `scripts/pm.py`
- `scripts/install_linux.py`
- `docs/deploy-linux.md` and focused event/installer/CLI tests

Acceptance tests:

- receiver returns only after durable inbox insert and performs no Feishu/Futu
  read, reconciliation, business write, or receipt send in the callback;
- startup completes schema/integrity work before connecting the SDK; under a
  held SQLite write lock the callback exits with an SDK-visible error in less
  than three seconds, does not falsely acknowledge, and leaves no claimed
  durable row; normal duplicate delivery verifies the stored digest before ack;
- exact event/app/table accepted; malformed target event fails visibly;
  unrelated resources are filtered without domain invocation;
- duplicate event id is a no-op; multiple actions all reach durable outcomes;
- simultaneous duplicate delivery stores one row, while the same event id with
  a different payload digest fails integrity validation;
- added/edited action fresh-reads the record and never treats event before/after
  values as field authority;
- `missing_completable` produces a pending case and confirmation receipt but no
  holdings patch; the event path cannot call apply, resolve, or recover;
- populated conflicts, manual/invalid/orphan outcomes also remain unwritten and
  create the correct blocking scope and receipts;
- all event outcomes preserve deterministic case/receipt identities; retryable
  transport/provider failure does not fabricate an apply outcome;
- processed inbox outcomes contain case/receipt keys but never an apply attempt
  id or remote holdings-mutation result;
- PM's own edited event, a new event id with the same resolved digest, an older
  revision, and a disappearing record are processed without duplicate writes or
  receipts;
- worker crash/lease expiry/restart reprocesses safely; provider/read failure is
  retryable without any holdings mutation;
- record deletion is recorded ignored and does not invent delete/recreate policy;
- `events status` is read-only; `events subscribe` requires confirm and targets
  only the configured Base; neither enables the service;
- installer produces one restartable service with no public socket or timer and
  never enables it without its explicit flag;
- existing CLI reconcile, Futu sync, receipt timer, and NAV outbox behavior are
  unchanged.

### S4 — Official NAV gate and validated snapshot consumption

Scope:

- global orphan preflight;
- post-Futu-sync account validation and dry-run projection contract;
- formal-run case materialization versus side-effect-free dry-run;
- frozen holdings handoff through read service to valuation;
- blocker and receipt/result rendering with holdings digest.

Primary files:

- `src/app/daily_nav_job_service.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/portfolio_read_service.py`
- `src/app/valuation_service.py` only if its existing holdings input needs
  contract tightening
- `src/app/nav_history_receipt_service.py`
- daily NAV and valuation tests

Acceptance tests:

- sync-before-validation conflict disappears in real flow;
- dry-run projection matches the planned Futu result and never mutates state;
- incomplete Futu projection fails closed;
- valuation receives the same holdings digest and performs no storage reread;
- valuation mutates only private copies and leaves frozen persistent-field rows
  unchanged;
- late external edits do not change the frozen valuation snapshot;
- attributed cases block only their account;
- one orphan blocks all accounts but creates one global receipt;
- source pagination, operation-state failure, and materialization failure block;
- existing eligible final NAV remains idempotently skipped;
- two accounts can run independently without sharing cases or snapshots.

## 15. Validation gates

Each slice runs focused tests and compile validation. Before aggregate review:

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_feishu_storage.py \
  tests/test_holdings_preload_minimal.py \
  tests/test_pm_cli.py \
  tests/test_operation_state_store.py \
  tests/test_holdings_event_service.py \
  tests/test_install_linux.py \
  tests/test_daily_nav_services.py \
  tests/test_portfolio_read_service.py \
  tests/test_pricing_service.py
python3.12 -X pycache_prefix=/tmp/pm_holdings_reconcile -m compileall -q \
  src skill_api.py scripts/pm.py
python3.12 -m pytest -q -p no:cacheprovider
```

New focused test files may replace the broad files above, but aggregate full
pytest and compile gates remain mandatory. No live Feishu or Futu write is a
test gate.

## 16. Review, rollout, and recovery boundaries

1. Run `planreview` against this artifact; fix and re-review until `pass` or an
   explicitly accepted `pass-with-risks`.
2. Stop after plan acceptance unless the user separately authorizes
   implementation.
3. If implementation is authorized, complete and review S1, S2, S3, and S4 in
   order, then run aggregate DeepReview and the validation gates.
4. Commit/push, Release, and upgrade remain separate user-authorized stages.
5. Before any later production apply, run read-only reconcile, report exact
   case counts by account/global scope, and obtain separate authorization.
6. Never use this feature to correct existing populated rows in bulk. Each
   conflict remains a separately confirmed case.
7. Feishu app event configuration/publication, document subscription, enabling
   the listener service, and a production canary are external mutations under a
   later release/upgrade authorization. Implementation/tests must not perform
   them live.

Rollback of a future software release restores the prior binary but does not
delete the additive workflow tables or feature-version key. Because the prior
binary cannot enforce holdings cases, a rollback runbook must stop official NAV
writers before starting that binary and keep them disabled until a compatible
version is restored. Read-only diagnostics may continue. Automating that
release/runbook behavior requires separate release authorization.

## 17. Residual risks and tracking

- Feishu has no atomic conditional-update primitive in the current client
  contract. Fresh-read, narrow patch, and readback cannot fully eliminate an
  external same-field race. Track a future ETag/revision capability separately
  if Feishu exposes one.
- Local CLI operator metadata is not authenticated. Interactive Feishu callback
  authentication or OS-level multi-user authorization is a later security work
  unit.
- Generic funds, bonds, crypto assets, and special trading-currency counters may
  remain unresolved until explicit metadata or human confirmation is supplied;
  do not expand heuristics to improve coverage.
- Listener or subscription outage delays case discovery and receipts.
  Official NAV remains protected by fresh preflight; alerting/SLO design beyond
  local service/inbox health belongs to a later operations work unit.
- Data-app type, event permissions, publication state, and Base owner/admin
  access are deployment prerequisites not provable from repository config. The
  later activation gate must verify them read-only before any subscription or
  service enable; failure stops activation rather than changing transport.
- Formula-field value changes do not produce this record event. They are not
  used as completion authority, and fresh NAV preflight remains the backstop.
- The long connection is a singleton in the current same-host topology because
  Feishu cluster delivery selects one connected client rather than broadcasting.
  Multi-host active/active ingestion is outside this work unit.
- This design adds no proactive standalone scan timer. A separate timer still
  requires its own notification-noise and operations decision.
- Record deletion behavior is not introduced by a missing-field completion work
  unit; deleted actions are recorded ignored. Any deletion-control policy needs
  a separate goal and authority decision.
- Cross-host concurrent writers are not serialized. Current deployment is
  same-host coordinated; a multi-host writer topology requires a separate
  locking/versioning design.

## 18. Completion state

- Current gate: `plan accepted; pass-with-risks`.
- Implementation authority: not granted.
- Production mutation authority: not granted.

When a later authorized implementation slice completes, its report must list:

- slice id and exact changed files;
- contracts/state transitions implemented and non-goals preserved;
- focused and aggregate validation commands with results;
- planreview/deepreview finding disposition;
- external actions explicitly not performed;
- residual risks with owner/destination;
- next Gateflow entry point and whether separate commit/push, release, or
  upgrade authorization is still required.
