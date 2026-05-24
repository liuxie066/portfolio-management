# CLI Service NAV Boundary

- `pm nav record` and `pm daily` should prefer the local HTTP service, not bypass it through direct `skill_api.py`, while preserving direct fallback for unavailable service.
- NAV recording is exposed through `PortfolioService.record_nav()`, `PortfolioServiceClient.record_nav()`, and `POST /nav/record`.
- The service API keeps the existing safety contract: dry-run by default; real writes require `dry_run=false` and `confirm=true`.
- `pm daily` records NAV and reads distribution through the same service client when the service is available.
- `PortfolioService.record_nav()` now directly builds the valuation snapshot with `PortfolioReadService` and writes through `PortfolioManager.record_nav()` instead of delegating to `skill_api.record_nav`.
- `PortfolioService.get_distribution()` now directly uses `PortfolioReadService.get_distribution()` instead of delegating to `skill_api.get_distribution`.
- Dependency and architecture graphs now show direct service dependencies for migrated methods, with `skill_api.py` retained as a caller-facing compatibility adapter rather than a service implementation dependency.
- `PortfolioService.get_nav()` now directly reads `nav_history` through `NavReadService` instead of delegating to `skill_api.get_nav`.
- `PortfolioService.get_holdings()` now directly uses `PortfolioReadService.get_holdings()` instead of delegating to `skill_api.get_holdings`.
- `PortfolioService.get_cash()` now directly uses `CashService.get_cash()` instead of delegating to `skill_api.get_cash`.
- `PortfolioService.list_accounts()` and `multi_account_overview()` now use `AccountService`; multi-account overview calls the direct service `full_report` path per account.
- `PortfolioService.full_report()` now directly uses `FullReportService.full_report()`; `PortfolioSkill.full_report()` is kept as a compatibility wrapper over the same application service.
- `PortfolioService.generate_report()` now directly uses `ReportGenerationService.generate_report()`; `PortfolioSkill.generate_report()` is kept as a compatibility wrapper over the same application service.
- The daily publisher now prefers `PortfolioService.daily_report_bundle()` through the local service client; direct `skill_api` execution remains only as an unavailable-service compatibility path.
- `daily_report_bundle` records NAV, assembles the daily report payload, and returns recent NAV page fields from one priced snapshot so the published HTML and stored NAV are consistent.
- `record_nav` and the official daily publisher now carry a `run_id`; callers may supply one, otherwise service/publisher code generates it and propagates it into snapshot, NAV result, report payload, HTML trace text, publish output, and persisted NAV `details`.
- `scripts/publish_daily_report.py` now suppresses noisy internal stdout only while building the bundle; the final success JSON is emitted by default and `--quiet` is the explicit no-output mode.
- Service-first validation treats business/data configuration failures as structured `success=false` payloads, not HTTP 500s or opaque client errors.
