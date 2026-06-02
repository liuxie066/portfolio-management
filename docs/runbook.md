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

If Futu cash/MMF sync is enabled:

```bash
./pm config doctor --require-futu --json
```

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

## Daily NAV Job

Dry-run:

```bash
./pm daily-job --json
./pm daily-job --accounts lx,alice --json
```

Write:

```bash
./pm daily-job --accounts lx,alice --write --confirm --json
./pm daily-job --account lx --sync-futu-cash-mmf --write --confirm --json
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
- Existing same-day NAV is skipped unless `--overwrite` is explicit.
- Real writes require `--write --confirm`.

## Daily Report

```bash
python scripts/publish_daily_report.py --account lx
python scripts/publish_daily_report.py --account lx --run-id manual-YYYYMMDD
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

The systemd timer should run every calendar day, for example
`*-*-* 08:10:00 Asia/Shanghai`. A Saturday timer run records Friday NAV; do not
limit the timer to Monday-Friday.

### Prices Missing

```bash
python scripts/diagnose_pricing.py --account lx --json
```

Check realtime/cache/stale/missing counts and provider errors. US quotes use
Finnhub/Yahoo Chart paths; A/H quotes use Tencent paths.

### Feishu Field Missing

```bash
python scripts/migrate_schema.py check-live
```

Compare with `docs/schema.md`. Schema changes must also update migration
registry files.

### Duplicate NAV Rows

```bash
./pm nav duplicates --json
```

Repair duplicates before running the daily job.

## Validation After Code Changes

```bash
python3 -m pytest tests -q
python3 tests/run_tests.py
git diff --check
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```
