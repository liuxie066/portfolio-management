# Gateflow Implementation Plan

- Gate: `plan`
- Work unit: `repo-review-27 correctness hardening`
- Branch: `gateflow/fix-repo-review-27`
- Base: `origin/main@d04c62496bb9682a5a3ac00b38efb34ad6f3e9b7` (`v0.1.17`)
- Goal artifact: `docs/gateflow/repo-review-27/goal-confirmation.md`
- Source review: `docs/reviews/repo-review-20260719-142425.md`
- Artifact path: `docs/gateflow/repo-review-27/implementation-plan.md`
- Status: `accepted`
- Current gate: `accepted slice commit`
- Next entry point: `S4 implementation`


## Plan review decision

- Initial review: `docs/reviews/plan-review-20260719-170245.md` -> `fail` with 6 accepted findings.
- Fix: revised lock/CAS semantics, recovery-unit grouping, NAV failure discovery, deadline execution, Feishu type mapping, and Futu matching order.
- Re-review: `docs/reviews/plan-review-20260719-170710.md` -> `pass`; all 6 findings `已修复`.
- Open questions: none.
- Residual risks: all classified in this plan.

## Goal, motivation, and success signal

Fix all 27 accepted repository-review defects without changing production data or adding a new platform. The implementation must make financial writes, recovery, synchronization, valuation, Feishu transport, installer behavior, and service exposure fail safely and expose truthful state.

The work unit is complete only when:

1. every finding is mapped exactly once and aggregate DeepReview marks it `已修复`;
2. focused regressions and the complete test suite pass;
3. each implementation slice has an accepted review artifact and protected commit;
4. aggregate DeepReview and PR review pass;
5. a Draft PR is pushed and Gateflow reaches `final closeout pass`.

## Non-goals and scope boundary

- No live Feishu/Futu repair, credential edit, deployment, release, merge, PR-ready transition, reviewer request, or production mutation.
- No distributed transaction coordinator, external idempotency service, authentication platform, database migration, or new dependency.
- No automatic rewrite of historical duplicates/corruption.
- No broad API redesign outside behavior required to report replay, partial state, retry safety, or transport failures.
- No unrelated cleanup, formatting sweep, or replacement of existing repositories/services.

## First-principles judgment and direct code evidence

The findings share seven authoritative boundaries. Fixing each boundary once is smaller and safer than adapter-level guards:

- `TradeService` owns multi-step economic writes, but currently validates too late, treats repository replay as creation, does not atomically protect same-host oversell, and suppresses partial failures.
- `CompensationService` currently records best-effort tasks with read-copy-replace JSONL and has no executable recovery state machine.
- `FutuBalanceSyncService._build_position_diff()` reconciles against a broader existing-type set than its upstream eligible set.
- `DailyNavJobService` rewrites downstream `partial` to `failed`; `SnapshotService` writes the formal local audit artifact during dry-run; repair apply lacks crash-safe progress.
- `ValuationService` uses a non-cancellable daemon-thread timeout and unsafe fallback semantics; quote/FX validation is not centralized.
- `FeishuClient` accepts malformed batch responses, swallows delete failure, and has unbounded core I/O.
- CLI fallback and the module-level ASGI app can repeat ambiguous writes or bypass wrapper-only bind checks.

## Minimality / why this is not over-designed

- Reuse current models, services, repositories, CLI, JSONL audit file, `requests`, Pydantic, and stdlib `fcntl`/`time`/`zoneinfo`.
- Add one small process-lock helper rather than a lock framework.
- Keep compensation as a local append-only event log plus existing optional Feishu mirror; do not add SQLite or a workflow engine.
- Use explicit operation handlers for the four currently required target-state repairs; no plugin registry or generic saga DSL.
- Preserve existing return models and add runtime-only replay metadata plus one explicit exception instead of replacing all write APIs.
- Enforce loopback with one middleware/config flag; do not add authentication.

## Finding-to-slice mapping

