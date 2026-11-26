# Runbook

This runbook is for local or Linux-instance operations. Production writes should
go through `./pm` or the local service, not ad hoc Python calls.

## NAV receipt layout

Successful accounts show NAV/assets, returns, and allocation on separate lines.
Allocation lists stocks, funds, signed net option value, and cash including pending
assets. Only the receipt groups pending assets with cash: this is not available
cash and does not change NAV, stored holdings, or valuation/API fields. Ratios use
unrounded values; independently rounded percentages can differ slightly from 100%. Missing option/pending fields in older reports mean no such
asset; explicit unknown values remain unavailable. A nonpositive/missing denominator
leaves derived ratios unavailable. Normal price summaries appear under Prices;
price problems and other warnings remain under Warnings. Failure actions and
holdings audit digests are retained.

## Preflight

```bash
./pm config inspect --json
./pm config doctor --json
./pm nav duplicates --json
python scripts/migrate_schema.py check-live
```

If full Futu holdings sync is enabled:

```bash
./pm config doctor --require-futu --json
./pm futu sync --account lx --json
```

`pm futu sync` is dry-run by default. It projects Futu `cn_cash/us_cash/hk_cash`
into the account's `CNY-CASH` holding with evidenced FX and synchronizes the
separate MMF holding, stock/ETF and long/short option positions. Run it
independently before `daily-job` so the account is reconciled even when NAV
skips an existing date.

A real write sends a success/failure receipt from the configured Feishu
“刘看山” app. Dry-runs do not send. Check `receipt.status` in JSON output;
notification failure does not replace the holdings sync result. Required
configuration: `feishu.agent.app_id`, `feishu.agent.open_id`, and the
`pm-feishu-agent-app-secret` systemd credential. This same Agent identity owns
all Base reads/writes. The Listener identity is limited to holdings/cash-flow
event ingress and is never used for receipts. Legacy `feishu.bitable.*`,
`feishu.conversation.*`, `feishu.receipt.*`, and `OM_FEISHU_BOT_*` names are
migration evidence only; production secure mode rejects plaintext Secret
fallback.
Run `systemctl start portfolio-feishu-preflight.service` before enabling jobs.

Failed Futu sync receipts preserve completed cash writeback and mark subsequent
stages that never started as `not_run` (未执行). An unknown cash mode or missing
writeback result is shown as unconfirmed. A completed MMF stage remains confirmed
if the later pending-asset stage fails. Pending-asset checks distinguish unavailable
readback, invalid holding structure and amount mismatch.

Feishu business errors include the HTTP status, `code`, `msg` and available
`log_id` in the error text, including HTTP 400 responses. Record these alongside
the sync run ID when investigating a failed request. GET HTTP 400/code `1254607`
is retried as described below; other HTTP 400 errors are not automatically
retried. The partial-write warning remains conservative because a read can fail
before or after a write; use the receipt's account-specific dry-run command to
inspect current state before another confirmed write.

### Feishu read throttling

GET requests retry HTTP 200/business code `1254290` (`TooManyRequest`) and
HTTP 400/code `1254607` (`Data not ready, please try again later`) up to three
times after the initial attempt. HTTP 429 retains its existing bounded retry
policy. Other business errors are not retried; this policy does not add
business-error retries to writes, batch mutations or messages.
The retry repeats only the current request/page, never the whole Futu sync.
This includes the fresh MMF readback after a successful update: only the read
is repeated, and the sync still fails if that read cannot be confirmed.

Backoff starts at 1, 2 and 4 seconds. A valid `Retry-After` (seconds or HTTP
date) or `x-ogw-ratelimit-reset` (seconds) can extend the wait. If both hints
exist, use the longer wait. Invalid hints are ignored. A wait exceeding 60
seconds stops automatic retry instead of shortening the server's requested
delay. Reads with a deadline refuse waits that do not fit the remaining budget
and recheck the budget before sending again. Exhaustion preserves the final
Feishu error and log ID. These retries improve recovery, not upstream capacity.

Each request attempt through `FeishuClient._request` emits an INFO log named
`feishu_request` with JSON fields: `started_at_epoch`, `elapsed_ms`, `pid`,
`client`, `method`, `endpoint`, hashed app/base/table identities, `attempt`,
`http_status`, `code`, `log_id`, `server_wait_seconds`, `retry_delay_seconds`
and `outcome`. A failed attempt also records its exception type. `retry` means
another attempt is planned; only a subsequent log proves it was sent. Authentication
failures before sending, token acquisition and retries inside the HTTP adapter
are outside this count. Logs omit raw URLs/query values, tokens, request and
response bodies, exception text, account names and holding values.

