# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `nav-finality-write-authority`
- Branch: `fix/nav-finality-write-authority`
- Base: `origin/main@81e82e2`
- Artifact path: `docs/gateflow/nav-finality-write-authority/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `确认创建` after the proposed goal/scope and protected-branch prompt
- Confirmed at: `2026-07-21 10:58:39 +0800`

## Why this work unit exists

The 2026-07-21 morning NAV job reported three accounts as `skipped_existing_nav` because rows for 2026-07-20 had been written prematurely by a repair operation. `DailyNavJobService._existing_nav_item()` treats row presence as sufficient finality. The stored row has no durable lifecycle metadata proving that it came from the canonical daily finalization path. Normal NAV mutations are also not serialized at the repository boundary, and public write surfaces use inconsistent overwrite/default-write semantics.

## Target outcome

Fix the four confirmed NAV defects with the smallest coherent change:

1. distinguish row existence from final daily NAV eligibility;
2. persist a versioned NAV finality/write-provenance contract without a Feishu schema migration;
3. serialize every authoritative `nav_history` mutation through one same-host lock;
4. make all public write surfaces fail closed and remove implicit NAV persistence from report publishing defaults.

## Success signals

1. `daily-job` skips only a row explicitly finalized by a trusted canonical or validated maintenance path for the target NAV date.
2. Existing legacy, manual, repair, or otherwise unfinalized rows return an explicit blocking status instead of `skipped_existing_nav`.
3. New application NAV rows carry versioned finality/provenance details including status, target NAV date, valuation timestamp when available, writer, write reason, and run ID.
4. Finality metadata is honest: this work unit does not fabricate a provider-observed quote trading date that the current valuation model cannot supply.
5. All Feishu `nav_history` create/update/patch/delete operations are serialized through one repository-owned same-host process lock, including the single-write existence check plus mutation.
6. `pm nav record`, `pm daily`, HTTP/service/compatibility defaults, and raw storage/application defaults do not overwrite existing rows unless explicitly requested.
7. `scripts/publish_daily_report.py` is read-only for NAV by default; any retained NAV write mode requires explicit write and confirmation flags.
8. Existing repair rollback/backfill and CLOSED-row behavior remain available and are classified as maintenance writes rather than canonical daily finalization.
9. Focused tests, full tests, compile checks, and review gates pass; a Draft PR is created.

## Scope boundary

### In scope

- Finality/provenance contract stored inside `NAVHistory.details`.
- Daily-job existing-row decision and result/receipt statuses.
- Application parameter propagation necessary to label canonical, manual, initialization, closed, report, and maintenance writes.
- Repository mutation serialization and elimination of the single-write check/write race.
- Safer overwrite and publisher defaults across CLI, HTTP, service client/application, compatibility API, and storage facades.
- Tests and operator-facing documentation for changed contracts.

### Out of scope

- Finnhub/Yahoo/Futu provider changes, batch price caching, or stale-price fallback.
- Claiming a provider-observed `quote_trading_date` before pricing payloads expose one.
- Distributed/cross-host locking or a new database unique constraint.
- Rewriting existing production rows or automatically promoting legacy rows to final.
- Deploying, releasing, merging, or mutating production Feishu data.

## Direct code evidence

- `src/app/daily_nav_job_service.py::_existing_nav_item` returns `skipped_existing_nav` for any existing row unless snapshot recovery is pending.
- `src/app/nav_record_service.py` only adds `run_id`; it has no finality/write-provenance contract.
- `src/feishu/repositories/nav_history_repository.py::_write_one_nav_record` performs a preview/existence lookup and the actual write as separate operations without a shared mutation lock.
- `src/feishu/repositories/nav_history_repository.py` is the only production owner of direct Feishu `nav_history` create/update/delete calls.
- `scripts/publish_daily_report.py` defines `--dry-run` as opt-in and internally sets `confirm=not dry_run`, making direct invocation a real NAV write.
- `overwrite_existing=True` is the default in manual CLI/service/compatibility/application/storage layers, while `daily-job` defaults to false.

## Minimality decision

- Store finality under `details["finality"]`; do not add Feishu columns or migrations.
- Use one repository-wide NAV mutation lock because NAV write volume is low and record-id patch methods cannot derive account/date without extra reads.
- Do not create a generic workflow engine, distributed lock service, or separate persistence entity.

## Blocking open questions

- None. Provider-observed quote-date proof is explicitly deferred to the separate pricing reliability work unit rather than fabricated here.

## Residual risks

- Cross-host writers are not serialized: `assigned to later infrastructure decision`.
- Existing legacy rows remain unverified and require explicit maintenance handling: `fixed behavior in current work unit; data remediation remains operator-owned`.
- Provider quote-date proof remains absent: `assigned to later pricing reliability work unit`.

## Completion state

- Current gate: `goal confirmation pass`.
- Next gate: `plan`.