| Finding | Slice | Owning correction |
|---|---|---|
| 01 | S1 | replay-aware financial unit idempotency |
| 02 | S1 | authoritative oversell/missing-holding rejection |
| 03 | S1 | service-boundary finite/positive write validation |
| 04 | S2 | explicit partial-write error and executable compensation |
| 05 | S3 | aligned Futu eligible/existing asset sets |
| 06 | S4 | preserved NAV partial state and deterministic snapshot recovery |
| 07 | S5 | reject foreign quote without valid `cny_price` |
| 08 | S5 | fixed unit price only for valid CNY cash/MMF |
| 09 | S5 | real end-to-end pricing deadline and zero-network cache-only |
| 10 | S5 | New York-local US market calendar/time |
| 11 | S5 | finite positive quote/FX validation |
| 12 | S6 | validated Feishu batch response mapping |
| 13 | S6 | delete failure propagation and cache consistency |
| 14 | S6 | configured connect/read timeouts on every Feishu request |
| 15 | S7 | no automatic write fallback after ambiguous service timeout |
| 16 | S7 | app-level loopback enforcement for direct ASGI startup |
| 17 | S3 | merge-only installer handling for three receipt env keys |
| 18 | S4 | NAV patch preflight/progress/resume/rollback-safe reporting |
| 19 | S4 | changed-date plus first-successor validation |
| 20 | S5 | quantity precision separate from money precision |
| 21 | S5 | complete validated FX cache before freshness update |
| 22 | S5 | one entry normalization for known market suffixes |
| 23 | S1 | same-host atomic repository check/create |
| 24 | S2 | locked append-only compensation event log |
| 25 | S4 | dry-run leaves formal snapshot artifact unchanged |
| 26 | S5 | foreign MMF FX conversion or rejection |
| 27 | S6 | typed, paginated strict live-schema validation |

No finding is intentionally deferred.

## Shared contracts and invariants

### Process lock contract

Add `src/process_lock.py` with a stdlib context manager backed by `fcntl.flock` and lock files under `${PM_DATA_DIR}/locks`.

- Lock names are SHA-256-derived from logical keys, never raw user strings.
- Account write lock key: `account-write:<account>`.
- Repository idempotency lock key: `<table>:<account>:<request-or-dedup-key>`.
- Compensation retry lock key: `compensation:<task_id>`.
- Lock order is fixed: account write lock -> either one compensation task lock or one repository idempotency lock -> repository/network call. Normal writes use account -> idempotency; compensation retry uses account -> task.
- No code acquires two account locks, two task locks, or two idempotency locks at once. A task never acquires an account lock after taking its task lock.
- Every product entry point that mutates account holdings/cash (TradeService, compatibility add/sub-cash, Futu sync, compensation retry) takes the account lock. Low-level repository calls remain internal and are documented as not concurrency-safe entry points.
- Contract is same-host only; cross-host duplicate prevention remains a documented residual risk because Feishu has no project-proven unique constraint.

### Runtime replay contract

`Transaction` and `CashFlow` gain a Pydantic `PrivateAttr` replay marker and a read-only property (for example `was_replayed`). It is never included in `model_dump()`, Feishu payloads, compensation payloads, JSON, or schema docs.

Repositories set the marker only when returning an existing record. On replay, `TradeService` returns immediately before holdings/cash side effects. Public adapters may include `replayed: true` while preserving existing success payload shape.

### Partial write contract

Add `PartialWriteError` in `src/app/compensation_service.py` (or a narrowly shared existing error module if review proves preferable) containing:

- `operation`, `account`, `related_record_id`;
- confirmed completed step(s);
- failed step;
- one `task_id` that identifies the complete recovery unit when recovery evidence was durably written;
- `target_count` and confirmed/failed step summaries;
- `compensation_persisted` boolean;
- original error text.

Any transaction/cash-flow ledger write followed by a failed holdings/cash side effect raises this error. It must never be wrapped as `success=true`.

### Compensation state machine

Local `${PM_DATA_DIR}/compensation_tasks.jsonl` becomes the durable same-host source of truth. The optional Feishu table remains a best-effort mirror.

Event states:

```text
PENDING -> RUNNING -> RESOLVED
                  -> FAILED -> RUNNING
RUNNING --crash--> RUNNING -> RUNNING (safe retry)
```

