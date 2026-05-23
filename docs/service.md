# Service API

`src/service/http.py` is the service-first HTTP boundary. It uses FastAPI and
delegates to `src/service/application.py`.

Core product paths should live behind this service boundary first. Account
discovery, multi-account overview, NAV recording, NAV history reads, holdings
reads, cash reads, position distribution, full report reads, and daily/monthly/
yearly report payloads now execute directly through `src/service/application.py`
plus `src/app/*`/`src/portfolio.py`. The daily report publisher also uses a
service bundle endpoint so NAV recording, report payload assembly, and page
return fields share the same priced snapshot. `skill_api.py` remains a
compatibility adapter, not a service implementation dependency.

## Run

```bash
python scripts/service.py start
python scripts/service.py status
python scripts/service.py stop
```

Default URL: `http://127.0.0.1:8765`.

The service is unauthenticated and binds to loopback hosts only by default.
Binding to `0.0.0.0` or any other non-loopback address requires the explicit
`--allow-remote` flag and should only be used behind an authenticated network
boundary.

Overrides:

- `PORTFOLIO_SERVICE_HOST`
- `PORTFOLIO_SERVICE_PORT`
- `PORTFOLIO_SERVICE_URL`

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

Legacy `/accounts/{account}/...` routes remain available for compatibility, but
new clients should pass account as a query parameter so account names do not
need to be embedded in the URL path.

## Write Endpoints

- `POST /nav/record`
- `POST /accounts/{account}/nav/record`
- `POST /report/daily-bundle`
- `POST /accounts/{account}/report/daily-bundle`

NAV record body:

```json
{
  "account": "alice",
  "price_timeout": 30,
  "dry_run": true,
  "confirm": false,
  "overwrite_existing": true,
  "use_bulk_persist": false,
  "run_id": "optional-operator-run-id"
}
```

NAV writes are dry-run by default. A real write still requires
`dry_run=false` and `confirm=true`; the CLI exposes this as
`./pm nav record --write --confirm` and `./pm daily --write --confirm`.

Daily report bundle body:

```json
{
  "account": "alice",
  "price_timeout": 30,
  "dry_run": true,
  "confirm": false,
  "sync_futu_cash_mmf": false,
  "use_bulk_persist": false,
  "run_id": "optional-operator-run-id"
}
```

`daily_report_bundle` is dry-run by default and requires `dry_run=false` plus
`confirm=true` for real NAV writes. It exists as one service call instead of a
client-side chain of `/nav/record`, `/report/daily`, and `/nav`, because the
publisher must reuse one priced valuation snapshot for consistency and runtime.
If `run_id` is omitted, the service generates one and returns it in the top-level
response, `nav_result`, report payload, snapshot, and persisted NAV `details`.

## Migration Rule

New product behavior should enter through `src/service/application.py` and the
HTTP route layer first. `skill_api.py`, `mcp_server.py`, and `scripts/pm.py`
remain compatibility adapters while heavy business logic is moved out of the
Skill facade over time.

Current direct service paths:

- `PortfolioService.list_accounts()` -> `AccountService.list_accounts()`
- `PortfolioService.multi_account_overview()` -> `AccountService.multi_account_overview()`
- `PortfolioService.record_nav()` -> `PortfolioReadService.build_snapshot()` -> `PortfolioManager.record_nav()`
- `PortfolioService.get_nav()` -> `NavReadService.get_nav()`
- `PortfolioService.get_holdings()` -> `PortfolioReadService.get_holdings()`
- `PortfolioService.get_cash()` -> `CashService.get_cash()`
- `PortfolioService.get_distribution()` -> `PortfolioReadService.get_distribution()`
- `PortfolioService.full_report()` -> `FullReportService.full_report()`
- `PortfolioService.generate_report()` -> `ReportGenerationService.generate_report()`
- `PortfolioService.daily_report_bundle()` -> one snapshot reused by NAV write, report payload, and recent NAV read

`scripts/pm.py` common commands prefer the local HTTP service and silently fall
back to `skill_api.py` when the service is unavailable. Use `--no-service` in
the CLI for explicit direct mode, or `--require-service` to fail instead of
falling back when the service cannot be reached.
`scripts/publish_daily_report.py` follows the same service-first rule through
`PortfolioServiceClient.daily_report_bundle()`; direct mode remains available
for local recovery and compatibility.
