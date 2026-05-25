# Dependency Graph

Edges point from caller or adapter to the component it depends on.

```mermaid
flowchart LR
    subgraph Entrypoints["Entrypoints"]
        PM["./pm"]
        CLI["scripts/pm.py"]
        ServiceRunner["scripts/service.py"]
        Publisher["scripts/publish_daily_report.py"]
        MCP["mcp_server.py"]
        SkillAPI["skill_api.py<br/>compat adapter"]
        RepairCLI["scripts/nav_history_repair.py"]
    end

    subgraph Service["Service Boundary"]
        ServiceClient["src/service/client.py"]
        HTTP["src/service/http.py"]
        PortfolioService["src/service/application.py"]
    end

    subgraph App["Application"]
        AppServices["src/app/*"]
    end

    subgraph Domain["Domain"]
        DomainServices["src/domain/*"]
        Models["src/models.py<br/>src/snapshot_models.py"]
        Guards["src/write_guard.py"]
        Utils["asset / time / reporting utils"]
    end

    subgraph Pricing["Pricing"]
        PriceFetcher["src/price_fetcher.py"]
        PriceService["src/pricing/service.py"]
        PriceProviders["src/pricing/providers/*"]
        PriceCache["src/pricing/cache.py"]
        Fx["src/pricing/fx.py"]
    end

    subgraph Storage["Storage"]
        StorageFactory["src/storage.py"]
        FeishuStorage["src/feishu_storage.py"]
        FeishuRepos["src/feishu/repositories/*"]
        FeishuClient["src/feishu_client.py"]
        LocalCache["src/local_cache.py"]
        Migrations["src/migrations/*"]
    end

    subgraph External["External"]
        Feishu["Feishu Bitable"]
        Quotes["Quote APIs"]
        Reports["reports / publish_root"]
    end

    PM --> CLI
    CLI --> ServiceClient
    CLI -.direct fallback.-> PortfolioService
    Publisher --> ServiceClient
    Publisher -.direct fallback.-> PortfolioService
    ServiceRunner --> HTTP
    ServiceClient --> HTTP
    HTTP --> PortfolioService
    MCP --> SkillAPI
    SkillAPI --> PortfolioService
    RepairCLI --> StorageFactory

    PortfolioService --> AppServices
    PortfolioService --> StorageFactory
    AppServices --> DomainServices
    AppServices --> Models
    AppServices --> PriceFetcher
    AppServices --> FeishuStorage

    DomainServices --> Models
    DomainServices --> Utils
    Guards --> Models

    PriceFetcher --> PriceService
    PriceService --> PriceProviders
    PriceService --> PriceCache
    PriceService --> Fx
    PriceProviders --> Quotes
    Fx --> Quotes

    StorageFactory --> FeishuStorage
    FeishuStorage --> FeishuRepos
    FeishuRepos --> FeishuClient
    FeishuRepos --> LocalCache
    FeishuClient --> Feishu
    Migrations --> FeishuClient
    Publisher --> Reports
```

## Important Directions

- CLI uses `src/service/client.py` first and falls back directly to
  `PortfolioService`; it no longer falls back through `skill_api.py`.
- `scripts/publish_daily_report.py` also follows service-first behavior and
  falls back to `PortfolioService` for local recovery.
- MCP still goes through `skill_api.py` because that is the compatibility API
  surface.
- `skill_api.py` delegates inward to `PortfolioService` / app services and
  should not own new behavior.
- Feishu table logic belongs in `src/feishu/repositories/*`; mixins are only
  `FeishuStorage` facade methods.
- Read-only full report behavior belongs to `ReportQueryService`.
- Scheduled daily NAV behavior belongs to `DailyNavJobService`.

## Removed Or Legacy Paths

- The old full-report alias layer has been removed.
- `save_nav()`, `upsert_nav_bulk()`, and `update_nav_fields()` are removed.
- Public daily-report URL publishing is disabled; outputs are local artifacts
  with `public_url=null` and `public_url_status=disabled`.
- JSON config is no longer the normal configuration path. Use `config.yaml`;
  legacy JSON is only a direct migration input when explicitly referenced.