- Every event append uses `O_APPEND`, an exclusive process lock, flush, and `fsync`.
- Fold by `task_id`; the first event owns immutable operation/account/payload, later events own status/retry/error/resolution metadata.
- One task owns an ordered `targets[]` recovery unit. Each target contains a narrow `type`, serialized `before`, serialized `target`, and stable identity fields. This is not a generic workflow registry.
- Retry acquires account lock then task lock and is allowed for `PENDING`, `FAILED`, and orphaned `RUNNING`. For every target it performs compare-and-set: current==target is already applied; current==before may be written to target; any other current state fails the task with `error_type=state_conflict` and writes nothing for that target.
- Per-target outcomes are appended in events so a crash after one target can resume. The fixed order is holding target/delete, cash targets, then NAV snapshot/details completion.
- Legacy one-row tasks are readable as `PENDING`; unsupported legacy delta payloads are listed with `supported=false` and are never automatically applied.
- Supported target types are deliberately limited to:
  1. `HOLDING_TARGET_SET` (serialized complete Holding before/target),
  2. `HOLDING_ZERO_DELETE` (serialized holding before and target absence/zero),
  3. `CASH_TARGET_SET` (serialized before/target CASH and MMF holdings),
  4. `HOLDINGS_SNAPSHOT_TARGET_SET` (serialized before evidence, target snapshots, digest, NAV record/details completion target).

### NAV recovery contract

If NAV persistence succeeds but snapshot persistence fails:

- `NavRecordService` first persists the local compensation task containing serialized target snapshots/digest, then patches the already-written NAV details with `snapshot_status=failed`, `snapshot_persisted=false`, and the durable `task_id`; failure of the details patch remains part of the unresolved task and cannot erase the local evidence;
- the returned status remains `partial` through `AccountNavRecorderService`, `DailyNavJobService`, CLI, and service;
- a later normal daily job seeing that existing NAV checks both persisted details and unresolved local compensation tasks by `related_record_id`; either source returns `partial/recovery_required` with the task ID and retry command, instead of silently treating the date as complete or recomputing historical NAV from current holdings;
- `pm compensation retry --task-id ... --confirm` compare-and-sets the serialized target snapshot, patches NAV details complete, and only then appends `RESOLVED`; a details-patch failure remains retryable;
- normal already-complete existing NAV remains a no-write success and does not resend a receipt; recovery itself emits CLI/audit output only.

### Pricing deadline contract

Use a monotonic absolute deadline propagated from `ValuationService` through `PriceFetcher`, `PriceService`, `BatchPricePlanner`, FX, and provider HTTP calls.

- No outer daemon worker or ownerless internal worker remains on the valuation deadline path.
- Use a bounded native batch request where its HTTP call accepts remaining timeout; run remaining single-asset and FX requests sequentially. Do not exit an executor while futures are still running and do not use `shutdown(wait=False)` as cancellation.
- Every retry/backoff and HTTP timeout uses remaining deadline and stops when exhausted.
- Cache-only mode returns cache hit/miss only and never calls realtime providers, FX APIs, or cache refresh.
- If a third-party call cannot receive a timeout, it is not used on the deadline-controlled path; do not simulate cancellation with `Thread.join()` or background futures.

### Feishu batch contract

For each chunk, `batch_create_records` and `batch_update_records` validate:

- `records` exists and is a list;
- returned count equals requested count;
- each row is a dict with a non-empty `record_id` (including supported nested response shape normalized once);
- response order is used only after cardinality validation.

Malformed/partial chunks raise a structured `FeishuBatchWriteError` containing operation/table/chunk offset and confirmed results from prior complete chunks. Repositories update counts/caches only for validated complete mappings. They never report requested count as confirmed count.

## Implementation sequence and slices

### S1 — Financial write invariants and same-host idempotency

- Objective: fix findings 01, 02, 03, 23.
- Prerequisite: accepted plan.
- Exact production ownership:
  - `src/process_lock.py` (new)
  - `src/models.py`
  - `src/write_guard.py`
  - `src/app/trade_service.py`
  - `src/feishu/repositories/transactions_repository.py`
  - `src/feishu/repositories/cash_flow_repository.py`
  - `src/feishu/repositories/holdings_repository.py`
  - `skill_api.py`
- Exact test ownership:
  - `tests/test_trade_service.py`
  - `tests/test_write_guard.py`
  - `tests/test_feishu_storage.py`
  - `tests/test_portfolio.py`
  - one narrow new process-lock/idempotency test file if existing files cannot express multi-process behavior cleanly.
