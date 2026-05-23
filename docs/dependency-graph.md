# Dependency Graph

This graph describes the current high-level dependency direction for the CLI
product shape. Edges point from caller or adapter to the component it depends
on.

```mermaid
flowchart LR
    %% Dependency direction: caller/adapter --> dependency.

    subgraph Users["Operators / Automation"]
        Human["Human"]
        Scheduler["Scheduler"]
        Agent["Agent / MCP client"]
    end

    subgraph Entrypoints["Product Entrypoints"]
        PM["./pm<br/>repo wrapper"]
        CLI["scripts/pm.py<br/>CLI product surface"]
        Publisher["scripts/publish_daily_report.py<br/>daily NAV + HTML publisher"]
        MCP["mcp_server.py<br/>MCP adapter"]
        ServiceRunner["scripts/service.py<br/>local service daemon"]
    end

    subgraph Service["Local Service Boundary"]
        ServiceClient["src/service/client.py<br/>HTTP client"]
        HTTP["src/service/http.py<br/>FastAPI routes"]
        AppFacade["src/service/application.py<br/>PortfolioService facade"]
    end

    subgraph Compatibility["Compatibility Facade"]
        SkillAPI["skill_api.py<br/>PortfolioSkill facade"]
    end

    subgraph Application["Application Layer"]
        PMgr["src/portfolio.py<br/>PortfolioManager compatibility facade"]
        AppServices["src/app/*<br/>trade / cash / valuation / NAV / snapshot / reporting"]
    end

    subgraph Domain["Domain Layer"]
        DomainServices["src/domain/*<br/>NAV calculator / history index / payload normalizer"]
        Models["src/models.py<br/>src/snapshot_models.py"]
        Guards["src/write_guard.py<br/>write validation"]
        Utils["asset / reporting / time utilities"]
    end

    subgraph Pricing["Pricing Layer"]
        PriceFetcher["src/price_fetcher.py<br/>legacy pricing facade"]
        PriceService["src/pricing/service.py<br/>structured quote entry"]
        BatchPlanner["src/pricing/batch.py<br/>optimized batch planner"]
        PriceResults["src/pricing/result.py<br/>quote / failure / batch result"]
        PriceCachePolicy["src/pricing/cache.py<br/>TTL + stale fallback"]
        FixedQuotes["src/pricing/fixed.py<br/>cash / MMF fixed quotes"]
        FxService["src/pricing/fx.py<br/>exchange-rate service"]
        Providers["src/pricing/providers/*<br/>single + batch quote providers"]
    end

    subgraph Storage["Storage Layer"]
        StorageFactory["src/storage.py<br/>storage factory"]
        FeishuStorage["src/feishu_storage.py<br/>FeishuStorage"]
        FeishuMixins["src/feishu/*<br/>table-specific mixins"]
        FeishuClient["src/feishu_client.py<br/>token / paging / retry"]
        LocalCache["src/local_cache.py<br/>.data caches"]
        Migrations["src/migrations/*"]
    end

    subgraph Config["Config"]
        ConfigModule["src/config.py<br/>env + config.json"]
    end

    subgraph External["External Systems"]
        Feishu["Feishu Bitable<br/>holdings / tx / cash_flow / nav_history / snapshots"]
        QuoteAPIs["Quote APIs<br/>Tencent / Finnhub / Yahoo Chart / East Money / FX"]
        StaticReports["reports/ + publish root"]
    end

    Human --> PM
    Human --> CLI
    Scheduler --> Publisher
    Agent --> MCP
    PM --> CLI

    CLI --> ServiceClient
    CLI --> SkillAPI
    MCP --> ServiceClient
    MCP --> SkillAPI
    Publisher --> ServiceClient
    Publisher -.fallback.-> SkillAPI
    Publisher --> StaticReports
    ServiceRunner --> HTTP

    ServiceClient --> HTTP
    HTTP --> AppFacade
    AppFacade --> StorageFactory
    AppFacade --> PMgr
    AppFacade --> AppServices
    AppFacade --> ConfigModule

    SkillAPI --> ConfigModule
    SkillAPI --> StorageFactory
    SkillAPI --> PMgr
    SkillAPI --> PriceFetcher
    SkillAPI --> AppServices
    SkillAPI --> Models
    SkillAPI --> Utils

    PMgr --> AppServices
    PMgr --> DomainServices
    PMgr --> Models
    PMgr --> PriceFetcher
    PMgr --> ConfigModule

    AppServices --> Models
    AppServices --> DomainServices
    AppServices --> PriceFetcher
    AppServices --> Utils
    AppServices --> ConfigModule
    AppServices --> StorageFactory

    DomainServices --> Models
    DomainServices --> Utils
    Guards --> Models
    Guards --> Utils

    PriceFetcher --> PriceService
    PriceFetcher --> BatchPlanner
    PriceFetcher --> Providers
    PriceFetcher --> FxService
    PriceFetcher --> Utils
    BatchPlanner --> PriceCachePolicy
    BatchPlanner --> FixedQuotes
    BatchPlanner --> Providers
    PriceService --> BatchPlanner
    PriceService --> PriceResults
    PriceService --> PriceCachePolicy
    PriceService --> FixedQuotes
    PriceService --> Providers
    PriceService --> Utils
    PriceCachePolicy --> FeishuStorage
    Providers --> ConfigModule
    Providers --> QuoteAPIs
    FxService --> LocalCache
    FxService --> QuoteAPIs

    StorageFactory --> ConfigModule
    StorageFactory --> FeishuStorage
    FeishuStorage --> Models
    FeishuStorage --> FeishuMixins
    FeishuStorage --> FeishuClient
    FeishuStorage --> LocalCache
    FeishuMixins --> FeishuClient
    FeishuClient --> ConfigModule
    FeishuClient --> Feishu
    Migrations --> FeishuClient
    Migrations --> ConfigModule

    LocalCache -.runtime files.-> StaticReports
```

## Reading Notes

- `./pm` is the human CLI wrapper; `scripts/pm.py` is the actual command
  implementation.
- CLI and MCP paths prefer the local service client and keep `skill_api.py` as
  a fallback/compatibility surface.
- The daily publisher prefers `PortfolioServiceClient.daily_report_bundle()` and
  keeps direct `skill_api.py` only as an unavailable-service fallback.
- `src/service/application.py` owns `list_accounts`, `multi_account_overview`,
  `record_nav`, `get_nav`, `get_holdings`, `get_cash`, `get_distribution`,
  `full_report`, `generate_report`, and `daily_report_bundle` through direct
  `src/app` / `PortfolioManager` paths. `skill_api.py` remains a caller-facing
  compatibility surface and should not own new service behavior.
- Business logic should keep moving downward into `src/app`, `src/domain`,
  `src/pricing`, and storage-specific modules rather than growing the facades.
