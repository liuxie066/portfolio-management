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
        CashFlow["cash_flow_effect_service.py"]
        CashFlowReceipt["cash_flow_effect_receipt_service.py"]
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
        CashFlowStore["cash_flow_effects.sqlite3<br/>technical workflow state"]
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
    CLI --> CashFlow

    DailyJob --> AccountNav
    DailyJob -.Futu CASH observation / MMF.-> FutuSync
    AccountNav --> Read
    AccountNav --> CashFlow
    AccountNav --> FeishuStorage
    FutuSync --> CashFlow
    CashFlow --> FeishuStorage
    CashFlow --> CashFlowStore
    CashFlow --> CashFlowReceipt
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
    CashFlowReceipt --> FeishuClient
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
6. Scan Cash Flow effects and block every unresolved affected account.
7. Build one priced valuation snapshot per account.
8. Record NAV and then persist `holdings_snapshot`.
9. Return per-account status and summary.
10. `PortfolioService` persists one consolidated NAV receipt before immediate
    delivery; the independent dispatcher retries failed delivery.

Production Futu accounts run `pm futu sync` as an independent step before
`daily-job`. This observes per-currency CASH and updates MMF, STOCK/ETF
quantity, and `average_cost` even when NAV recording later skips an existing
date. The embedded daily-job option has the same observe-only CASH semantics.

## Report Boundaries

- `FutuBalanceSyncService` owns broker CASH observation and non-CASH holdings
  synchronization; it never directly writes CASH.
- `PortfolioService` serializes each account's complete full Futu sync from
  broker observation through evidence persistence. Its OM refresh-request path
  is non-durable and silent; 202 is acceptance, not synchronization proof.
- `CashFlowEffectService` owns durable CASH discovery, preview/confirmation,
  compensation, and the NAV gate.
- `FutuSyncReceiptService` owns the best-effort Feishu receipt after a real Futu write; delivery failure is reported separately from sync success.
- `NavHistoryReceiptService` renders and sends the one-message multi-account NAV receipt; `NavReceiptOutboxService` persists it first and retries delivery independently from NAV success.
- `scripts/portfolio_scheduled_job.sh` owns production ordering: lx/sy Futu sync first, then the morning multi-account NAV job.
- `AccountNavRecorderService` owns snapshot build, NAV write, and holdings
  snapshot persistence; its embedded Futu path observes CASH and syncs MMF.
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

- `transactions` (legacy read-only archive; no active product writer)
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