- Allowed changes:
  1. validate finite positive quantity/price/amount and non-negative finite fee at the first `TradeService` line before name lookup, ledger creation, or cash checks;
  2. construct transaction content dedup key before generating a random fallback request ID;
  3. guard repository check/create with per-key process locks and recheck inside the lock;
  4. set runtime replay marker on existing records;
  5. take one account write lock around validation, available-position/cash snapshot, ledger write, and immediate side effects;
  6. reject SELL when exact account/broker holding is missing, zero, or insufficient before writing the transaction;
  7. return immediately on transaction/cash-flow replay;
  8. route compatibility `PortfolioSkill.add_cash/sub_cash` through finite/positive validation plus the same account lock so compensation CAS cannot race those public writes;
  9. make `update_holding_quantity` reject missing holdings and negative target quantities rather than silently returning.
- Explicit non-goals: no compensation retry engine; existing secondary failure path remains until S2 but must not regress.
- Required assertions:
  - serial and multi-process duplicate request/dedup writes create one ledger row and one economic side effect;
  - default no-request-ID identical content replays, while explicit distinct request IDs permit identical split trades;
  - oversell, missing holding, broker mismatch, zero/negative/NaN/Infinity inputs cause zero writes;
  - replay marker is absent from JSON/model dump and Feishu payload.
- Validation:
  - `python3 -m pytest tests/test_trade_service.py tests/test_write_guard.py tests/test_feishu_storage.py tests/test_portfolio.py -q -p no:cacheprovider`
  - `python3 -m compileall -q src skill_api.py`
- Completion signal: all S1 focused tests pass and DeepReview finds no accepted S1 issue.
- Residual risk classification: cross-host duplicate creation is `assigned to later infrastructure decision`, documented, non-blocking for same-host service contract.
- Commit: `gateflow: accept repo-review-27 S1-financial-invariants`

### S2 — Truthful partial writes and crash-safe compensation

- Objective: fix findings 04 and 24 and complete recovery paths introduced by S1.
- Prerequisite: S1 accepted commit.
- Exact production ownership:
  - `src/app/compensation_service.py`
  - `src/app/trade_service.py`
  - `src/app/cash_service.py`
  - `src/app/nav_record_service.py`
  - `src/portfolio.py`
  - `src/feishu_storage.py`
  - `src/feishu/repositories/nav_history_repository.py`
  - `scripts/pm.py`
  - `docs/schema.md`
  - `docs/operations.md` if it is the existing operator runbook; otherwise a narrow compensation section in the closest existing operations doc.
- Exact test ownership:
  - `tests/test_compensation_service.py`
  - `tests/test_trade_service.py`
  - `tests/test_nav_record_service.py`
  - `tests/test_pm_cli.py`
  - `tests/test_audit_fixes.py`
- Allowed changes:
  1. make `PortfolioManager._record_compensation()` return durable evidence or raise;
  2. append/fold compensation events under a process lock and mirror to Feishu only after local durability;
  3. before ledger write, capture the complete ordered `targets[]` recovery unit with before/target snapshots for holding/delete and all CASH/MMF rows under the S1 account lock;
  4. after ledger creation, apply targets in fixed order and stop starting new side effects at first failure; persist one task containing every target not yet proven converged;
  5. replace delta-only compensation payloads with the four approved target types and compare-and-set semantics;
  6. raise `PartialWriteError` after any secondary failure, including compensation-persistence failure;
  7. add `pm compensation list [--json]` and `pm compensation retry --task-id ID --confirm [--json]`;
  8. retry under account -> task lock, append `RUNNING`, compare/apply each target, append `RESOLVED` or `FAILED`, and expose per-target confirmed/conflict state;
  9. include serialized target NAV snapshots, digest, related NAV record, and details before/target in NAV snapshot compensation.
- Crash cases required:
  - crash after target side effect but before `RESOLVED` -> current equals target, rerun converges and resolves;
  - later legitimate account write changes current away from both before and target -> retry reports state conflict and does not overwrite;
  - crash/exception before any side effect -> `FAILED`, rerun succeeds;
  - two concurrent recorders retain both task IDs;
  - two concurrent retries do not apply incompatible transitions;
  - local task durability failure produces `PartialWriteError(compensation_persisted=false)`.
- Validation:
  - `python3 -m pytest tests/test_compensation_service.py tests/test_trade_service.py tests/test_nav_record_service.py tests/test_pm_cli.py tests/test_audit_fixes.py -q -p no:cacheprovider`
  - `python3 -m compileall -q src scripts/pm.py`
- Completion signal: partial writes cannot return success and every supported task can be listed/retried idempotently.
- Residual risk classification: legacy delta tasks are `requiring explicit operator decision`; listed but not mutated.
- Commit: `gateflow: accept repo-review-27 S2-compensation-recovery`

