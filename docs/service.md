# Service API

The local HTTP service is the primary programmatic boundary. It delegates all
product behavior to `src/service/application.py`.

`skill_api.py` is not a service dependency; it is a compatibility adapter for
older Python/Skill callers.

## Run

```bash
pm config doctor --json
python scripts/service.py start
python scripts/service.py status
python scripts/service.py stop
curl http://127.0.0.1:8765/health
```

Default URL: `http://127.0.0.1:8765`.

The service is unauthenticated and binds to loopback hosts only by default.
Binding to `0.0.0.0` or any other non-loopback address requires
`--allow-remote` and an authenticated outer network boundary.

Config keys:

- `service.host` / `PORTFOLIO_SERVICE_HOST`
- `service.port` / `PORTFOLIO_SERVICE_PORT`
- `service.url` / `PORTFOLIO_SERVICE_URL`

## Read Endpoints

- `GET /health`
- `GET /accounts?include_default=true`
- `GET /accounts/overview?accounts=alice,bob&price_timeout=30`
- `GET /holdings?account=alice&include_cash=true&include_price=false`
- `GET /cash?account=alice`
- `GET /nav?account=alice&days=30`
- `GET /distribution?account=alice`
- `GET /report/full?account=alice&price_timeout=30`
- `GET /report/{daily|monthly|yearly}?account=alice&price_timeout=30`

Legacy `/accounts/{account}/...` routes remain available for compatibility.
New clients should pass account as a query parameter.

## Write Endpoints

- `POST /futu/holdings/sync`
- `POST /accounts/{account}/futu/holdings/sync`
- `POST /nav/record`
- `POST /accounts/{account}/nav/record`
- `POST /report/daily-bundle`
- `POST /accounts/{account}/report/daily-bundle`
- `POST /daily-nav-job`
- `POST /accounts/{account}/daily-nav-job`

Writes are dry-run by default. A real write requires `dry_run=false` and
`confirm=true`.

## Futu Holdings Sync

Request:

```json
{
  "account": "lx",
  "dry_run": true,
  "confirm": false,
  "allow_empty_stock_snapshot": false
}
```

This independently synchronizes CNY cash/MMF plus Futu LONG STOCK/ETF quantity
and `average_cost`. It never uses `diluted_cost` or deprecated `cost_price`.
Real writes require `dry_run=false` and `confirm=true`. An empty eligible stock
snapshot is blocked while existing Futu stocks are non-zero; the override also
requires `confirm=true`.

For real writes, the application service sends a Feishu receipt through the
configured “刘看山” app and adds a `receipt` object to the response. Dry-runs
return `receipt.status=skipped`. Delivery failure is reported as
`receipt.status=failed` without changing the holdings sync `success` value.
Configure `feishu.receipt.app_id`, `feishu.receipt.app_secret`, and
`feishu.receipt.open_id`, or inject options-monitor's existing
`OM_FEISHU_BOT_APP_ID`, `OM_FEISHU_BOT_APP_SECRET`, and
`OM_FEISHU_BOT_USER_OPEN_ID` variables.

Run this endpoint or `pm futu sync` before `daily-nav-job`. Keeping the commands independent ensures holdings still refresh when NAV recording skips an existing date. Production ordering is owned by `scripts/portfolio_scheduled_job.sh`, not by `DailyNavJobService`.

## NAV Record

Request:

```json
{
  "account": "alice",
  "nav_date": "2026-05-22",
  "price_timeout": 30,
  "dry_run": true,
  "confirm": false,
  "overwrite_existing": true,
  "use_bulk_persist": false,
  "run_id": "optional-operator-run-id"
}
```

Use this for a single account NAV write. For scheduled production work prefer `daily-nav-job`. A real multi-account job adds one best-effort Feishu `receipt` object to the response; dry-run returns `receipt.status=skipped`.

## Daily NAV Job

Request:

```json
{
  "accounts": ["alice", "bob"],
  "nav_date": "auto",
  "run_date": "2026-05-25",
  "price_timeout": 30,
  "dry_run": true,
  "confirm": false,
  "overwrite_existing": false,
  "sync_futu_cash_mmf": false,
  "sync_futu_dry_run": null,
  "force_non_business_day": false,
  "run_id": "optional-operator-run-id"
}
```

Behavior:

1. Resolve `nav_date`. `auto` means the most recent business day before
   `run_date`.
2. Skip NAV dates that are weekends or configured `calendar.holidays` unless
   `force_non_business_day=true`.
3. Resolve accounts from the request or current holdings.
4. Block duplicate `nav_history` account/date records.
5. Block pending generated-field reconciliation in manual `cash_flow` rows.
6. Build one priced snapshot per account and write NAV. The embedded Futu cash/MMF fields remain a legacy compatibility path; full holdings sync is a separate endpoint.

Top-level response includes:

- `success`
- `status`
- `date`
- `run_id`
- `calendar`
- `accounts`
- `items`
- `summary`
- `receipt` (consolidated Feishu receipt for real jobs; delivery failure does not change top-level `success`)

## Daily Report Bundle

Request:

```json
{
  "account": "alice",
  "nav_date": "2026-05-22",
  "price_timeout": 30,
  "dry_run": true,
  "confirm": false,
  "overwrite_existing": true,
  "sync_futu_cash_mmf": false,
  "sync_futu_dry_run": null,
  "use_bulk_persist": false,
  "run_id": "optional-operator-run-id"
}
```

The bundle is one service call instead of a client-side chain of
`/nav/record`, `/report/daily`, and `/nav`. It must reuse one priced snapshot
for NAV recording, report payload, and recent NAV output.

Internal boundaries:

- `FutuBalanceSyncService`: independent Futu cash/MMF and stock/ETF quantity + average-cost sync.
- `AccountNavRecorderService`: priced snapshot and NAV write; embedded cash/MMF sync is compatibility-only.
- `DailyReportPayloadService`: report/distribution/recent-NAV payload from the
  existing snapshot and NAV fact.
- `DailyAccountNavService`: response-shape orchestrator for the two services.

`daily_report_bundle` is also dry-run by default and requires `dry_run=false`
plus `confirm=true` for real NAV writes.

## Service Facade Map

- `PortfolioService.list_accounts()` -> `AccountService.list_accounts()`
- `PortfolioService.multi_account_overview()` -> `AccountService.multi_account_overview()`
- `PortfolioService.record_nav()` -> `AccountNavRecorderService.record()`
- `PortfolioService.init_nav_history()` -> `NavInitializationService.init_nav_history()`
- `PortfolioService.get_nav()` -> `NavReadService.get_nav()`
- `PortfolioService.get_holdings()` -> `PortfolioReadService.get_holdings()`
- `PortfolioService.get_cash()` -> `CashService.get_cash()`
- `PortfolioService.get_distribution()` -> `PortfolioReadService.get_distribution()`
- `PortfolioService.full_report()` -> `ReportQueryService.full_report()`
- `PortfolioService.generate_report()` -> `ReportGenerationService.generate_report()`
- `PortfolioService.daily_report_bundle()` -> `DailyAccountNavService.run()`
- `PortfolioService.daily_nav_job()` -> `DailyNavJobService.run()`

## Client Rules

- `scripts/pm.py` should prefer the local service and fall back to
  `PortfolioService` directly.
- `scripts/publish_daily_report.py` should prefer
  `PortfolioServiceClient.daily_report_bundle()` and fall back to the
  application service only for local recovery.
- New product behavior should enter `PortfolioService` and then be exposed by
  HTTP/CLI. Do not add service behavior to `skill_api.py`.
