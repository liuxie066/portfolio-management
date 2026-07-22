# Runbook

This runbook is for local or Linux-instance operations. Production writes should
go through `./pm` or the local service, not ad hoc Python calls.

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

`pm futu sync` is dry-run by default. It synchronizes cash/MMF plus LONG
STOCK/ETF quantity and Futu `average_cost`; `diluted_cost` is never used. Run
it independently before `daily-job` so holdings refresh even when NAV skips an
existing date.

A real write sends a success/failure receipt from the configured Feishu
“刘看山” app. Dry-runs do not send. Check `receipt.status` in JSON output;
notification failure does not replace the holdings sync result. Required
configuration: `feishu.receipt.app_id`, `feishu.receipt.app_secret`, and
`feishu.receipt.open_id`. If these are absent, the resolver accepts the
existing options-monitor variables `OM_FEISHU_BOT_APP_ID`,
`OM_FEISHU_BOT_APP_SECRET`, and `OM_FEISHU_BOT_USER_OPEN_ID`.

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

- If `--nav-date` is omitted, the job records the most recent business day
  before the run date.
- Weekends and `calendar.holidays` are skipped as NAV dates, not as timer run
  dates.
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