### S3 — Futu scope consistency and installer env preservation

- Objective: fix findings 05 and 17.
- Prerequisite: S2 accepted commit.
- Exact production ownership:
  - `src/app/futu_balance_sync_service.py`
  - `scripts/install_linux.py`
- Exact test ownership:
  - `tests/test_futu_balance_sync_service.py`
  - `tests/test_install_linux.py`
- Allowed changes:
  1. define one stock-sync asset-type set aligned with accepted upstream Futu security types (stock plus ETF mapping only);
  2. first build eligible incoming positions by normalized code; for each incoming code, join against every same-account/broker existing row so a legacy `*_FUND` ETF row can be updated and preserve metadata;
  3. after recording matched rows, build absent zero candidates only from unmatched canonical stock/`EXCHANGE_FUND` types. Unmatched `CN_FUND`, `HK_FUND`, and `US_FUND` rows are untouched; test legacy ETF update, unrelated filtered fund untouched, and absent canonical stock zeroed;
  4. acquire the same account write lock around Futu diff read and all position/cash/MMF writes so compensation compare-and-set cannot race broker synchronization;
  5. parse existing target env, preserve all non-managed lines and existing managed values, and replace only explicit valid source values for the three `OM_FEISHU_BOT_*` keys;
  6. missing source file or missing source key preserves the corresponding target value; duplicate keys remain a hard error before writes.
- Validation:
  - `python3 -m pytest tests/test_futu_balance_sync_service.py tests/test_install_linux.py -q -p no:cacheprovider`
  - installer digest test proves unrelated env content is unchanged.
- Completion signal: no filtered Futu fund is zeroed and rerunning installer cannot erase deployed receipt credentials.
- Residual risks: none uncovered; Futu unsupported security types remain intentionally ignored and visible in source snapshot diagnostics.
- Commit: `gateflow: accept repo-review-27 S3-futu-installer`

### S4 — NAV partial state, dry-run purity, and repair resumability

- Objective: fix findings 06, 18, 19, 25.
- Prerequisite: S2 compensation target/retry contract.
- Exact production ownership:
  - `src/app/daily_nav_job_service.py`
  - `src/app/account_nav_recorder_service.py`
  - `src/app/nav_record_service.py`
  - `src/app/snapshot_service.py`
  - `src/maintenance/nav_history_repair/patch.py`
  - `scripts/nav_history_repair.py`
  - `docs/operations.md` / nearest existing NAV repair runbook
- Exact test ownership:
  - `tests/test_daily_nav_services.py`
  - `tests/test_nav_record_service.py`
  - `tests/test_snapshot_service.py`
  - new focused `tests/test_nav_history_patch.py` if no current patch tests exist.
- Allowed changes:
  1. preserve downstream `partial` and `recovery_required` instead of rewriting to `failed`;
  2. existing NAV with failed snapshot returns deterministic recovery metadata and never recalculates historical NAV from current holdings; detection reads persisted NAV details and unresolved local compensation by related record ID;
  3. dry-run skips `_write_local_snapshot()` entirely; formal local file digest/mtime remain unchanged;
  4. patch preflight resolves every target date to exactly one record before first write;
  5. validation set is every changed date plus its first chronological successor (if any), deduplicated;
  6. apply writes an append-only progress journal under `PM_DATA_DIR/nav_repair`, recording plan digest, per-row pending/applied/failed and original/target fields;
  7. resume accepts only the same plan digest and skips verified applied rows; rollback restores recorded originals in reverse applied order;
  8. result reports `status=completed|partial|failed`, applied/failed/pending rows, journal path, and resume/rollback command; never silent success.
- State machine:

```text
PLANNED -> APPLYING -> COMPLETED
                   -> PARTIAL -> APPLYING (resume)
                   -> ROLLING_BACK -> ROLLED_BACK | ROLLBACK_PARTIAL
```

- Validation:
  - `python3 -m pytest tests/test_daily_nav_services.py tests/test_nav_record_service.py tests/test_snapshot_service.py tests/test_nav_history_patch.py -q -p no:cacheprovider`
  - fault injection at row N proves truthful partial report, deterministic resume, and rollback of confirmed prefix.