For a failed scheduled sync, inspect a bounded time window across the daily
job, API and listener, e.g. (replace times with the incident's local times):

```bash
journalctl -u portfolio-nav-daily.service -u portfolio-management-api.service \
  -u portfolio-holdings-event-listener.service \
  --since '2026-11-25 08:09:30' --until '2026-11-25 08:11:00' --no-pager
```

Match the failure's `log_id`, then group request timestamps by endpoint and
app/base/table hashes across PIDs to investigate overlapping requests. Local
60 ms pacing applies to one client instance; it does not guarantee a global
20 QPS allowance or prove which upstream quota was hit. Other applications
and devices are not covered by these PM logs. Preserve the sync run ID and
Feishu log ID for upstream investigation when aggregate evidence is insufficient.

Cash synchronization reuses the fresh base already read for the prepared cash
evidence, avoiding one duplicate account-wide read. The repository still checks
that base immediately before a mutation and proves the write by fresh readback;
cash evidence readback and final reconciliation remain intact. The diagnostic
logs can guide further read reduction or shared pacing; neither should remove
those safeguards. Do not rerun a complete confirmed sync merely because a read
was throttled: inspect the receipt and use its dry-run command first.

### Background refresh receipts

Durable background Futu refreshes enqueue a compact receipt only for successful,
trusted stock, ETF or option quantity changes, including new/zeroed positions:
the shared PM receipt shell with type “富途持仓同步”, status “✅ 成功” and a
single “持仓变化｜EXAMPLE：数量 50→51” field (synthetic example).
Cost/MMF-only and unchanged refreshes remain silent. The existing operation receipt dispatcher sends through the same
Agent destination. `(account, request_id)` is the deduplication identity; completed
requests and old stored results are never scanned for historical notifications.
The frozen outbox payload retains the sync run, source snapshot and observation
time. A later timer reports its own fresh diff, not the earlier change again.

Notification failure does not rerun holdings sync: known pre-send failures retry
through the existing receipt timer; ambiguous sends remain `unknown` for explicit
operator resolution. Live settlement retries retain the original change result.
Process death or shutdown while settlement storage is unavailable can still lose
an uncommitted change receipt after external holdings writes; restart uses fresh
state rather than inventing a historical notification. Exactly-once external
delivery is not guaranteed.

## Read-Only Checks

```bash
./pm accounts --json
./pm holdings --account lx --json
./pm cash --account lx --json
./pm nav --account lx --json
./pm positions distribution --account lx --json
./pm report daily --preview --account lx --json
```

`pm report` is preview-only. Formal daily report generation uses
`scripts/publish_daily_report.py`.

## Manual NAV Writes

Manual `pm nav record` and `pm daily` writes refuse same-day replacement by
default. Use `--overwrite` only for a deliberate operator correction:

```bash
./pm nav record --account lx --write --confirm --json
./pm daily --account lx --write --confirm --json
./pm nav record --account lx --write --confirm --overwrite --json
```

## Daily NAV Job

Dry-run:

```bash
./pm daily-job --json
./pm daily-job --accounts lx,alice --json
```

Write:

```bash
./pm daily-job --accounts lx,alice --write --confirm --json
./pm futu sync --account lx --write --confirm --json
./pm daily-job --account lx --write --confirm --json
```

Manual date:

```bash
./pm daily-job --accounts lx,alice --nav-date 2026-05-22 --write --confirm --json
```

Rules:

- If `--nav-date` is omitted, the job records the most recent CN/HK/US trading day
  before the run date.
- A NAV date is skipped only when all three Futu OpenD market calendars are
  closed. Calendar query failures block the job; timer dates are independent.
- Duplicate `nav_history` account/date records block writes.
- Pending generated fields in manual `cash_flow` rows block writes.
- An existing row is skipped only when `details.finality` is supported, explicitly final, and matches the NAV date. Legacy, manual, malformed, or date-mismatched rows block with `existing_nav_not_final`; snapshot recovery state returns `recovery_required`.
- Real writes require `--write --confirm`.

## Daily Report

```bash
# Default: NAV dry-run, but HTML artifacts are still written.
python scripts/publish_daily_report.py --account lx
python scripts/publish_daily_report.py --account lx --run-id manual-YYYYMMDD

# Explicit NAV persistence; same-day overwrite remains disabled.
python scripts/publish_daily_report.py --account lx --write-nav --confirm
```

Service controls:

```bash
python scripts/publish_daily_report.py --require-service
python scripts/publish_daily_report.py --no-service
```

The old public daily-report domain is invalid. Validate local artifacts instead:

```bash
ls reports/investment-daily-*.html
ls ../prototypes/investment-daily-*/index.html
```

Expected publish output has `public_url=null` and
`public_url_status=disabled`.

## Local Service

```bash
python scripts/service.py start
python scripts/service.py status
curl http://127.0.0.1:8765/health
```

Use `--require-service` on CLI commands when you want service outage to fail
instead of falling back:

```bash
./pm daily-job --require-service --json
```

## Compensation Recovery

List unresolved partial writes before retrying anything:

```bash
./pm compensation list --json
./pm compensation list --include-resolved --json
```

Inspect `task_id`, `status`, `supported`, `target_count`, `error_type`, and `target_outcomes`. A `state_conflict` means the current holding or NAV details match neither the recorded before state nor the intended target; investigate the live state instead of forcing an overwrite.

Retry one supported task explicitly:

```bash
./pm compensation retry --task-id repair_... --confirm --json
```

Retries use target-level compare-and-set semantics and can resume `PENDING`, `FAILED`, or orphaned `RUNNING` tasks. Do not manually replay legacy delta payloads (`supported=false`); repair those from the authoritative ledger and current holdings with a separately reviewed procedure.

## NAV History Patch Recovery

Always preview a patch before applying it:

```bash
python scripts/nav_history_repair.py patch \
  --account lx \
  --patch-file audit/nav_patch.json \
  --dry-run
```

`--validate-scope changed` validates every changed date and its first chronological successor. Apply fails before the first Feishu write if any target date is missing, duplicated, lacks a record ID, or violates validation.

```bash
python scripts/nav_history_repair.py patch \
  --account lx \
  --patch-file audit/nav_patch.json \
  --apply
```

Apply writes an append-only journal under `${PM_DATA_DIR}/nav_repair/`. Inspect the returned `status`, `applied`, `failed`, `pending`, and `journal_path`; do not treat a non-zero exit as a full rollback. For `partial`, run the exact returned `resume_command` after fixing the underlying error, or use `rollback_command` to restore recorded original target fields in reverse order. Resume rejects a changed patch plan digest, and both resume and rollback fail closed when the current live row matches neither the recorded original nor target state.

## Cash Flow Reconcile

Manual `cash_flow` rows may omit generated fields, but daily NAV writes will
block until generated fields are reconciled.

```bash
./pm cash-flow reconcile --account lx --json
./pm cash-flow reconcile --account lx --apply --confirm --json
```

## Close Account NAV Point

Use only for explicit close/clear-account states:

```python
close_nav(date_str="YYYY-MM-DD", total_value=0, dry_run=True)
close_nav(date_str="YYYY-MM-DD", total_value=0, dry_run=False, confirm=True)
```

Rules:

- `shares=0` is intentional only through this explicit action.
- `nav=1.0`.
- `details.status="CLOSED"`.
- It does not fetch prices or build valuation.

## Common Failures

### Config Doctor Fails

Check `PORTFOLIO_CONFIG_FILE`, then inspect the resolved values:

```bash
echo "$PORTFOLIO_CONFIG_FILE"
./pm config inspect --json
```

Required scheduled-job tables are holdings, nav_history, cash_flow, and
holdings_snapshot.

### Date Looks Wrong

Business dates use Beijing date semantics. `daily-job` auto-date means previous
business day before the run date, not calendar yesterday.

The installer creates a Monday-Saturday 08:10 Beijing morning timer that synchronizes lx/sy before one multi-account NAV job, plus a Monday-Friday 17:10 Futu-only timer. Saturday morning records Friday NAV; the evening timer never calls `daily-job`.

### Prices Missing

```bash
python scripts/diagnose_pricing.py --account lx --json
```

Check realtime/cache/stale/missing counts and provider errors. US quotes use
Finnhub/Sina US paths; A/H quotes use Tencent paths.

### Feishu Field Missing

```bash
python scripts/migrate_schema.py check-live
```

Compare with `docs/schema.md`. Schema changes must also update the schema
documentation and checks.

### Duplicate NAV Rows

```bash
./pm nav duplicates --json
```

Repair duplicates before running the daily job.

## Validation After Code Changes

```bash
python3 -m pytest tests -q
git diff --check
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```
