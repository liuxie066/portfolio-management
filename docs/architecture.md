# Architecture

The project is now a CLI + local-service portfolio product. The service and app
layers own product behavior; `skill_api.py` remains a compatibility adapter for older Python callers.

## Current Shape

```mermaid
flowchart TB
    subgraph Operators["Operators / Automation"]
        Human["Human"]
        Scheduler["systemd / cron"]
        Agent["legacy Skill caller"]
    end

    subgraph Entry["Entrypoints"]
        CLI["./pm / scripts/pm.py<br/>CLI product surface"]
        ServiceRunner["scripts/service.py<br/>local service process"]
        Publisher["scripts/publish_daily_report.py<br/>daily report publisher"]
        SkillAPI["skill_api.py<br/>compatibility Python API"]
    end

    subgraph Service["Service Boundary"]
        ServiceClient["src/service/client.py"]
        HTTP["src/service/http.py"]
        AppFacade["src/service/application.py<br/>PortfolioService"]
    end

    subgraph App["Application Layer"]
        Account["account_service.py"]
        Read["portfolio_read_service.py"]
        DailyJob["daily_nav_job_service.py"]
        AccountNav["account_nav_recorder_service.py"]
        InitNav["nav_initialization_service.py"]
        ReportPayload["daily_report_payload_service.py"]
        ReportQuery["report_query_service.py"]
        FutuSync["futu_balance_sync_service.py"]
        FutuReceipt["futu_sync_receipt_service.py"]
    end

    subgraph Domain["Domain Layer"]
        NavCalc["nav_calculator.py"]
        Performance["domain/nav/performance.py"]
        HoldingsProjection["domain/report/holdings_projection.py"]
        Payload["payload_normalizer.py"]
        Models["models.py / snapshot_models.py"]
    end

    subgraph Pricing["Pricing"]
        PriceFetcher["price_fetcher.py<br/>compat facade"]
        PriceService["pricing/service.py"]
        Providers["pricing/providers/*"]
        Fx["pricing/fx.py"]
        CachePolicy["pricing/cache.py"]
    end

    subgraph Storage["Storage"]
        FeishuStorage["feishu_storage.py"]
        Repos["feishu/repositories/*"]
        FeishuClient["feishu_client.py"]
        LocalCache["local_cache.py / .data"]
    end

    subgraph External["External"]
        Feishu["Feishu Bitable"]
        Quotes["Tencent / Finnhub / Sina US / East Money / FX"]
        Reports["reports/ + publish_root"]
    end

    Human --> CLI
    Scheduler --> CLI
    Scheduler --> Publisher
    Agent --> SkillAPI

    CLI --> ServiceClient
    CLI -.direct fallback.-> AppFacade
    Publisher --> ServiceClient
    Publisher -.direct fallback.-> AppFacade
    ServiceRunner --> HTTP
    ServiceClient --> HTTP
    HTTP --> AppFacade
    SkillAPI --> AppFacade

    AppFacade --> Account
    AppFacade --> Read
    AppFacade --> DailyJob
    AppFacade --> AccountNav
    AppFacade --> InitNav
    AppFacade --> ReportPayload
    AppFacade --> ReportQuery
    AppFacade --> FutuSync
    AppFacade --> FutuReceipt
    AppFacade --> FeishuStorage

    DailyJob --> AccountNav
    DailyJob -.legacy cash/MMF option.-> FutuSync
    AccountNav --> Read
    AccountNav --> FeishuStorage
    ReportPayload --> ReportQuery
    ReportQuery --> Performance
    ReportQuery --> HoldingsProjection
    App --> Domain

    Read --> PriceFetcher
    PriceFetcher --> PriceService
    PriceService --> Providers
    PriceService --> CachePolicy
    PriceService --> Fx
    Providers --> Quotes
    Fx --> Quotes

    FeishuStorage --> Repos
    FutuReceipt --> FeishuClient
    Repos --> FeishuClient
    Repos --> LocalCache
    FeishuClient --> Feishu
    Publisher --> Reports
```

## Ownership Rules

- New product behavior enters through `src/service/application.py`.
- Multi-step workflows live in `src/app/*`.
- Pure calculations live in `src/domain/*`.
- Quote-source code lives in `src/pricing/*`.
- Feishu table-specific read/write code lives in `src/feishu/repositories/*`.
- `skill_api.py` must stay a thin compatibility adapter.
- `PortfolioManager` and `PriceFetcher` are compatibility facades, not places
  for new orchestration.

## Core Daily NAV Workflow

`DailyNavJobService` is the canonical scheduled workflow.

1. Resolve NAV date. If omitted, use the most recent business day before the
   run date.
2. Skip NAV dates that are weekends or configured `calendar.holidays`.
3. Resolve target accounts from CLI input or current holdings.
4. Audit duplicate `nav_history` account/date rows and block writes if found.
5. Reconcile-check manual `cash_flow` rows and block writes if generated fields
   are pending.
6. Build one priced valuation snapshot per account.
7. Record NAV and then persist `holdings_snapshot`.
8. Return per-account status and summary.
9. `PortfolioService` sends one best-effort consolidated NAV receipt for a real job.

Production Futu accounts run `pm futu sync` as an independent step before
`daily-job`. This updates cash/MMF, STOCK/ETF quantity, and `average_cost` even
when `daily-job` later skips an already-recorded NAV date. The embedded
cash/MMF option remains only for compatibility.

## Report Boundaries

- `FutuBalanceSyncService` independently owns broker holdings synchronization.
- `FutuSyncReceiptService` owns the best-effort Feishu receipt after a real Futu write; delivery failure is reported separately from sync success.
- `NavHistoryReceiptService` owns the one-message multi-account NAV receipt after a real `daily-job`; delivery failure is reported separately from NAV success.
- `scripts/portfolio_scheduled_job.sh` owns production ordering: lx/sy Futu sync first, then the morning multi-account NAV job.
- `AccountNavRecorderService` owns snapshot build, NAV write, and holdings snapshot persistence; its embedded cash/MMF sync remains a compatibility path.
- `DailyReportPayloadService` consumes the already-built snapshot and NAV fact.
  It does not fetch prices or write NAV.
- `ReportQueryService` owns read-only full-report queries. Synthetic NAV preview
  exists only here through `NavPreviewService`.
- `scripts/publish_daily_report.py` is the only daily HTML publisher.

The old public daily-report domain is invalid. Publishing creates local static
artifacts only and returns `public_url=null` with
`public_url_status=disabled`.

## Storage Boundaries

Feishu Bitable is the production source of truth. Core tables:

- `holdings`
- `cash_flow`
- `nav_history`
- `holdings_snapshot`

Optional capability tables:

- `transactions`
- `compensation_tasks`
- `schema_version`

Table-level logic belongs in repositories under `src/feishu/repositories/*`.
The mixins under `src/feishu/*` are thin `FeishuStorage` method facades.

## Current Risks

- Feishu is the only production backend; there is no full offline write mode.
- Schema changes are still managed by docs and checks, not automatic migration.
- Some historical Python API tests still instantiate `PortfolioSkill`; keep that
  path covered but do not grow it.
- Cross-table writes are not database-transactional; compensation and audit
  surfaces remain important.

## Next Architecture Priorities

1. Keep shrinking compatibility behavior in `skill_api.py`.
2. Add stronger schema version checks for Feishu tables.
3. Improve structured run logs for scheduled daily NAV jobs.
4. Add a local read-only backup/export path for recent holdings, NAV, and report
   bundles.