- Completion signal: partial NAV and patch states remain explicit/recoverable; dry-run has no formal audit write.
- Residual risk classification: external Feishu response ambiguity is `covered by S6 transport contracts`.
- Commit: `gateflow: accept repo-review-27 S4-nav-recovery`

### S5 — Pricing, valuation, FX, market time, and precision

- Objective: fix findings 07, 08, 09, 10, 11, 20, 21, 22, 26.
- Prerequisite: S4 accepted commit.
- Exact production ownership:
  - `src/app/valuation_service.py`
  - `src/price_fetcher.py`
  - `src/pricing/service.py`
  - `src/pricing/batch.py`
  - `src/pricing/payload.py`
  - `src/pricing/fixed.py`
  - `src/pricing/fx.py`
  - provider modules under `src/pricing/providers/` only where timeout propagation is required
  - `src/pricing/classifier.py`
  - `src/market_time.py`
  - `src/snapshot_models.py`
- Exact test ownership:
  - `tests/test_decimal_valuation.py`
  - `tests/test_price_boundary_decimal.py`
  - `tests/test_price_fetcher.py`
  - `tests/test_price_fetcher_branch_normalization.py`
  - `tests/test_price_fetcher_single_fetch_cache_only.py`
  - `tests/test_pricing_providers.py`
  - `tests/test_pricing_classifier.py`
  - market-time, FX, and snapshot tests already present; add narrow files only if absent.
- Allowed changes:
  1. normalize known suffixes `.HK/.SH/.SZ/.US` once at pricing entry, preserving internal-dot symbols such as `BRK.B`;
  2. central quote validator rejects non-finite/non-positive `price`, `cny_price`, and exchange rate; foreign currency requires valid `cny_price`;
  3. valuation unit-price fallback is only for CNY `CASH`/`MMF`; other missing quotes produce blocking warning/no fabricated value;
  4. CNY MMF remains 1; USD/HKD MMF uses validated FX via the same fixed-cash path or returns a typed failure;
  5. validate FX file shape requires finite positive `USDCNY` and `HKDCNY` before setting memory freshness; invalid/incomplete cache is ignored;
  6. remove the outer daemon-thread timeout; on the deadline-controlled valuation path use only bounded native batch HTTP plus sequential remaining single/FX calls, propagating a monotonic absolute deadline through HTTP and retry sleeps; no background executor may outlive return;
  7. cache-only planner path is selected before fixed-price branches that may fetch FX and before realtime/batch providers;
  8. US open check converts the supplied instant to `America/New_York` and tests weekday/time there;
  9. add quantity quantization constant (existing holding quantity precision) and stop applying `MONEY_QUANT` to snapshot quantity.
- Deadline assertions:
  - no provider/network mock called in cache-only miss/hit;
  - elapsed time stays within deadline plus small scheduler tolerance;
  - retries/backoff stop when no remaining time;
  - timed-out work does not continue in a background thread.
- Validation:
  - `python3 -m pytest tests/test_decimal_valuation.py tests/test_price_boundary_decimal.py tests/test_price_fetcher.py tests/test_price_fetcher_branch_normalization.py tests/test_price_fetcher_single_fetch_cache_only.py tests/test_pricing_providers.py tests/test_pricing_classifier.py tests/test_snapshot_service.py -q -p no:cacheprovider`
  - `python3 -m compileall -q src`
- Completion signal: valuation never fabricates convertibility/value, cache-only is network-free, and the deadline is end-to-end.
- Residual risk classification: third-party provider latency beyond configured request timeout is `fixed in current slice`; market holidays remain existing documented limitation, not introduced by this work unit.
- Commit: `gateflow: accept repo-review-27 S5-pricing-correctness`

### S6 — Feishu transport, response mapping, delete semantics, and typed schema

- Objective: fix findings 12, 13, 14, 27.
- Prerequisite: S5 accepted commit.
- Exact production ownership:
  - `src/config.py`
  - `config.example.yaml`
  - `src/feishu_client.py`
  - `src/feishu/repositories/holdings_repository.py`
  - `src/feishu/repositories/cash_flow_repository.py`
  - `src/feishu/repositories/nav_history_repository.py`
  - `src/feishu/repositories/snapshots_repository.py`
  - `scripts/migrate_schema.py`
  - `docs/schema.md`
  - `docs/migrations.md`
- Exact test ownership:
  - `tests/test_feishu_client.py`
  - `tests/test_feishu_efficiency.py`
  - `tests/test_feishu_storage.py`
  - `tests/test_holdings_bulk_upsert_minimal.py`
  - `tests/test_nav_bulk_upsert_minimal.py`
  - schema-check tests (new narrow file if absent).
- Allowed changes:
  1. add configured Feishu connect/read timeout pair with conservative defaults and pass it to tenant-token and every session request unless a smaller caller deadline is supplied;
  2. delete configuration errors and HTTP/API failures raise; repository caches invalidate only after confirmed delete;
  3. normalize and validate each batch chunk before repository count/cache mutation; expose structured confirmed prior chunks on failure;
  4. repositories derive created/updated counts and record IDs from validated responses only;
  5. parse documented field name plus normalized accepted type family (`text`, `select`, `number`, `date`, `datetime`, `json-text` where documentation permits alternatives);
  6. paginate live field listing until `has_more/page_token` is exhausted;
  7. compare required field types in `--strict`; optional/extra fields are reported but do not block unless documented incompatible;
  8. use the official Feishu field schema mapping documented for the fields API: type `1=text`, `2=number`, `3=single select`, `4=multi select`, `5=date`; use `ui_type` to distinguish DateTime presentation where required. Parse documented alternatives literally: `text/select` accepts 1/3/4, `date/text` accepts 5/1, `text/datetime` accepts 1/5, JSON text accepts 1. Record the official-source note in `docs/migrations.md`; do not infer unlisted IDs.
- Validation:
  - malformed empty/short/long/missing-record-id responses fail and leave caches/counts truthful;
  - delete timeout/API failure retains cache;
  - token and data requests both receive timeout tuple;
  - multipage typed schema mismatch fails strict mode.
  - command: `python3 -m pytest tests/test_feishu_client.py tests/test_feishu_efficiency.py tests/test_feishu_storage.py tests/test_holdings_bulk_upsert_minimal.py tests/test_nav_bulk_upsert_minimal.py tests/test_schema_check.py -q -p no:cacheprovider`
- Completion signal: Feishu protocol ambiguity cannot be reported as full success and strict schema proves names, types, and pagination.
- Residual risk classification: undocumented Feishu protocol evolution is `covered by strict failure and later deployment canary`.
- Commit: `gateflow: accept repo-review-27 S6-feishu-boundary`

### S7 — Service timeout replay and ASGI loopback safety

- Objective: fix findings 15 and 16.
- Prerequisite: S6 accepted commit.
- Exact production ownership:
  - `scripts/pm.py`
  - `src/service/client.py`
  - `src/service/http.py`
  - `src/service/bind.py`
  - `scripts/service.py`
  - `scripts/serve.py`
  - `scripts/install_linux.py` only if environment wiring is required
  - service/runbook docs nearest existing service documentation.
- Exact test ownership:
  - `tests/test_pm_cli.py`
  - `tests/test_service_client.py`
  - `tests/test_service_http.py`
  - `tests/test_service_cli.py`
  - `tests/test_install_linux.py` if unit environment changes.
- Allowed changes:
  1. `_service_or_fallback(..., allow_fallback=...)`; read-only commands pass true, write-capable commands pass false;
  2. service timeout for writes raises an outcome-unknown error instructing operator not to blindly retry; explicit `--no-service` remains the direct operator path;
  3. connection-refused before request dispatch may be classified separately only if the client can prove no request was sent; otherwise fail closed;
  4. `create_app(service=None, allow_remote=None)` resolves remote permission from an explicit environment value and defaults false;
  5. middleware rejects non-loopback `request.client.host` when remote is not allowed; it does not trust `Host` or forwarding headers;
  6. wrapper `--allow-remote` sets the explicit environment flag before uvicorn import/start; direct `uvicorn src.service.http:app --host 0.0.0.0` remains fail-closed;
  7. loopback includes IPv4/IPv6 loopback only.
- Validation:
  - read timeout may fall back for reads;
  - write timeout never invokes direct backend;
  - `--no-service` invokes direct backend exactly once;
  - TestClient/non-loopback middleware cases and direct module app default fail closed;
  - spoofed Host/X-Forwarded-For cannot bypass actual-client check.
  - command: `python3 -m pytest tests/test_pm_cli.py tests/test_service_client.py tests/test_service_http.py tests/test_service_cli.py tests/test_install_linux.py -q -p no:cacheprovider`
- Completion signal: ambiguous write requests are never automatically replayed and app-level loopback safety survives wrapper bypass.
- Residual risk classification: deliberate `--allow-remote` remains unauthenticated and is `documented explicit operator risk`, not silently enabled.
- Commit: `gateflow: accept repo-review-27 S7-service-safety`

## Cross-slice review requirements

Each slice follows:

```text
implementation artifact
-> focused diagnostics/tests
-> DeepReview current slice against previous accepted commit
-> fix artifact when needed
-> DeepReview re-review
-> accepted slice commit
```

DeepReview must trace real entry points and include adversarial failure, project-instruction, overcoupling, and semantic-ownership-drift passes.

## Aggregate validation and review

After S7:

1. `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q -p no:cacheprovider`
2. `python3 -m compileall -q src scripts skill_api.py`
3. `git diff --check origin/main...HEAD`
4. aggregate `$deepreview --base origin/main` with all 27 source findings re-mapped to final status;
5. fix/re-review until no accepted finding remains;
6. protected commit `gateflow: accept deepreview for repo-review-27`;
7. verify branch status and intended commit list;
8. push branch, create Draft PR, DeepReview the PR, fix/re-review, accept PR review commit if needed, final push;
9. write final closeout artifact.

## Documentation decision

Documentation changes are required and limited to behavior that operators or schema maintainers must know:

- same-host idempotency/cross-host residual risk;
- partial-write error and compensation list/retry/resume semantics;
- NAV recovery and repair journal/resume/rollback commands;
- installer merge-only treatment of three receipt keys;
- Feishu timeout/batch/delete/typed-schema failure behavior;
- service ambiguous-timeout and explicit remote-bind risk;
- pricing cache-only/deadline and foreign MMF behavior where current docs state otherwise.

No changelog/version bump or release documentation is part of this work unit.

## Historical compatibility

- Existing transaction/cash-flow rows remain readable; runtime replay metadata is not persisted.
- Existing identical transactions without request IDs may now replay by content as the model documentation already claims; callers requiring identical split trades must provide distinct request IDs.
- Existing compensation one-row JSONL records are readable; delta-only tasks are not auto-applied.
- Existing NAV rows without snapshot failure metadata keep current complete-existing behavior. Only explicit `snapshot_persisted=false` / failed task evidence enters recovery-required state.
- Existing installer env lines are preserved byte-semantically except normalized managed-key replacement where an explicit source value exists.
- Existing schema docs gain typed parsing without changing live tables.

## Risks and open questions

### Closed by plan

- Lock ordering: fixed as account -> task/idempotency and tested; no reverse or multi-account nesting.
- Replay marker leakage: prevented with `PrivateAttr` and serialization tests.
- Crash/stale compensation: before/target compare-and-set under account -> task lock plus append-only per-target event state.
- NAV receipt duplication/orphan marker: normal existing-complete skip remains; recovery checks row details plus unresolved local task and does not run normal receipt path.
- Pricing timeout: deadline path uses bounded native batch plus sequential remaining calls; no worker outlives return.
- Feishu partial mapping: structured failure with only prior complete chunks confirmed.
- ASGI wrapper bypass: middleware uses actual client address and defaults fail-closed.

### Closed by plan review evidence

- Feishu field type IDs are fixed from the official fields API schema: 1 text, 2 number, 3 single select, 4 multi select, 5 date; documented slash-separated alternatives define accepted families.

## Per-slice residual-risk classification

| Slice | Residual risk | Classification |
|---|---|---|
| S1 | Cross-host Feishu duplicate race | assigned to later infrastructure decision; documented |
| S2 | Legacy delta compensation tasks | requiring explicit operator decision |
| S3 | Unsupported Futu security types | intentionally ignored and observable |
| S4 | Feishu ambiguous row write | covered by S6 |
| S5 | Market holiday calendar completeness | pre-existing limitation, documented/unmodified |
| S6 | Future Feishu protocol/type changes | strict failure plus later deployment canary |
| S7 | Explicit unauthenticated remote mode | documented operator-controlled risk |

No residual risk is unclassified.

## Completion report format

Final closeout will report:

- changed contracts and files by slice;
- exact 27/27 finding status;
- focused/full test and compile evidence;
- review artifact paths and accepted commit hashes;
- docs changes;
- residual risks/owners;
- Draft PR URL and PR review status;
- next entry point: user-authorized merge/release/deployment only.
